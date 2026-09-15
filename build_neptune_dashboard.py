#!/usr/bin/env python3
"""
build_neptune_dashboard.py — Neptune Dashboard Generator
Reads neptune_holdings.json and all neptune_trades_*.json files
and produces a standalone neptune_dashboard.html.

Usage:
    python3 build_neptune_dashboard.py
    python3 build_neptune_dashboard.py --clear-trades   # Archive trade logs then rebuild
    python3 build_neptune_dashboard.py --output /path/to/output.html
"""

import json
import os
import sys
import glob
import shutil
import argparse
from datetime import datetime

SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
HOLDINGS_FILE = os.path.join(SCRIPT_DIR, "neptune_holdings.json")
DATA_DIR      = os.path.join(SCRIPT_DIR, "data")
REPORTS_DIR   = os.path.join(SCRIPT_DIR, "reports")
OUTPUT_FILE   = os.path.join(SCRIPT_DIR, "neptune_dashboard.html")


def load_json(path):
    with open(path) as f:
        return json.load(f)


def load_all_trades():
    """Load and merge all neptune_trades_*.json files, sorted oldest first."""
    all_results = []
    trade_files = sorted(glob.glob(os.path.join(DATA_DIR, "neptune_trades_*.json")))
    for tf in trade_files:
        try:
            data = load_json(tf)
            run_date = data.get("run_date", "")
            for r in data.get("results", []):
                r["_run_date"] = run_date
                r["_run_file"] = os.path.basename(tf)
            all_results.extend(data.get("results", []))
        except Exception as e:
            print(f"  ⚠️  Could not load {tf}: {e}")
    return all_results, len(trade_files)


def clear_trades():
    """Archive all trade files to data/archived_trades/ and clear."""
    archive_dir = os.path.join(DATA_DIR, "archived_trades")
    os.makedirs(archive_dir, exist_ok=True)
    trade_files = glob.glob(os.path.join(DATA_DIR, "neptune_trades_*.json"))
    snap_files  = glob.glob(os.path.join(DATA_DIR, "neptune_snapshot_*.json"))
    for f in trade_files + snap_files:
        shutil.move(f, os.path.join(archive_dir, os.path.basename(f)))
    print(f"  📦 Archived {len(trade_files)} trade files + {len(snap_files)} snapshots")
    print(f"     → {archive_dir}")


def fmt_money(v):
    try:
        v = float(v)
        return f"${v:,.2f}"
    except:
        return str(v)


def fmt_pct(v):
    try:
        v = float(v)
        sign = "+" if v >= 0 else ""
        return f"{sign}{v:.2f}%"
    except:
        return str(v)


def build_holdings_rows(positions, total_portfolio):
    rows = []
    for ticker, p in sorted(positions.items(), key=lambda x: -x[1]["market_value"]):
        gl     = p.get("gain_loss", 0)
        gl_pct = p.get("gain_loss_pct", 0)
        weight = round((p["market_value"] / total_portfolio) * 100, 2) if total_portfolio else 0
        gl_cls = "pos" if gl >= 0 else "neg"
        arrow  = "▲" if gl >= 0 else "▼"
        atype  = p.get("asset_type", "equity")
        badge  = "etf-badge" if atype == "etf" else "eq-badge"
        rows.append(f"""
          <tr>
            <td><span class="ticker-cell">{ticker}</span>
                <span class="{badge}">{atype.upper()}</span></td>
            <td class="company-col">{p.get('company', ticker)}</td>
            <td class="num">{p['shares']:,.4f}</td>
            <td class="num">{fmt_money(p['avg_cost_per_share'])}</td>
            <td class="num">{fmt_money(p['last_price'])}</td>
            <td class="num">{fmt_money(p['cost_basis'])}</td>
            <td class="num">{fmt_money(p['market_value'])}</td>
            <td class="num {gl_cls}">{arrow} {fmt_money(abs(gl))}</td>
            <td class="num {gl_cls}">{fmt_pct(gl_pct)}</td>
            <td class="num">{weight:.2f}%</td>
            <td class="num muted">{p.get('price_date','—')}</td>
          </tr>""")
    return "\n".join(rows)


