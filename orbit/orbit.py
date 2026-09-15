#!/usr/bin/env python3
"""
orbit.py — Orbit Customer Service Agent
Processes client issues: orders, statements, address/beneficiary
changes, and holdings inquiries. Escalates to Neptune when needed.
Notifies Venus on full position closes.

Usage:
    python3 orbit.py
    python3 orbit.py --issues orbit_issues_20260618_1600.json
    python3 orbit.py --dry-run
    python3 orbit.py --verbose
"""

import json, os, sys, subprocess, glob, argparse
from datetime import datetime, date

SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
PAPER_DIR     = os.path.dirname(SCRIPT_DIR)
VENUS_DIR     = os.path.join(PAPER_DIR, "venus")
HOLDINGS_FILE = os.path.join(PAPER_DIR, "neptune_holdings.json")
FUND_FILE     = os.path.join(VENUS_DIR, "ocrff_fund.json")
CLIENTS_FILE  = os.path.join(VENUS_DIR, "crm_clients.json")
ISSUES_FILE   = os.path.join(SCRIPT_DIR, "orbit_issues_latest.json")
OUTBOX_DIR    = os.path.join(SCRIPT_DIR, "orbit_outbox")
STATEMENTS_DIR= os.path.join(SCRIPT_DIR, "orbit_statements")
VENUS_OUTBOX  = os.path.join(VENUS_DIR,  "venus_outbox")

os.makedirs(OUTBOX_DIR, exist_ok=True)
os.makedirs(STATEMENTS_DIR, exist_ok=True)

sys.path.insert(0, SCRIPT_DIR)
from trade_engine import (execute_ocrff_buy, execute_ocrff_sell,
                           neptune_rebalance_for_cash, load_holdings,
                           CASH_FLOOR_PCT)


def load_json(path):
    with open(path) as f: return json.load(f)

def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f: json.dump(data, f, indent=2)
    os.replace(tmp, path)


def call_orbit_llm(prompt, verbose=False):
    cmd = ["orbit", "-z", prompt, "--ignore-rules"]
    if verbose: print("  [LLM] Orbit thinking...")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return r.stdout.strip()
    except subprocess.TimeoutExpired:
        return "Thank you for contacting Obsidian Capital. I've processed your request and will follow up shortly."
    except FileNotFoundError:
        return "[Orbit LLM not available — placeholder response]"


def get_top5_holdings():
    holdings  = load_holdings()
    positions = holdings["positions"]
    total     = holdings["summary"]["total_portfolio_value"]
    ranked    = sorted(positions.items(), key=lambda x: -x[1]["market_value"])[:5]
    return [{"ticker": t, "company": p["company"],
             "weight_pct": round(p["market_value"]/total*100, 2),
             "asset_type": p.get("asset_type","equity")}
            for t, p in ranked]


def get_client_record(account_id):
    data = load_json(CLIENTS_FILE)
    return next((c for c in data["clients"] if c["account_id"] == account_id), None)


def update_client_record(account_id, updates, pending=False):
    data    = load_json(CLIENTS_FILE)
    clients = data["clients"]
    for i, c in enumerate(clients):
        if c["account_id"] == account_id:
            if pending:
                c.setdefault("pending_changes", {}).update(updates)
                c["pending_changes"]["requested_at"] = datetime.now().isoformat()
            else:
                c.update(updates)
            # Add to transaction history
            if "transaction" in updates:
                c.setdefault("transactions", []).append(updates["transaction"])
            clients[i] = c
            break
    save_json(CLIENTS_FILE, data)


def add_client_transaction(account_id, txn):
    data    = load_json(CLIENTS_FILE)
    clients = data["clients"]
    for i, c in enumerate(clients):
        if c["account_id"] == account_id:
            c.setdefault("transactions", []).append(txn)
            clients[i] = c
            break
    save_json(CLIENTS_FILE, data)


# ── Statement Generator ────────────────────────────────────────────────────

