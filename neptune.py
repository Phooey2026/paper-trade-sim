#!/usr/bin/env python3
"""
neptune.py — Neptune Paper Trading Agent
Reads pending orders, evaluates each against config thresholds,
calls local LLM (via Hermes) for approval/rejection rationale,
and executes approved trades against neptune_holdings.json.

Usage:
    python3 neptune.py --orders data/neptune_orders_20260617_1400.json
    python3 neptune.py --orders data/neptune_orders_20260617_1400.json --dry-run
    python3 neptune.py --orders data/neptune_orders_20260617_1400.json --verbose

Hermes profile: neptune
  Jetson  → llama.cpp  @ http://localhost:8081/v1  (gemma4:e2b Q4_K_M)
  Mac Mini → Ollama    @ http://localhost:11434/v1  (gemma4:12b)
"""

import json
import os
import sys
import argparse
import subprocess
import re
from datetime import datetime
from copy import deepcopy

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HOLDINGS_FILE  = os.path.join(SCRIPT_DIR, "neptune_holdings.json")
CONFIG_FILE    = os.path.join(SCRIPT_DIR, "neptune_config.json")
DATA_DIR       = os.path.join(SCRIPT_DIR, "data")
REPORTS_DIR    = os.path.join(SCRIPT_DIR, "reports")


# ── File I/O ──────────────────────────────────────────────────────────────

def load_json(path, label="file"):
    if not os.path.exists(path):
        print(f"❌ {label} not found: {path}")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ── Pre-flight checks (pure Python, no LLM) ───────────────────────────────

class PreflightResult:
    def __init__(self):
        self.hard_reject = False
        self.reject_reason = ""
        self.warnings = []
        self.metrics = {}

    def reject(self, reason):
        self.hard_reject = True
        self.reject_reason = reason

    def warn(self, msg):
        self.warnings.append(msg)


def run_preflight(order, holdings, config):
    """
    Run all hard-limit checks. Returns PreflightResult.
    Hard rejects bypass the LLM entirely.
    """
    result = PreflightResult()
    positions = holdings.get("positions", {})
    summary   = holdings.get("summary", {})
    ticker    = order.get("ticker", "").upper()
    action    = order.get("action", "").upper()
    dollars   = float(order.get("dollar_amount", 0))

    total_portfolio = summary.get("total_portfolio_value", 0)
    cash_value      = summary.get("cash_value", 0)
    limits          = config.get("order_limits", {})
    pos_limits      = config.get("position_limits", {})
    cash_limits     = config.get("cash_limits", {})
    restricted      = config.get("restricted_tickers", {}).get("tickers", [])
    mercury_allowed = config.get("mercury_allowed_tickers", ["BITQ", "VDE", "IAU"])
    soft            = config.get("soft_criteria", {})

    # ── Hard limit: restricted ticker ─────────────────────────────────────
    if ticker in restricted:
        reason = config.get("restricted_tickers", {}).get("reason", "Restricted ticker")
        result.reject(f"RESTRICTED_TICKER: {ticker} — {reason}")
        return result

    # ── Hard limit: Mercury can only trade its ETF proxies ─────────────────
    if order.get("agent") == "mercury" and ticker not in mercury_allowed:
        result.reject(f"MERCURY_UNAUTHORIZED_TICKER: Mercury may only trade {mercury_allowed}")
        return result

    # ── Hard limit: order size ─────────────────────────────────────────────
    min_order = limits.get("min_order_dollars", 5000)
    max_order = limits.get("max_order_dollars", 50000)
    if dollars < min_order:
        result.reject(f"ORDER_TOO_SMALL: ${dollars:,.0f} is below minimum ${min_order:,.0f}")
        return result
    if dollars > max_order:
        result.reject(f"ORDER_TOO_LARGE: ${dollars:,.0f} exceeds maximum ${max_order:,.0f}")
        return result

    # ── Hard limit: selling what we don't own ─────────────────────────────
    if action == "SELL" and ticker not in positions:
        result.reject(f"NO_POSITION: Cannot sell {ticker} — not currently held")
        return result

    # ── Hard limit: cash floor (BUY orders) ───────────────────────────────
    if action == "BUY":
        cash_floor_pct = cash_limits.get("min_cash_floor_pct", 5.0)
        min_cash = total_portfolio * (cash_floor_pct / 100)
        cash_after = cash_value - dollars
        if cash_after < min_cash:
            result.reject(
                f"CASH_FLOOR_BREACH: Buy of ${dollars:,.0f} would leave "
                f"${cash_after:,.2f} cash — below {cash_floor_pct}% floor "
                f"(${min_cash:,.2f})"
            )
            return result

    # ── Hard limit: position concentration (BUY) ──────────────────────────
    if action == "BUY":
        max_pos_pct = pos_limits.get("max_single_position_pct", 10.0)
        current_pos_value = positions.get(ticker, {}).get("market_value", 0)
        projected_value   = current_pos_value + dollars
        projected_pct     = (projected_value / total_portfolio) * 100 if total_portfolio else 0
        result.metrics["projected_position_pct"] = round(projected_pct, 2)
        if projected_pct > max_pos_pct:
            result.reject(
                f"CONCENTRATION_LIMIT: {ticker} would reach {projected_pct:.1f}% "
                f"of portfolio — exceeds {max_pos_pct}% max"
            )
            return result

    # ── Soft warning: BUY on AVOID verdict ────────────────────────────────
    if action == "BUY" and order.get("verdict", "").upper() == "AVOID":
        if soft.get("reject_buy_on_avoid_verdict", True):
            result.reject(f"VERDICT_CONFLICT: BUY order on {ticker} which is AVOID-rated")
            return result
        else:
            result.warn(f"BUY on AVOID-rated ticker {ticker} — requires strong rationale")

    # ── Soft warning: SELL on ACCUMULATE verdict ───────────────────────────
    if action == "SELL" and order.get("verdict", "").upper() == "ACCUMULATE":
        result.warn(f"SELL on ACCUMULATE-rated ticker {ticker} — unusual, review rationale")

    # ── Soft warning: large order relative to position ─────────────────────
    if action == "SELL" and ticker in positions:
        pos_value = positions[ticker].get("market_value", 0)
        if pos_value > 0 and dollars > pos_value * 0.5:
            result.warn(
                f"Large SELL: ${dollars:,.0f} is >50% of current {ticker} "
                f"position (${pos_value:,.0f})"
            )

    return result