def build_activity_rows(results):
    if not results:
        return """<tr><td colspan="7" class="empty-row">
            No trade activity yet. Run neptune.py to generate orders.</td></tr>"""

    rows = []
    for r in reversed(results):  # Most recent first
        order    = r.get("order", {})
        status   = r.get("status", "")
        rationale = r.get("neptune_rationale", r.get("reject_reason", "—"))
        agent    = order.get("agent", "").upper()
        ticker   = order.get("ticker", "")
        action   = order.get("action", "")
        dollars  = order.get("dollar_amount", 0)
        run_date = r.get("_run_date", "")[:10]
        confidence = order.get("neptune_confidence", "")

        if status == "APPROVED":
            status_html = '<span class="badge-approved">✅ APPROVED</span>'
        elif status == "PRE_REJECTED":
            status_html = '<span class="badge-prerej">⛔ PRE-REJECTED</span>'
        else:
            status_html = '<span class="badge-rejected">❌ REJECTED</span>'

        action_cls = "action-buy" if action == "BUY" else "action-sell"
        conf_html  = f'<span class="conf-{confidence.lower()}">{confidence}</span>' if confidence else ""

        # Truncate long rationale
        short_rat = rationale[:120] + "…" if len(rationale) > 120 else rationale

        rows.append(f"""
          <tr>
            <td class="muted">{run_date}</td>
            <td><span class="agent-badge">{agent}</span></td>
            <td><strong>{ticker}</strong></td>
            <td class="{action_cls}">{action}</td>
            <td class="num">{fmt_money(dollars)}</td>
            <td>{status_html} {conf_html}</td>
            <td class="rationale-col" title="{rationale}">{short_rat}</td>
          </tr>""")
    return "\n".join(rows)


def generate_html(holdings, all_results, n_trade_files, generated_at):
    s          = holdings["summary"]
    positions  = holdings["positions"]
    account_id = holdings.get("account_id", "OC-CLIENT-001")
    last_updated = holdings.get("last_updated", "—")

    total_val  = s["total_portfolio_value"]
    invested   = s["total_invested"]
    cash       = s["cash_value"]
    cash_pct   = s["cash_pct"]
    total_gl   = s["total_gain_loss"]
    gl_pct     = s["total_gain_loss_pct"]
    cost_basis = s["total_cost_basis"]
    eq_val     = s["total_equity_value"]
    etf_val    = s["total_etf_value"]

    gl_cls     = "pos" if total_gl >= 0 else "neg"
    gl_arrow   = "▲" if total_gl >= 0 else "▼"

    approved_n   = sum(1 for r in all_results if r.get("status") == "APPROVED")
    rejected_n   = sum(1 for r in all_results if r.get("status") in ("REJECTED", "PRE_REJECTED"))
    total_orders = len(all_results)

    holdings_rows = build_holdings_rows(positions, total_val)
    activity_rows = build_activity_rows(all_results)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🔱 Neptune — {account_id}</title>