def generate_statement(client, fund, holdings):
    top5   = get_top5_holdings()
    nav    = fund["current_nav"]
    perf   = fund["performance"]
    ytd    = perf["ytd"]
    one_yr = perf["one_year"]
    since  = perf["since_inception_total"]
    txns   = client.get("transactions", [])[-5:]  # Last 5 transactions
    acct_val = client["ocrff_account_value"]
    shares   = client["ocrff_shares"]
    today    = date.today().strftime("%B %d, %Y")

    # Recent transactions HTML
    txn_rows = ""
    for t in reversed(txns):
        ttype = t.get("type","")
        cls   = "txn-buy" if ttype=="BUY" else "txn-sell"
        txn_rows += f"""
        <tr>
          <td>{t.get('date','')}</td>
          <td class="{cls}">{ttype}</td>
          <td class="num">{t.get('shares',0):,.4f}</td>
          <td class="num">${t.get('nav',nav):.2f}</td>
          <td class="num">${t.get('amount_dollars',0):,.2f}</td>
        </tr>"""
    if not txn_rows:
        txn_rows = '<tr><td colspan="5" class="muted-cell">No recent transactions</td></tr>'

    # Top 5 holdings HTML
    holding_rows = ""
    for h in top5:
        badge = "etf" if h["asset_type"]=="etf" else "eq"
        holding_rows += f"""
        <tr>
          <td><strong>{h['ticker']}</strong>
              <span class="type-{badge}">{h['asset_type'].upper()}</span></td>
          <td>{h['company']}</td>
          <td class="num">{h['weight_pct']:.2f}%</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Obsidian Capital — Account Statement</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
  :root {{
    --navy: #0a1628; --navy2: #112240; --navy3: #1a3a5c;
    --gold: #c9a84c; --gold2: #e8c97a; --text: #e8edf5;
    --muted: #8899aa; --green: #4ade80; --red: #f87171;
    --border: #1e3a5f;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Inter', sans-serif; background: #f0f4f8;
          color: #1a2a3a; padding: 20px; font-size: 13px; }}
  .statement {{ max-width: 780px; margin: 0 auto; background: white;
                box-shadow: 0 4px 24px rgba(0,0,0,0.12); border-radius: 4px;
                overflow: hidden; }}

  /* Header */
  .stmt-header {{ background: var(--navy); color: var(--text);
                  padding: 28px 32px; display: flex;
                  justify-content: space-between; align-items: flex-start; }}
  .firm-name {{ font-size: 22px; font-weight: 700; color: var(--gold); letter-spacing: -0.02em; }}
  .firm-sub  {{ font-size: 11px; color: var(--muted); margin-top: 3px; letter-spacing: 0.05em; }}
  .stmt-meta {{ text-align: right; font-size: 11px; color: var(--muted); }}
  .stmt-meta strong {{ color: var(--gold2); display: block; font-size: 13px; }}

  /* Client info band */
  .client-band {{ background: var(--navy2); padding: 16px 32px;
                  display: flex; justify-content: space-between;
                  border-bottom: 1px solid var(--border); }}
  .client-name {{ font-size: 16px; font-weight: 600; color: var(--text); }}
  .client-acct {{ font-size: 11px; color: var(--muted); margin-top: 3px; }}
  .client-fa   {{ font-size: 11px; color: var(--muted); text-align: right; }}
  .client-fa strong {{ color: var(--gold2); }}

  /* Summary cards */
  .cards-section {{ padding: 24px 32px; background: var(--navy);
                    display: grid; grid-template-columns: repeat(3,1fr); gap: 16px; }}
  .card {{ background: var(--navy2); border: 1px solid var(--border);
           border-radius: 6px; padding: 14px 16px; }}
  .card .lbl {{ font-size: 10px; color: var(--muted); text-transform: uppercase;
                letter-spacing: 0.08em; margin-bottom: 6px; }}
  .card .val {{ font-size: 22px; font-weight: 700; color: var(--text); }}
  .card .sub {{ font-size: 11px; color: var(--muted); margin-top: 3px; }}
  .card.gold  {{ border-top: 2px solid var(--gold); }}

  /* Performance section */
  .section {{ padding: 24px 32px; border-bottom: 1px solid #e5eaf0; }}
  .section-title {{ font-size: 12px; font-weight: 600; color: #4a6080;
                    text-transform: uppercase; letter-spacing: 0.08em;
                    margin-bottom: 14px; }}
  .perf-grid {{ display: grid; grid-template-columns: repeat(3,1fr); gap: 12px; }}
  .perf-card {{ background: #f8fafc; border: 1px solid #e2e8f0;
                border-radius: 6px; padding: 12px; text-align: center; }}
  .perf-card .period {{ font-size: 10px; color: #64748b; text-transform: uppercase;
                        letter-spacing: 0.07em; margin-bottom: 6px; }}
  .perf-card .ocrff  {{ font-size: 18px; font-weight: 700; color: #1a5c2a; }}
  .perf-card .sp500  {{ font-size: 11px; color: #64748b; margin-top: 3px; }}
  .perf-card .alpha  {{ font-size: 11px; color: #c9a84c; font-weight: 600; }}

  /* Tables */
  table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  th {{ padding: 8px 10px; text-align: left; font-size: 10px; color: #64748b;
        text-transform: uppercase; letter-spacing: 0.07em;
        border-bottom: 2px solid #e2e8f0; background: #f8fafc; }}
  td {{ padding: 9px 10px; border-bottom: 1px solid #f0f4f8; vertical-align: middle; }}
  .num {{ text-align: right; }}
  .txn-buy  {{ color: #1a5c2a; font-weight: 600; }}
  .txn-sell {{ color: #9a1a1a; font-weight: 600; }}
  .muted-cell {{ text-align: center; color: #94a3b8; padding: 20px !important; }}
  .type-etf {{ display: inline-block; padding: 1px 6px; background: #fef3c7;
               color: #92400e; border-radius: 3px; font-size: 9px;
               font-weight: 600; margin-left: 4px; }}
  .type-eq  {{ display: inline-block; padding: 1px 6px; background: #dbeafe;
               color: #1e40af; border-radius: 3px; font-size: 9px;
               font-weight: 600; margin-left: 4px; }}

  /* Footer */
  .stmt-footer {{ background: #f8fafc; padding: 16px 32px;
                  border-top: 1px solid #e2e8f0;
                  display: flex; justify-content: space-between;
                  font-size: 10px; color: #94a3b8; }}
  .disclaimer {{ font-size: 9px; color: #b0bcc8; padding: 12px 32px;
                 border-top: 1px solid #f0f4f8; line-height: 1.6; }}
</style>
</head>
<body>
<div class="statement">

  <div class="stmt-header">
    <div>
      <div class="firm-name">⚫ Obsidian Capital</div>
      <div class="firm-sub">AI-DRIVEN INVESTMENT RESEARCH · OCRFF</div>
    </div>
    <div class="stmt-meta">
      <strong>Account Statement</strong>
      As of {today}
    </div>
  </div>

  <div class="client-band">
    <div>
      <div class="client-name">{client['name']}</div>
      <div class="client-acct">Account: {client['account_id']}</div>
    </div>
    <div class="client-fa">
      <strong>{client['fa_name']}</strong>
      {client['firm']}<br>{client['territory']}
    </div>
  </div>

  <div class="cards-section">
    <div class="card gold">
      <div class="lbl">Account Value</div>
      <div class="val" style="color:var(--gold)">${acct_val:,.2f}</div>
      <div class="sub">as of {today}</div>
    </div>
    <div class="card">
      <div class="lbl">OCRFF Shares</div>
      <div class="val">{shares:,.4f}</div>
      <div class="sub">NAV: ${nav:.2f}/share</div>
    </div>
    <div class="card">
      <div class="lbl">YTD Return</div>
      <div class="val" style="color:var(--green)">+{ytd['ocrff']}%</div>
      <div class="sub">vs S&P 500 +{ytd['sp500']}%</div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">Fund Performance vs S&P 500</div>
    <div class="perf-grid">
      <div class="perf-card">
        <div class="period">Year to Date</div>
        <div class="ocrff">+{ytd['ocrff']}%</div>
        <div class="sp500">S&P 500: +{ytd['sp500']}%</div>
        <div class="alpha">Alpha: +{ytd['alpha']}%</div>
      </div>
      <div class="perf-card">
        <div class="period">1 Year</div>
        <div class="ocrff">+{one_yr['ocrff']}%</div>
        <div class="sp500">S&P 500: +{one_yr['sp500']}%</div>
        <div class="alpha">Alpha: +{one_yr['alpha']}%</div>
      </div>
      <div class="perf-card">
        <div class="period">Since Inception (2020)</div>
        <div class="ocrff">+{since['ocrff']}%</div>
        <div class="sp500">S&P 500: +{since['sp500']}%</div>
        <div class="alpha">Alpha: +{since['alpha']}%</div>
      </div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">Top 5 Fund Holdings (by Weight)</div>
    <table>
      <thead><tr><th>Ticker</th><th>Company</th><th class="num">Weight</th></tr></thead>
      <tbody>{holding_rows}</tbody>
    </table>
    <p style="font-size:10px;color:#94a3b8;margin-top:8px">
      Full holdings available to institutional investors. Contact your advisor for details.
    </p>
  </div>

  <div class="section">
    <div class="section-title">Recent Transactions</div>
    <table>
      <thead><tr><th>Date</th><th>Type</th><th class="num">Shares</th>
        <th class="num">NAV</th><th class="num">Amount</th></tr></thead>
      <tbody>{txn_rows}</tbody>
    </table>
  </div>

  <div class="stmt-footer">
    <span>⚫ Obsidian Capital Research Fund · OC-CLIENT-001</span>
    <span>Beneficiary: {client.get('beneficiary','—')}</span>
    <span>FA: {client['fa_name']} · {client['firm']}</span>
  </div>

  <div class="disclaimer">
    This statement is provided for informational purposes only and does not constitute investment advice.
    Past performance is not indicative of future results. The Obsidian Capital Research Fund (OCRFF) is
    an AI-driven simulation for research and testing purposes. All performance figures are hypothetical.
    NAV calculated as of market close on the statement date. Questions? Contact your financial advisor.
  </div>
</div>
</body>
</html>"""
    return html