# ── LLM call via Hermes ───────────────────────────────────────────────────

def build_neptune_prompt(order, preflight, holdings, config):
    """Build the prompt Neptune (the LLM) will reason about."""
    positions  = holdings.get("positions", {})
    summary    = holdings.get("summary", {})
    ticker     = order.get("ticker", "").upper()
    action     = order.get("action", "").upper()
    dollars    = float(order.get("dollar_amount", 0))
    agent      = order.get("agent", "unknown")
    rationale  = order.get("rationale", "No rationale provided.")

    current_pos = positions.get(ticker, {})
    pos_value   = current_pos.get("market_value", 0)
    pos_shares  = current_pos.get("shares", 0)
    pos_pct     = round((pos_value / summary.get("total_portfolio_value", 1)) * 100, 2)

    cash_value      = summary.get("cash_value", 0)
    total_portfolio = summary.get("total_portfolio_value", 0)
    cash_pct        = summary.get("cash_pct", 0)

    limits      = config.get("order_limits", {})
    pos_limits  = config.get("position_limits", {})
    cash_limits = config.get("cash_limits", {})
    soft        = config.get("soft_criteria", {})

    min_order      = limits.get("min_order_dollars", 5000)
    max_order      = limits.get("max_order_dollars", 50000)
    max_pos_pct    = pos_limits.get("max_single_position_pct", 10.0)
    cash_floor_pct = cash_limits.get("min_cash_floor_pct", 5.0)

    warnings_text = "\n".join(f"  ⚠️  {w}" for w in preflight.warnings) if preflight.warnings else "  None"
    proj_pct = preflight.metrics.get("projected_position_pct", "N/A")

    prompt = f"""You are Neptune, the paper trading risk manager for Obsidian Capital (account OC-CLIENT-001).

Your job: evaluate this incoming order and decide to APPROVE or REJECT it.
You have already passed all automated hard-limit checks. Your role is to apply
judgment on the qualitative factors and soft criteria.

═══════════════════════════════════════════════════════
ORDER DETAILS
═══════════════════════════════════════════════════════
Agent       : {agent.upper()} ({'Equity analyst' if agent=='jupiter' else 'CCC analyst'})
Ticker      : {ticker}
Action      : {action}
Dollar Amt  : ${dollars:,.0f}
Agent Rationale: {rationale}

═══════════════════════════════════════════════════════
CURRENT POSITION IN {ticker}
═══════════════════════════════════════════════════════
Shares held    : {pos_shares:,.4f}
Market value   : ${pos_value:,.2f}
Portfolio %    : {pos_pct:.2f}%
Projected % after trade: {proj_pct}%

═══════════════════════════════════════════════════════
PORTFOLIO SNAPSHOT
═══════════════════════════════════════════════════════
Total portfolio : ${total_portfolio:,.2f}
Cash (VMRXX)    : ${cash_value:,.2f} ({cash_pct:.2f}%)
Cash after BUY  : ${cash_value - dollars:,.2f} (if approved)

═══════════════════════════════════════════════════════
PORTFOLIO GUARDRAILS (current live thresholds — cite these exact figures
in your rationale, don't assume or recall figures from elsewhere)
═══════════════════════════════════════════════════════
Order size range     : ${min_order:,.0f} - ${max_order:,.0f}  (this order already passed this check)
Max single position  : {max_pos_pct}% of portfolio
Min cash floor       : {cash_floor_pct}% of portfolio

═══════════════════════════════════════════════════════
SOFT-CRITERIA WARNINGS (from automated checks)
═══════════════════════════════════════════════════════
{warnings_text}

═══════════════════════════════════════════════════════
YOUR EVALUATION CRITERIA
═══════════════════════════════════════════════════════
1. Does the agent's rationale justify the order?
2. Is the dollar amount proportionate to conviction?
3. Do any warnings above change your view?
4. Any other portfolio-level concerns?

═══════════════════════════════════════════════════════
RESPOND IN THIS EXACT FORMAT (no extra text):
═══════════════════════════════════════════════════════
DECISION: APPROVE or REJECT
RATIONALE: [2-4 sentences explaining your decision, referencing specific facts above]
CONFIDENCE: HIGH, MEDIUM, or LOW
"""
    return prompt