<style>
  :root {{
    --bg:        #0d1117;
    --bg2:       #161b22;
    --bg3:       #1c2128;
    --border:    #30363d;
    --border2:   #21262d;
    --text:      #e6edf3;
    --muted:     #7d8590;
    --blue:      #58a6ff;
    --blue-dim:  #1f6feb;
    --green:     #3fb950;
    --green-dim: #1a4731;
    --red:       #f85149;
    --red-dim:   #3d1a1a;
    --purple:    #bc8cff;
    --gold:      #e3b341;
    --cyan:      #39d353;
    --accent:    #388bfd;
  }}

  * {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', monospace;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    font-size: 13px;
  }}

  /* ── Header ─────────────────────────────────────── */
  .header {{
    background: var(--bg2);
    border-bottom: 1px solid var(--border);
    padding: 16px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    position: sticky;
    top: 0;
    z-index: 100;
  }}
  .header-left {{ display: flex; align-items: center; gap: 16px; }}
  .trident {{ font-size: 28px; line-height: 1; }}
  .header-title {{ font-size: 18px; font-weight: 700; color: var(--blue); letter-spacing: -0.02em; }}
  .header-sub {{ font-size: 11px; color: var(--muted); margin-top: 2px; }}
  .header-meta {{ text-align: right; font-size: 11px; color: var(--muted); }}
  .header-meta strong {{ color: var(--text); }}

  /* ── Summary cards ───────────────────────────────── */
  .summary-section {{ padding: 20px 24px 0; }}
  .cards {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 12px;
    margin-bottom: 20px;
  }}
  .card {{
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 16px;
  }}
  .card .label {{
    font-size: 10px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 6px;
  }}
  .card .value {{
    font-size: 20px;
    font-weight: 700;
    color: var(--text);
    letter-spacing: -0.02em;
  }}
  .card .sub {{
    font-size: 11px;
    color: var(--muted);
    margin-top: 3px;
  }}
  .card.accent-blue  {{ border-top: 2px solid var(--blue); }}
  .card.accent-green {{ border-top: 2px solid var(--green); }}
  .card.accent-gold  {{ border-top: 2px solid var(--gold); }}
  .card.accent-purple {{ border-top: 2px solid var(--purple); }}

  /* ── Activity stats row ─────────────────────────── */
  .stat-row {{
    display: flex;
    gap: 12px;
    padding: 0 24px 16px;
    flex-wrap: wrap;
  }}
  .stat-pill {{
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 20px;
    padding: 6px 14px;
    font-size: 12px;
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  .stat-pill .n {{ font-weight: 700; font-size: 15px; }}

  /* ── Tabs ────────────────────────────────────────── */
  .tabs {{
    display: flex;
    gap: 0;
    border-bottom: 1px solid var(--border);
    padding: 0 24px;
    margin-bottom: 0;
  }}
  .tab {{
    padding: 10px 20px;
    font-size: 12px;
    font-weight: 600;
    color: var(--muted);
    cursor: pointer;
    border-bottom: 2px solid transparent;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    transition: all 0.15s;
    background: none;
    border-top: none;
    border-left: none;
    border-right: none;
    font-family: inherit;
  }}
  .tab:hover {{ color: var(--text); }}
  .tab.active {{
    color: var(--blue);
    border-bottom-color: var(--blue);
  }}

  /* ── Tab panels ──────────────────────────────────── */
  .panel {{ display: none; padding: 20px 24px; }}
  .panel.active {{ display: block; }}

  /* ── Tables ──────────────────────────────────────── */
  .table-wrap {{ overflow-x: auto; border-radius: 8px; border: 1px solid var(--border); }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
    min-width: 900px;
  }}
  thead tr {{
    background: var(--bg3);
  }}
  th {{
    padding: 10px 12px;
    text-align: left;
    color: var(--muted);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }}
  th.num, td.num {{ text-align: right; }}
  td {{
    padding: 9px 12px;
    border-bottom: 1px solid var(--border2);
    vertical-align: middle;
  }}
  tbody tr:last-child td {{ border-bottom: none; }}
  tbody tr:hover td {{ background: var(--bg3); }}

  /* ── Cell styles ─────────────────────────────────── */
  .ticker-cell {{
    font-weight: 700;
    color: var(--blue);
    font-size: 13px;
    margin-right: 6px;
  }}
  .company-col {{ color: var(--muted); max-width: 180px; }}
  .pos {{ color: var(--green); }}
  .neg {{ color: var(--red); }}
  .muted {{ color: var(--muted); }}

  .eq-badge, .etf-badge {{
    display: inline-block;
    padding: 1px 6px;
    border-radius: 4px;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.05em;
  }}
  .eq-badge  {{ background: #1f3a5f; color: var(--blue); }}
  .etf-badge {{ background: #3d2a1a; color: var(--gold); }}

  .agent-badge {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 10px;
    font-weight: 700;
    background: var(--bg3);
    color: var(--purple);
    border: 1px solid var(--border);
  }}

  .badge-approved  {{ color: var(--green); font-weight: 700; }}
  .badge-rejected  {{ color: var(--red);   font-weight: 700; }}
  .badge-prerej    {{ color: var(--gold);  font-weight: 700; }}

  .action-buy  {{ color: var(--green); font-weight: 700; }}
  .action-sell {{ color: var(--red);   font-weight: 700; }}

  .conf-high   {{ color: var(--green); font-size: 10px; }}
  .conf-medium {{ color: var(--gold);  font-size: 10px; }}
  .conf-low    {{ color: var(--muted); font-size: 10px; }}

  .rationale-col {{
    color: var(--muted);
    max-width: 380px;
    font-size: 11px;
    line-height: 1.4;
    cursor: help;
  }}

  .empty-row {{
    text-align: center;
    color: var(--muted);
    padding: 40px !important;
    font-size: 13px;
  }}

  /* ── Reset button ────────────────────────────────── */
  .actions-bar {{
    display: flex;
    justify-content: flex-end;
    margin-bottom: 14px;
    gap: 10px;
  }}
  .btn {{
    padding: 7px 16px;
    border-radius: 6px;
    font-size: 11px;
    font-weight: 600;
    cursor: pointer;
    font-family: inherit;
    letter-spacing: 0.04em;
    border: 1px solid var(--border);
    background: var(--bg2);
    color: var(--muted);
    transition: all 0.15s;
  }}
  .btn:hover {{ color: var(--text); border-color: var(--blue); }}
  .btn-danger {{ color: var(--red); }}
  .btn-danger:hover {{ border-color: var(--red); background: var(--red-dim); color: var(--red); }}

  /* ── Separator ───────────────────────────────────── */
  .section-label {{
    font-size: 10px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.1em;
    padding: 12px 0 8px;
    border-top: 1px solid var(--border2);
    margin-top: 16px;
  }}

  /* ── Footer ──────────────────────────────────────── */
  .footer {{
    padding: 20px 24px;
    font-size: 10px;
    color: var(--muted);
    border-top: 1px solid var(--border2);
    margin-top: 24px;
  }}
</style>
</head>
<body>

<!-- ── Header ──────────────────────────────────────────── -->
<div class="header">
  <div class="header-left">
    <div class="trident">🔱</div>
    <div>
      <div class="header-title">Neptune Paper Trading</div>
      <div class="header-sub">{account_id} &nbsp;·&nbsp; Obsidian Capital Research Fund</div>
    </div>
  </div>
  <div class="header-meta">
    <div>Price date: <strong>{last_updated}</strong></div>
    <div>Generated: <strong>{generated_at}</strong></div>
    <div>Trade files: <strong>{n_trade_files}</strong></div>
  </div>
</div>

<!-- ── Summary Cards ───────────────────────────────────── -->
<div class="summary-section">
  <div class="cards">
    <div class="card accent-blue">
      <div class="label">Total Portfolio</div>
      <div class="value">{fmt_money(total_val)}</div>
      <div class="sub">Positions + Cash</div>
    </div>
    <div class="card accent-green">
      <div class="label">Total Gain / Loss</div>
      <div class="value {gl_cls}">{gl_arrow} {fmt_money(abs(total_gl))}</div>
      <div class="sub {gl_cls}">{fmt_pct(gl_pct)} vs cost basis</div>
    </div>
    <div class="card accent-gold">
      <div class="label">Cash (VMRXX)</div>
      <div class="value">{fmt_money(cash)}</div>
      <div class="sub">{cash_pct:.2f}% of portfolio</div>
    </div>
    <div class="card accent-purple">
      <div class="label">Cost Basis</div>
      <div class="value">{fmt_money(cost_basis)}</div>
      <div class="sub">Equities {fmt_money(eq_val)} &nbsp;·&nbsp; ETFs {fmt_money(etf_val)}</div>
    </div>
    <div class="card">
      <div class="label">Positions</div>
      <div class="value">{len(positions)}</div>
      <div class="sub">{sum(1 for p in positions.values() if p.get('asset_type')=='equity')} equities &nbsp;·&nbsp; {sum(1 for p in positions.values() if p.get('asset_type')=='etf')} ETFs</div>
    </div>
    <div class="card">
      <div class="label">Orders Processed</div>
      <div class="value">{total_orders}</div>
      <div class="sub">{approved_n} approved &nbsp;·&nbsp; {rejected_n} rejected</div>
    </div>
  </div>
</div>

<!-- ── Tabs ─────────────────────────────────────────────── -->
<div class="tabs">
  <button class="tab active" onclick="showTab('holdings', this)">📊 Holdings</button>
  <button class="tab" onclick="showTab('activity', this)">⚡ Activity</button>
</div>

<!-- ── Holdings Panel ──────────────────────────────────── -->
<div id="holdings" class="panel active">
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Ticker</th>
          <th>Company</th>
          <th class="num">Shares</th>
          <th class="num">Avg Cost</th>
          <th class="num">Last Price</th>
          <th class="num">Cost Basis</th>
          <th class="num">Market Value</th>
          <th class="num">Gain / Loss</th>
          <th class="num">G/L %</th>
          <th class="num">Weight</th>
          <th class="num">Price Date</th>
        </tr>
      </thead>
      <tbody>
        {holdings_rows}
        <!-- Cash row -->
        <tr style="background: #161b22;">
          <td><span class="ticker-cell">VMRXX</span>
              <span class="etf-badge">CASH</span></td>
          <td class="company-col">Vanguard Federal Money Market</td>
          <td class="num muted">—</td>
          <td class="num muted">$1.00</td>
          <td class="num muted">$1.00</td>
          <td class="num">{fmt_money(cash)}</td>
          <td class="num">{fmt_money(cash)}</td>
          <td class="num muted">—</td>
          <td class="num muted">—</td>
          <td class="num">{cash_pct:.2f}%</td>
          <td class="num muted">—</td>
        </tr>
      </tbody>
    </table>
  </div>
</div>

<!-- ── Activity Panel ──────────────────────────────────── -->
<div id="activity" class="panel">
  <div class="actions-bar">
    <button class="btn" onclick="window.print()">🖨 Print</button>
    <button class="btn btn-danger"
      onclick="if(confirm('Archive all trade logs? This cannot be undone.')){{
        alert('Run: python3 build_neptune_dashboard.py --clear-trades\\nThen rebuild the dashboard.');
      }}">
      🗑 Clear Trade Log
    </button>
  </div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Date</th>
          <th>Agent</th>
          <th>Ticker</th>
          <th>Action</th>
          <th class="num">Amount</th>
          <th>Decision</th>
          <th>Neptune Rationale</th>
        </tr>
      </thead>
      <tbody>
        {activity_rows}
      </tbody>
    </table>
  </div>
</div>

<div class="footer">
  Neptune Paper Trading System &nbsp;·&nbsp; Obsidian Capital &nbsp;·&nbsp;
  Account {account_id} &nbsp;·&nbsp; For simulation use only — not real financial advice.
  &nbsp;·&nbsp; Rebuild: <code>python3 build_neptune_dashboard.py</code>
  &nbsp;·&nbsp; Restore: <code>cp neptune_holdings_ORIGINAL.json neptune_holdings.json</code>
</div>

<script>
  function showTab(id, btn) {{
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.getElementById(id).classList.add('active');
    btn.classList.add('active');
  }}
</script>

</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="Neptune Dashboard Generator")
    parser.add_argument("--clear-trades", action="store_true",
                        help="Archive all trade log files before rebuilding")
    parser.add_argument("--output", type=str, default=OUTPUT_FILE,
                        help="Output HTML file path")
    args = parser.parse_args()

    os.makedirs(REPORTS_DIR, exist_ok=True)

    print("=" * 55)
    print(" 🔱 Neptune Dashboard Builder")
    print(f"    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 55)

    if args.clear_trades:
        print("\n🗑  Clearing trade logs...")
        clear_trades()

    holdings = load_json(HOLDINGS_FILE)
    print(f"\n📊 Holdings loaded: {len(holdings['positions'])} positions")
    print(f"   Portfolio: ${holdings['summary']['total_portfolio_value']:,.2f}")

    all_results, n_files = load_all_trades()
    print(f"⚡ Trade files loaded: {n_files} files, {len(all_results)} orders total")

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    html = generate_html(holdings, all_results, n_files, generated_at)

    with open(args.output, "w") as f:
        f.write(html)

    print(f"\n✅ Dashboard written: {args.output}")
    print(f"   Open in browser or copy to stock_dashboard/ for serving")
    print(f"\nRestore original holdings:")
    print(f"   cp ~/paper_trade/neptune_holdings_ORIGINAL.json ~/paper_trade/neptune_holdings.json")


if __name__ == "__main__":
    main()