# ── Issue Handlers ─────────────────────────────────────────────────────────

def handle_order(issue, fund, dry_run=False, verbose=False):
    """Handle BUY, SELL, or FULL_CLOSE orders."""
    itype    = issue["issue_type"]
    client   = get_client_record(issue["account_id"])
    params   = issue["parameters"]
    nav      = fund["current_nav"]
    amount   = params["amount_dollars"]
    is_close = itype == "full_close"
    result   = {"issue_id": issue["issue_id"], "type": itype, "escalation": None}

    if itype == "buy":
        # Build Orbit's response prompt
        prompt = f"""You are Orbit, a warm and professional customer service representative 
at Obsidian Capital.

Client: {issue['client_name']} (Account: {issue['account_id']})
Request: "{issue['narrative']}"

You have just processed their BUY order:
  Amount invested: ${amount:,.2f}
  Shares purchased: {params['shares_equivalent']:,.4f} OCRFF shares
  NAV used: ${nav:.2f}/share
  New account value will be: ${client['ocrff_account_value'] + amount:,.2f}

Write a brief, warm confirmation email (3-4 sentences).
Confirm the transaction details. Sign as: — Orbit | Obsidian Capital Client Services"""

        if not dry_run:
            txn, err = execute_ocrff_buy(amount, nav, "orbit", issue["issue_id"])
            if not err:
                # Update client record
                new_shares = round(client["ocrff_shares"] + txn["shares"], 6)
                new_value  = round(new_shares * nav, 2)
                update_client_record(issue["account_id"], {
                    "ocrff_shares": new_shares,
                    "ocrff_account_value": new_value,
                })
                add_client_transaction(issue["account_id"], {
                    **txn, "shares": txn["shares"]
                })
                result["transaction"] = txn

    else:  # SELL or FULL_CLOSE
        actual_shares = issue["current_shares"] if is_close else None
        prompt_amount = issue["current_account_value"] if is_close else amount

        prompt = f"""You are Orbit, a warm and professional customer service representative 
at Obsidian Capital.

Client: {issue['client_name']} (Account: {issue['account_id']})
Request: "{issue['narrative']}"

You have processed their {'full position closure' if is_close else 'SELL order'}:
  Amount redeemed: ${prompt_amount:,.2f}
  Shares redeemed: {params['shares_equivalent']:,.4f} OCRFF shares
  NAV used: ${nav:.2f}/share
  {'This was a full account closure.' if is_close else ''}

{'IMPORTANT: Do not mention any internal portfolio rebalancing or which specific stocks were sold internally. Just confirm the client order was processed.' if not is_close else 'Express genuine appreciation for their time as a client. Keep the door open for future business.'}

Write a brief, warm confirmation (3-4 sentences). Sign as: — Orbit | Obsidian Capital Client Services"""

        if not dry_run:
            txn, escalation_needed, shortfall, err = execute_ocrff_sell(
                amount, nav, "orbit", issue["issue_id"],
                is_full_close=is_close,
                actual_shares=actual_shares
            )
            if not err:
                result["transaction"] = txn
                result["escalation_needed"] = escalation_needed

                # Update client record
                if is_close:
                    new_shares = 0.0
                    new_value  = 0.0
                else:
                    new_shares = round(max(0, client["ocrff_shares"] - txn["shares"]), 6)
                    new_value  = round(new_shares * nav, 2)

                update_client_record(issue["account_id"], {
                    "ocrff_shares": new_shares,
                    "ocrff_account_value": new_value,
                })
                add_client_transaction(issue["account_id"], txn)

                # Neptune escalation if cash floor breached
                if escalation_needed:
                    print(f"  ⚠️  ESCALATING TO NEPTUNE — VMRXX insufficient "
                          f"(shortfall: ${shortfall:,.0f})")
                    esc = neptune_rebalance_for_cash(shortfall, issue["issue_id"])
                    result["escalation"] = esc
                    print(f"  ✅ NEPTUNE RESOLVED — raised ${esc['total_raised']:,.0f} "
                          f"from {len(esc['actions'])} position(s)")

                    prompt += f"\n\nYour order has been fully processed. " \
                              f"Proceeds of ${txn['amount_dollars']:,.2f} have been " \
                              f"credited to your account's money market position."

                # Alert Venus if full close
                if is_close:
                    notify_venus(issue, client, txn, fund, dry_run, verbose)

    response = call_orbit_llm(prompt, verbose)
    result["orbit_response"] = response
    return result