def call_neptune_llm(prompt, verbose=False):
    """
    Call Neptune via Hermes subprocess (neptune profile).
    Returns the raw LLM response string.
    """
    cmd = ["neptune", "-z", prompt, "--ignore-rules"]
    if verbose:
        print(f"\n  [LLM] Calling Neptune via Hermes...")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            print(f"  ⚠️  Hermes returned non-zero exit: {result.returncode}")
            if stderr:
                print(f"  stderr: {stderr[:200]}")
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return "DECISION: REJECT\nRATIONALE: Neptune LLM timed out — order held pending manual review.\nCONFIDENCE: LOW"
    except FileNotFoundError:
        print("  ❌ Hermes not found. Is it installed and on PATH?")
        print("     Falling back to auto-APPROVE for testing.")
        return "DECISION: APPROVE\nRATIONALE: Hermes not available — auto-approved for testing. All hard limits passed.\nCONFIDENCE: LOW"


def parse_llm_response(response):
    """Parse Neptune's structured response into decision dict."""
    decision = {
        "decision": "REJECT",  # safe default
        "rationale": response,
        "confidence": "LOW",
        "raw_response": response
    }

    lines = response.upper()

    # Decision
    if "DECISION: APPROVE" in lines:
        decision["decision"] = "APPROVE"
    elif "DECISION: REJECT" in lines:
        decision["decision"] = "REJECT"

    # Rationale (case-preserved)
    rat_match = re.search(r"RATIONALE:\s*(.+?)(?=\nCONFIDENCE:|$)", response,
                          re.DOTALL | re.IGNORECASE)
    if rat_match:
        decision["rationale"] = rat_match.group(1).strip()

    # Confidence
    if "CONFIDENCE: HIGH" in lines:
        decision["confidence"] = "HIGH"
    elif "CONFIDENCE: MEDIUM" in lines:
        decision["confidence"] = "MEDIUM"
    elif "CONFIDENCE: LOW" in lines:
        decision["confidence"] = "LOW"

    return decision


# ── Trade execution ───────────────────────────────────────────────────────

def execute_trade(order, holdings, verbose=False):
    """
    Execute an approved trade:
      BUY  → deduct from VMRXX cash, add/update position
      SELL → add to VMRXX cash, reduce/remove position
    Returns updated holdings and a trade record.
    """
    positions = holdings["positions"]
    summary   = holdings["summary"]
    ticker    = order["ticker"].upper()
    action    = order["action"].upper()
    dollars   = float(order["dollar_amount"])
    run_date  = datetime.now().strftime("%Y-%m-%d")

    # Get current price from holdings (already updated by update_prices.py)
    # For new BUY positions not in holdings, fetch via yfinance as fallback
    if ticker in positions:
        price = positions[ticker]["last_price"]
    else:
        # New position — try order price, then yfinance
        price = order.get("last_price") or order.get("price")
        if not price:
            try:
                import yfinance as yf
                info = yf.Ticker(ticker).fast_info
                price = getattr(info, 'last_price', None) or getattr(info, 'previous_close', None)
                if price:
                    print(f"  📡 Fetched {ticker} price via yfinance: ${float(price):.2f}")
            except Exception:
                pass
        if not price:
            return holdings, None, f"No price available for new position {ticker} — run update_prices.py first"

    price = float(price)
    shares_traded = dollars / price

    trade_record = {
        "trade_id": f"TRD-{ticker}-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "order_id": order.get("order_id", ""),
        "agent": order.get("agent", ""),
        "ticker": ticker,
        "action": action,
        "dollar_amount": dollars,
        "price": price,
        "shares": round(shares_traded, 6),
        "trade_date": run_date,
        "rationale": order.get("rationale", ""),
        "neptune_rationale": order.get("neptune_rationale", "")
    }

    if action == "BUY":
        if ticker not in positions:
            # New position
            positions[ticker] = {
                "company": order.get("company", ticker),
                "ticker": ticker,
                "shares": 0,
                "avg_cost_per_share": price,
                "cost_basis": 0,
                "last_price": price,
                "market_value": 0,
                "gain_loss": 0,
                "gain_loss_pct": 0,
                "asset_type": "equity",
                "price_date": run_date,
                "price_source": "trade_execution"
            }
        pos = positions[ticker]
        old_shares = pos["shares"]
        old_cost   = pos["cost_basis"]
        new_shares = old_shares + shares_traded
        new_cost   = old_cost + dollars
        pos["shares"]            = round(new_shares, 6)
        pos["cost_basis"]        = round(new_cost, 2)
        pos["avg_cost_per_share"]= round(new_cost / new_shares, 4)
        pos["market_value"]      = round(new_shares * price, 2)
        pos["gain_loss"]         = round(pos["market_value"] - pos["cost_basis"], 2)
        pos["gain_loss_pct"]     = round((pos["gain_loss"] / pos["cost_basis"]) * 100, 2)

        summary["cash_value"] = round(summary["cash_value"] - dollars, 2)

    elif action == "SELL":
        if ticker not in positions:
            return holdings, None, f"Cannot sell {ticker}: position not found"
        pos = positions[ticker]
        # Sell by dollar amount — cap at full position value
        sell_value  = min(dollars, pos["market_value"])
        shares_sold = sell_value / price
        # Pro-rata cost basis reduction
        cb_reduction = pos["cost_basis"] * (shares_sold / pos["shares"]) if pos["shares"] else 0

        pos["shares"]     = round(pos["shares"] - shares_sold, 6)
        pos["cost_basis"] = round(pos["cost_basis"] - cb_reduction, 2)
        pos["market_value"] = round(pos["shares"] * price, 2)
        pos["gain_loss"]    = round(pos["market_value"] - pos["cost_basis"], 2)
        pos["gain_loss_pct"] = round(
            (pos["gain_loss"] / pos["cost_basis"]) * 100, 2
        ) if pos["cost_basis"] else 0

        if pos["shares"] < 0.0001:
            del positions[ticker]  # Position closed

        summary["cash_value"] = round(summary["cash_value"] + sell_value, 2)
        trade_record["shares"] = round(shares_sold, 6)
        trade_record["dollar_amount"] = round(sell_value, 2)

    # Recompute portfolio summary
    total_equity = sum(p["market_value"] for p in positions.values()
                       if p.get("asset_type") == "equity")
    total_etf    = sum(p["market_value"] for p in positions.values()
                       if p.get("asset_type") == "etf")
    total_invested = round(total_equity + total_etf, 2)
    total_portfolio = round(total_invested + summary["cash_value"], 2)
    total_cb = round(sum(p["cost_basis"] for p in positions.values()), 2)
    total_gl = round(sum(p["gain_loss"] for p in positions.values()), 2)
    gl_pct   = round((total_gl / total_cb) * 100, 2) if total_cb else 0.0

    summary.update({
        "total_portfolio_value": total_portfolio,
        "total_invested": total_invested,
        "total_equity_value": round(total_equity, 2),
        "total_etf_value": round(total_etf, 2),
        "cash_pct": round((summary["cash_value"] / total_portfolio) * 100, 4),
        "total_cost_basis": total_cb,
        "total_gain_loss": total_gl,
        "total_gain_loss_pct": gl_pct,
    })
    holdings["last_updated"] = run_date

    if verbose:
        print(f"  ✅ Executed {action} {ticker}: {trade_record['shares']:.4f} shares "
              f"@ ${price:.2f} = ${trade_record['dollar_amount']:,.2f}")

    return holdings, trade_record, None