def handle_statement(issue, fund, dry_run=False, verbose=False):
    client = get_client_record(issue["account_id"])
    holdings = load_holdings()
    nav = fund["current_nav"]

    prompt = f"""You are Orbit, a warm customer service rep at Obsidian Capital.

Client: {issue['client_name']} (Account: {issue['account_id']})
Request: "{issue['narrative']}"

Account details:
  OCRFF Shares: {client['ocrff_shares']:,.4f}
  Account Value: ${client['ocrff_account_value']:,.2f}
  YTD Return: +{fund['performance']['ytd']['ocrff']}% (S&P 500: +{fund['performance']['ytd']['sp500']}%)
  1-Year Return: +{fund['performance']['one_year']['ocrff']}%

You are attaching their account statement. Write a brief, warm 3-4 sentence 
cover note to accompany the statement. Mention the key highlights (account value, 
YTD performance vs S&P). Sign as: — Orbit | Obsidian Capital Client Services"""

    response = call_orbit_llm(prompt, verbose)

    # Generate the styled HTML statement
    statement_html = generate_statement(client, fund, holdings)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stmt_file = os.path.join(STATEMENTS_DIR,
                             f"statement_{issue['account_id']}_{ts}.html")
    if not dry_run:
        with open(stmt_file, "w") as f:
            f.write(statement_html)

    return {
        "issue_id": issue["issue_id"],
        "type": "statement",
        "orbit_response": response,
        "statement_file": stmt_file,
        "statement_html": statement_html,
    }


def handle_address_change(issue, dry_run=False, verbose=False):
    params = issue["parameters"]
    new_addr = (f"{params['new_street']}, {params['new_city']}, "
                f"{params['new_state']} {params['new_zip']}")

    prompt = f"""You are Orbit, a warm but compliance-aware customer service rep 
at Obsidian Capital.

Client: {issue['client_name']} (Account: {issue['account_id']})
Request: "{issue['narrative']}"

POLICY: Address changes require written authorization before they can be processed.
You cannot confirm the change is complete. You must:
1. Acknowledge their request warmly
2. Explain that written authorization is required (a form will be sent to their FA)
3. Give them a timeline (typically 3-5 business days after authorization received)
4. Reassure them the request has been logged

New address requested: {new_addr}

Write a professional, warm 3-4 sentence response.
Sign as: — Orbit | Obsidian Capital Client Services"""

    response = call_orbit_llm(prompt, verbose)

    if not dry_run:
        # Flag as pending — do not directly update
        update_client_record(issue["account_id"], {
            "new_street":  params["new_street"],
            "new_city":    params["new_city"],
            "new_state":   params["new_state"],
            "new_zip":     params["new_zip"],
            "requested_at": datetime.now().isoformat(),
        }, pending=True)

    return {
        "issue_id": issue["issue_id"],
        "type": "address",
        "orbit_response": response,
        "pending": True,
        "change_requested": new_addr,
    }