# ── Report generation ─────────────────────────────────────────────────────

def generate_report(results, holdings, ts):
    """Write a standalone HTML report for this Neptune run."""
    approved  = [r for r in results if r["status"] == "APPROVED"]
    rejected  = [r for r in results if r["status"] == "REJECTED"]
    preject   = [r for r in results if r["status"] == "PRE_REJECTED"]
    summary   = holdings.get("summary", {})

    def money(v):
        return f"${v:,.2f}"

    def pct(v):
        sign = "+" if v >= 0 else ""
        return f"{sign}{v:.2f}%"

    rows_approved = ""
    for r in approved:
        rows_approved += f"""
        <tr class="approved">
          <td>{r['order'].get('agent','').upper()}</td>
          <td><strong>{r['order'].get('ticker','')}</strong></td>
          <td>{r['order'].get('action','')}</td>
          <td>{money(r['order'].get('dollar_amount',0))}</td>
          <td>APPROVED ✅</td>
          <td class="rationale">{r.get('neptune_rationale','')}</td>
        </tr>"""

    rows_rejected = ""
    for r in rejected + preject:
        reason = r.get("neptune_rationale") or r.get("reject_reason", "")
        rows_rejected += f"""
        <tr class="rejected">
          <td>{r['order'].get('agent','').upper()}</td>
          <td><strong>{r['order'].get('ticker','')}</strong></td>
          <td>{r['order'].get('action','')}</td>
          <td>{money(r['order'].get('dollar_amount',0))}</td>
          <td>{r['status']} ❌</td>
          <td class="rationale">{reason}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Neptune Trading Report — {ts}</title>
<style>
  body {{ font-family: 'Segoe UI', sans-serif; background: #0d1117; color: #c9d1d9; margin: 0; padding: 20px; }}
  h1 {{ color: #58a6ff; border-bottom: 2px solid #1f6feb; padding-bottom: 8px; }}
  h2 {{ color: #79c0ff; margin-top: 30px; }}
  .summary-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 20px 0; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }}
  .card .label {{ font-size: 11px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.05em; }}
  .card .value {{ font-size: 20px; font-weight: bold; color: #f0f6fc; margin-top: 4px; }}
  .card .value.green {{ color: #3fb950; }}
  .card .value.blue  {{ color: #58a6ff; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 13px; }}
  th {{ background: #161b22; color: #8b949e; text-align: left; padding: 8px 12px;
        border-bottom: 1px solid #30363d; font-size: 11px; text-transform: uppercase; }}
  td {{ padding: 10px 12px; border-bottom: 1px solid #21262d; vertical-align: top; }}
  tr.approved td {{ background: #0d2818; }}
  tr.rejected td {{ background: #2d1317; }}
  .rationale {{ font-size: 12px; color: #8b949e; max-width: 400px; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: bold; }}
  .badge-approved {{ background: #1a4731; color: #3fb950; }}
  .badge-rejected {{ background: #3d1a1a; color: #f85149; }}
  .stats-row {{ display: flex; gap: 20px; margin: 16px 0; }}
  .stat {{ background: #161b22; border: 1px solid #30363d; border-radius: 6px;
           padding: 10px 16px; display: flex; gap: 12px; align-items: center; }}
  .stat .n {{ font-size: 24px; font-weight: bold; }}
  .footer {{ margin-top: 40px; font-size: 11px; color: #484f58; border-top: 1px solid #21262d; padding-top: 12px; }}
</style>
</head>
<body>
<h1>🔱 Neptune — Paper Trading Report</h1>
<p style="color:#8b949e">Account: <strong>OC-CLIENT-001</strong> &nbsp;|&nbsp;
   Run: <strong>{ts}</strong></p>

<h2>Portfolio Summary</h2>
<div class="summary-grid">
  <div class="card">
    <div class="label">Total Portfolio</div>
    <div class="value blue">{money(summary.get('total_portfolio_value',0))}</div>
  </div>
  <div class="card">
    <div class="label">Invested</div>
    <div class="value">{money(summary.get('total_invested',0))}</div>
  </div>
  <div class="card">
    <div class="label">Cash (VMRXX)</div>
    <div class="value">{money(summary.get('cash_value',0))} <span style="font-size:13px;color:#8b949e">({summary.get('cash_pct',0):.1f}%)</span></div>
  </div>
  <div class="card">
    <div class="label">Total Gain/Loss</div>
    <div class="value green">{money(summary.get('total_gain_loss',0))} ({pct(summary.get('total_gain_loss_pct',0))})</div>
  </div>
</div>

<h2>Order Results</h2>
<div class="stats-row">
  <div class="stat"><span class="n" style="color:#3fb950">{len(approved)}</span>
    <span class="badge badge-approved">APPROVED</span></div>
  <div class="stat"><span class="n" style="color:#f85149">{len(rejected)+len(preject)}</span>
    <span class="badge badge-rejected">REJECTED</span></div>
  <div class="stat"><span class="n">{len(results)}</span> <span style="color:#8b949e">TOTAL</span></div>
</div>

{'<h2>✅ Approved Orders</h2><table><tr><th>Agent</th><th>Ticker</th><th>Action</th><th>Amount</th><th>Status</th><th>Neptune Rationale</th></tr>' + rows_approved + '</table>' if approved else '<p style="color:#8b949e">No orders approved this run.</p>'}

{'<h2>❌ Rejected Orders</h2><table><tr><th>Agent</th><th>Ticker</th><th>Action</th><th>Amount</th><th>Status</th><th>Reason</th></tr>' + rows_rejected + '</table>' if (rejected or preject) else ''}

<div class="footer">
  Generated by neptune.py &nbsp;|&nbsp; Obsidian Capital Paper Trading System &nbsp;|&nbsp;
  For internal simulation use only — not real financial advice.
</div>
</body>
</html>"""
    return html


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Neptune Paper Trading Agent")
    parser.add_argument("--orders", required=True,
                        help="Path to neptune_orders_*.json file")
    parser.add_argument("--dry-run", action="store_true",
                        help="Evaluate orders but do not modify holdings")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose output including LLM prompts")
    parser.add_argument("--skip-llm", action="store_true",
                        help="Skip LLM review — auto-approve all hard-limit-passing orders (for testing)")
    args = parser.parse_args()

    os.makedirs(REPORTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")

    print("=" * 60)
    print(" 🔱 Neptune — Paper Trading Agent")
    print(f"    Account : OC-CLIENT-001")
    print(f"    Run     : {ts}")
    if args.dry_run:
        print("    *** DRY RUN — no files will be modified ***")
    print("=" * 60)

    holdings = load_json(HOLDINGS_FILE, "neptune_holdings.json")
    config   = load_json(CONFIG_FILE,   "neptune_config.json")
    orders_file = load_json(args.orders, "orders file")
    orders   = orders_file.get("orders", [])

    print(f"\n📋 Orders loaded: {len(orders)}")
    print(f"   Portfolio: ${holdings['summary']['total_portfolio_value']:,.2f}")
    print(f"   Cash:      ${holdings['summary']['cash_value']:,.2f}")

    # Work on a deep copy for dry-run safety
    working_holdings = deepcopy(holdings) if args.dry_run else holdings

    results     = []
    trade_log   = []

    for i, order in enumerate(orders):
        ticker = order.get("ticker", "?").upper()
        action = order.get("action", "?").upper()
        agent  = order.get("agent", "?").upper()
        dollars = float(order.get("dollar_amount", 0))

        print(f"\n{'─'*60}")
        print(f"  Order {i+1}/{len(orders)}: [{agent}] {action} {ticker} ${dollars:,.0f}")

        # ── Step 1: Pre-flight hard checks ────────────────────────────────
        preflight = run_preflight(order, working_holdings, config)

        if preflight.hard_reject:
            print(f"  ❌ PRE-REJECTED: {preflight.reject_reason}")
            results.append({
                "status": "PRE_REJECTED",
                "order": order,
                "reject_reason": preflight.reject_reason,
                "neptune_rationale": preflight.reject_reason,
                "warnings": preflight.warnings
            })
            continue

        if preflight.warnings:
            for w in preflight.warnings:
                print(f"  ⚠️  {w}")

        # ── Step 2: Neptune LLM review ────────────────────────────────────
        if args.skip_llm:
            decision = {
                "decision": "APPROVE",
                "rationale": "LLM skipped (--skip-llm flag). All hard limits passed.",
                "confidence": "LOW",
                "raw_response": ""
            }
        else:
            print(f"  🤖 Sending to Neptune (LLM) for review...")
            prompt = build_neptune_prompt(order, preflight, working_holdings, config)
            response = call_neptune_llm(prompt, verbose=args.verbose)
            if args.verbose:
                print(f"\n  [LLM Raw Response]\n  {response}\n")
            decision = parse_llm_response(response)

        status = decision["decision"]
        print(f"  {'✅ APPROVED' if status=='APPROVE' else '❌ REJECTED'} "
              f"[{decision['confidence']}] — {decision['rationale'][:80]}...")

        order["neptune_rationale"] = decision["rationale"]
        order["neptune_confidence"] = decision["confidence"]

        if status == "APPROVE":
            if not args.dry_run:
                working_holdings, trade_record, err = execute_trade(
                    order, working_holdings, verbose=args.verbose
                )
                if err:
                    print(f"  ⚠️  Execution error: {err}")
                    status = "EXECUTION_ERROR"
                elif trade_record:
                    trade_record["neptune_rationale"] = decision["rationale"]
                    trade_log.append(trade_record)
            results.append({
                "status": "APPROVED",
                "order": order,
                "neptune_rationale": decision["rationale"],
                "neptune_confidence": decision["confidence"],
                "warnings": preflight.warnings
            })
        else:
            results.append({
                "status": "REJECTED",
                "order": order,
                "neptune_rationale": decision["rationale"],
                "neptune_confidence": decision["confidence"],
                "warnings": preflight.warnings
            })

    # ── Save outputs ──────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    approved_n  = sum(1 for r in results if r["status"] == "APPROVED")
    rejected_n  = sum(1 for r in results if r["status"] in ("REJECTED", "PRE_REJECTED"))
    print(f"  Results: {approved_n} APPROVED  |  {rejected_n} REJECTED  |  {len(results)} TOTAL")

    if not args.dry_run:
        # Append new trades to holdings trade_log
        working_holdings.setdefault("trade_log", []).extend(trade_log)
        save_json(HOLDINGS_FILE, working_holdings)
        print(f"  💾 Holdings updated: {HOLDINGS_FILE}")

        # Save trade log archive
        trades_path = os.path.join(DATA_DIR, f"neptune_trades_{ts}.json")
        save_json(trades_path, {
            "run_date": datetime.now().isoformat(),
            "account_id": "OC-CLIENT-001",
            "orders_file": args.orders,
            "results": results,
            "trades_executed": trade_log
        })
        print(f"  💾 Trade log: {trades_path}")

        # Save portfolio snapshot
        snap_path = os.path.join(DATA_DIR, f"neptune_snapshot_{ts}.json")
        save_json(snap_path, working_holdings)
        print(f"  💾 Snapshot: {snap_path}")

        # HTML report
        report_html = generate_report(results, working_holdings, ts)
        report_path = os.path.join(REPORTS_DIR, f"neptune_report_{ts}.html")
        with open(report_path, "w") as f:
            f.write(report_html)
        print(f"  📄 Report: {report_path}")

    else:
        print("\n  ⚠️  Dry run — no files modified.")

    # Final portfolio summary
    s = working_holdings["summary"]
    print(f"\n  Portfolio after run:")
    print(f"    Total   : ${s['total_portfolio_value']:>14,.2f}")
    print(f"    Cash    : ${s['cash_value']:>14,.2f}  ({s['cash_pct']:.2f}%)")
    print(f"    G/L     : ${s['total_gain_loss']:>14,.2f}  ({s['total_gain_loss_pct']:+.2f}%)")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