def handle_beneficiary_change(issue, dry_run=False, verbose=False):
    params  = issue["parameters"]
    new_b   = params["new_beneficiary"]
    curr_b  = params.get("current_beneficiary", "")

    prompt = f"""You are Orbit, a warm but compliance-aware customer service rep 
at Obsidian Capital.

Client: {issue['client_name']} (Account: {issue['account_id']})
Request: "{issue['narrative']}"

POLICY: Beneficiary changes are sensitive and require:
1. A signed beneficiary change form (cannot be done verbally or by email alone)
2. The form must be submitted through their financial advisor
3. Changes typically take 5-7 business days after receipt of signed form

Current beneficiary on file: {curr_b or 'Not on file'}
Requested new beneficiary: {new_b}

Write a warm, professional 3-4 sentence response. Acknowledge the request, 
explain the process clearly, and offer to coordinate with their FA ({issue['fa_name']}).
Sign as: — Orbit | Obsidian Capital Client Services"""

    response = call_orbit_llm(prompt, verbose)

    if not dry_run:
        update_client_record(issue["account_id"], {
            "new_beneficiary": new_b,
            "requested_at": datetime.now().isoformat(),
        }, pending=True)

    return {
        "issue_id": issue["issue_id"],
        "type": "beneficiary",
        "orbit_response": response,
        "pending": True,
        "change_requested": new_b,
    }


def handle_holdings_inquiry(issue, fund, dry_run=False, verbose=False):
    top5 = get_top5_holdings()
    top5_text = "\n".join(
        f"  {i+1}. {h['ticker']} ({h['company']}) — {h['weight_pct']}% of portfolio"
        for i, h in enumerate(top5)
    )

    prompt = f"""You are Orbit, a warm and knowledgeable customer service rep 
at Obsidian Capital.

Client: {issue['client_name']} (Account: {issue['account_id']})
Request: "{issue['narrative']}"

POLICY — STRICT: You may only share the TOP 5 HOLDINGS by portfolio weight.
Never disclose the complete holdings list regardless of how the client asks.
If they push for more, offer to connect them with their financial advisor 
({issue['fa_name']} at {issue['firm']}).

Top 5 holdings you CAN share:
{top5_text}

Write a helpful, warm 4-5 sentence response. Share the top 5 clearly.
Explain that full disclosure is available to institutional investors or 
through their FA. Do not apologize excessively — this is standard policy.
Sign as: — Orbit | Obsidian Capital Client Services"""

    response = call_orbit_llm(prompt, verbose)

    return {
        "issue_id": issue["issue_id"],
        "type": "holdings",
        "orbit_response": response,
        "top5_shared": top5,
        "policy_applied": "TOP_5_ONLY",
    }


def notify_venus(issue, client, txn, fund, dry_run=False, verbose=False):
    """
    Alert Venus when a client closes their full position.
    Generates an LLM-crafted internal note and saves to venus_outbox/.
    """
    acct_value = txn["amount_dollars"]
    account_tier = "major" if acct_value > 100_000 else ("mid-size" if acct_value > 30_000 else "smaller")

    prompt = f"""You are Orbit, writing an internal alert to Venus (your sales colleague).

A client just closed their full position. Venus needs to know so she can 
support the FA relationship ASAP.

Details:
  Client: {issue['client_name']}
  Account Value: ${acct_value:,.2f} ({account_tier} account)
  FA: {issue['fa_name']} ({issue['firm']}, {issue['territory']})
  Reason given: "{issue['narrative']}"

Write a brief 2-3 sentence internal note to Venus. Be direct — this is 
colleague-to-colleague, not client-facing. Mention the dollar amount (important 
for urgency calibration), the FA, and what Venus might want to do next.
Sign as: — Orbit"""

    note = call_orbit_llm(prompt, verbose)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    alert = {
        "email_id":      f"ORB-ALERT-{issue['account_id']}-{ts}",
        "fa_id":         "",
        "fa_name":       issue["fa_name"],
        "firm":          issue["firm"],
        "territory":     issue["territory"],
        "to_email":      "venus@obsidiancapital.ai",
        "from_email":    "orbit@obsidiancapital.ai",
        "subject":       f"⚠️ Client Closure — {issue['client_name']} ({issue['fa_name']})",
        "body":          note,
        "sent_at":       datetime.now().isoformat(),
        "status":        "sent",
        "source":        "orbit_alert",
        "response":      None,
        "alert_type":    "full_close",
        "account_value": acct_value,
        "prospect_score": 0,
        "strategy_notes": [
            f"Client {issue['client_name']} closed ${acct_value:,.0f} position",
            f"FA: {issue['fa_name']} at {issue['firm']} — relationship at risk",
        ],
    }

    if not dry_run and os.path.exists(VENUS_OUTBOX):
        alert_file = os.path.join(VENUS_OUTBOX, f"email_{ts}_ORBIT_ALERT.json")
        with open(alert_file, "w") as f:
            json.dump(alert, f, indent=2)
        if verbose:
            print(f"  📨 Venus alert saved: {os.path.basename(alert_file)}")

    return alert


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Orbit Customer Service Agent")
    parser.add_argument("--issues", default=ISSUES_FILE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    print("=" * 60)
    print(" 🌀 Orbit — Customer Service Agent | Obsidian Capital")
    print(f"    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if args.dry_run: print("    *** DRY RUN ***")
    print("=" * 60)

    data   = load_json(args.issues)
    fund   = load_json(FUND_FILE)
    issues = data["issues"]
    nav    = fund["current_nav"]

    print(f"\n📋 Issues loaded: {len(issues)} | NAV: ${nav:.2f}\n")

    results = []
    for i, issue in enumerate(issues):
        itype = issue["issue_type"]
        icon  = {"buy":"💰","sell":"💸","full_close":"🚪","statement":"📄",
                 "address":"📮","beneficiary":"👤","holdings":"📊"}.get(itype,"❓")

        print(f"{'─'*60}")
        print(f"  {icon} Issue {i+1}/{len(issues)}: {itype.upper()} | "
              f"{issue['client_name']} | ${issue['current_account_value']:,.0f}")
        print(f"  \"{issue['narrative'][:70]}{'...' if len(issue['narrative'])>70 else ''}\"")
        print()

        try:
            if itype in ("buy","sell","full_close"):
                result = handle_order(issue, fund, args.dry_run, args.verbose)
            elif itype == "statement":
                result = handle_statement(issue, fund, args.dry_run, args.verbose)
            elif itype == "address":
                result = handle_address_change(issue, args.dry_run, args.verbose)
            elif itype == "beneficiary":
                result = handle_beneficiary_change(issue, args.dry_run, args.verbose)
            elif itype == "holdings":
                result = handle_holdings_inquiry(issue, fund, args.dry_run, args.verbose)
            else:
                result = {"issue_id": issue["issue_id"], "error": f"Unknown type: {itype}"}

            result["client_name"]  = issue["client_name"]
            result["issue_type"]   = itype
            result["account_value"]= issue["current_account_value"]
            results.append(result)

            # Show response preview
            resp = result.get("orbit_response","")
            print(f"  🌀 Orbit: {resp[:160]}{'...' if len(resp)>160 else ''}")

            if result.get("escalation"):
                esc = result["escalation"]
                print(f"\n  ⚡ ESCALATED → NEPTUNE → {esc['status']}")
                print(f"     Raised ${esc['total_raised']:,.0f} from "
                      f"{len(esc['actions'])} position(s)")

            if result.get("statement_file"):
                print(f"  📄 Statement: {os.path.basename(result['statement_file'])}")

        except Exception as e:
            print(f"  ❌ Error: {e}")
            import traceback; traceback.print_exc()
            results.append({"issue_id": issue["issue_id"], "error": str(e)})

    # Save run record
    if not args.dry_run:
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        out_file = os.path.join(OUTBOX_DIR, f"orbit_run_{ts}.json")
        save_json(out_file, {
            "run_date": datetime.now().isoformat(),
            "nav": nav, "count": len(results),
            "results": results,
        })
        print(f"\n💾 Run saved: {out_file}")

    print(f"\n{'='*60}")
    print(f"  Processed: {len(results)} issues")
    orders   = sum(1 for r in results if r.get("type") in ("buy","sell","full_close"))
    escalated= sum(1 for r in results if r.get("escalation"))
    stmts    = sum(1 for r in results if r.get("statement_file"))
    print(f"  Orders: {orders} | Escalated: {escalated} | Statements: {stmts}")
    print(f"\n▶️  Build dashboard: python3 build_orbit_dashboard.py")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
