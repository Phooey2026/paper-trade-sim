#!/usr/bin/env python3
"""
build_orbit_dashboard.py — Orbit Dashboard Generator
Reads orbit_outbox/ runs and produces a standalone dark-theme HTML dashboard.

Usage:
    python3 build_orbit_dashboard.py
    python3 build_orbit_dashboard.py --output /path/output.html
"""

import json, os, glob, argparse
from datetime import datetime

SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
OUTBOX_DIR    = os.path.join(SCRIPT_DIR, "orbit_outbox")
PAPER_DIR     = os.path.dirname(SCRIPT_DIR)
HOLDINGS_FILE = os.path.join(PAPER_DIR, "neptune_holdings.json")
ESC_FILE      = os.path.join(SCRIPT_DIR, "neptune_escalations.json")
OUTPUT_FILE   = os.path.join(SCRIPT_DIR, "orbit_dashboard.html")

def load_json(path):
    if not os.path.exists(path): return None
    with open(path) as f: return json.load(f)

def load_all_runs():
    results = []
    for rf in sorted(glob.glob(os.path.join(OUTBOX_DIR, "orbit_run_*.json")), reverse=True):
        try:
            data = load_json(rf)
            if data:
                for r in data.get("results", []):
                    r["_run_date"] = data.get("run_date","")[:10]
                    r["_nav"]      = data.get("nav", 101.39)
                results.extend(data.get("results", []))
        except: pass
    return results

def load_escalations():
    data = load_json(ESC_FILE)
    return data.get("escalations", []) if data else []

def fmt_money(v):
    return f"${float(v or 0):,.2f}"
def fmt_pct(v):
    n = float(v or 0)
    return f"{'+'if n>=0 else ''}{n:.2f}%"

ICONS = {"buy":"💰","sell":"💸","full_close":"🚪","statement":"📄",
         "address":"📮","beneficiary":"👤","holdings":"📊"}
TYPE_LABELS = {"buy":"BUY ORDER","sell":"SELL ORDER","full_close":"FULL CLOSE",
               "statement":"STATEMENT","address":"ADDRESS CHANGE",
               "beneficiary":"BENEFICIARY","holdings":"HOLDINGS INQUIRY"}

def build_issue_cards(results):
    if not results:
        return """<div class="empty-state">
          <div class="empty-icon">🌀</div>
          <div>No issues processed yet.</div>
          <code>python3 generate_issues.py && python3 orbit.py</code>
        </div>"""

    cards = []
    for r in results:
        itype   = r.get("issue_type","")
        icon    = ICONS.get(itype,"❓")
        label   = TYPE_LABELS.get(itype, itype.upper())
        esc     = r.get("escalation")
        resp    = r.get("orbit_response","")
        txn     = r.get("transaction",{})
        top5    = r.get("top5_shared",[])
        pending = r.get("pending", False)
        stmt    = r.get("statement_file","")

        # Status badge
        if esc and esc.get("status") == "RESOLVED":
            status_html = '<span class="badge-escalated">⚡ ESCALATED → NEPTUNE → ✅ RESOLVED</span>'
            card_border = "card-escalated"
        elif pending:
            status_html = '<span class="badge-pending">⏳ PENDING AUTHORIZATION</span>'
            card_border = "card-pending"
        elif r.get("error"):
            status_html = '<span class="badge-error">❌ ERROR</span>'
            card_border = "card-error"
        else:
            status_html = '<span class="badge-resolved">✅ RESOLVED</span>'
            card_border = "card-resolved"

        # Transaction detail
        txn_html = ""
        if txn:
            txn_html = f"""
            <div class="txn-strip">
              <span class="txn-type {'txn-buy' if txn.get('type')=='BUY' else 'txn-sell'}">
                {txn.get('type','')}</span>
              <span>{fmt_money(txn.get('amount_dollars',0))}</span>
              <span class="muted">{txn.get('shares',0):,.4f} shares @ ${txn.get('nav',0):.2f} NAV</span>
              {'<span class="txn-esc">⚡ Escalated to Neptune</span>' if txn.get('escalated') else ''}
              {'<span class="txn-close">🚪 Full Close</span>' if txn.get('is_full_close') else ''}
            </div>"""

        # Top 5 for holdings inquiry
        top5_html = ""
        if top5:
            rows = "".join(f"<tr><td><strong>{h['ticker']}</strong></td>"
                          f"<td class='muted'>{h['company']}</td>"
                          f"<td class='num'>{h['weight_pct']}%</td></tr>"
                          for h in top5)
            top5_html = f"""
            <div class="top5-block">
              <div class="block-label">Top 5 Disclosed</div>
              <table class="mini-table"><tbody>{rows}</tbody></table>
              <div class="policy-note">📋 Policy: TOP_5_ONLY — full list withheld</div>
            </div>"""

        # Escalation detail
        esc_html = ""
        if esc:
            actions = esc.get("actions",[])
            action_rows = "".join(
                f"<tr><td><strong>{a['ticker']}</strong></td>"
                f"<td class='muted'>{a.get('verdict','AVOID')}</td>"
                f"<td class='num'>{fmt_money(a['sold_dollars'])}</td></tr>"
                for a in actions)
            esc_html = f"""
            <div class="esc-block">
              <div class="esc-header">⚡ Neptune Escalation — {esc.get('status','')}</div>
              <div class="esc-detail">
                Shortfall: {fmt_money(esc.get('shortfall_dollars',0))} &nbsp;|&nbsp;
                Raised: {fmt_money(esc.get('total_raised',0))} &nbsp;|&nbsp;
                Positions sold: {len(actions)}
              </div>
              {f'<table class="mini-table"><tbody>{action_rows}</tbody></table>' if actions else ''}
            </div>"""

        stmt_html = ""
        if stmt:
            stmt_html = f'<div class="stmt-note">📄 Statement generated: <code>{os.path.basename(stmt)}</code></div>'

        cards.append(f"""
        <div class="issue-card {card_border}">
          <div class="card-header">
            <div class="card-title">
              <span class="type-icon">{icon}</span>
              <span class="type-label">{label}</span>
              <span class="client-name">{r.get('client_name','')}</span>
            </div>
            <div class="card-meta">
              {status_html}
              <span class="acct-value">{fmt_money(r.get('account_value',0))}</span>
              <span class="run-date muted">{r.get('_run_date','')}</span>
            </div>
          </div>
          {txn_html}
          {esc_html}
          {top5_html}
          {stmt_html}
          <div class="orbit-response">
            <div class="resp-label">🌀 Orbit Response</div>
            <div class="resp-body">{resp.replace(chr(10),'<br>')}</div>
          </div>
        </div>""")

    return "\n".join(cards)


def build_escalations_tab(escalations):
    if not escalations:
        return '<div class="empty-state"><div class="empty-icon">⚡</div><div>No escalations yet.</div></div>'

    rows = []
    for esc in reversed(escalations):
        actions = esc.get("actions",[])
        tickers = ", ".join(a["ticker"] for a in actions)
        rows.append(f"""
        <div class="esc-record">
          <div class="esc-rec-header">
            <span class="esc-id">{esc.get('escalation_id','')}</span>
            <span class="badge-{'resolved' if esc.get('status')=='RESOLVED' else 'pending'}">
              {esc.get('status','')}
            </span>
          </div>
          <div class="esc-rec-body">
            <div>Shortfall: <strong>{fmt_money(esc.get('shortfall_dollars',0))}</strong> &nbsp;|&nbsp;
                 Raised: <strong>{fmt_money(esc.get('total_raised',0))}</strong></div>
            <div class="muted">Positions sold: {tickers or '—'}</div>
            <div class="muted">Issue: {esc.get('issue_id','')}</div>
          </div>
          <div class="esc-actions">
            {''.join(f"<span class='esc-action'>{a['ticker']} {fmt_money(a['sold_dollars'])}</span>" for a in actions)}
          </div>
        </div>""")
    return "\n".join(rows)


def build_transactions_tab(results):
    txns = [(r, r["transaction"]) for r in results if r.get("transaction")]
    if not txns:
        return '<div class="empty-state"><div class="empty-icon">💸</div><div>No transactions yet.</div></div>'

    rows = "".join(f"""
    <tr>
      <td class='muted'>{r.get('_run_date','')}</td>
      <td><strong>{r.get('client_name','')}</strong></td>
      <td class="{'txn-buy' if t.get('type')=='BUY' else 'txn-sell'}">{t.get('type','')}</td>
      <td class='num'>{t.get('shares',0):,.4f}</td>
      <td class='num'>${t.get('nav',0):.2f}</td>
      <td class='num'><strong>{fmt_money(t.get('amount_dollars',0))}</strong></td>
      <td class='num'>{fmt_money(t.get('cash_after',0))}</td>
      {'<td><span class="badge-escalated">⚡</span></td>' if t.get('escalated') else '<td></td>'}
    </tr>""" for r, t in txns)

    return f"""
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Date</th><th>Client</th><th>Type</th>
          <th class="num">Shares</th><th class="num">NAV</th>
          <th class="num">Amount</th><th class="num">Cash After</th><th>⚡</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>"""


def build_statements_tab(results):
    stmts = [r for r in results if r.get("statement_html")]
    if not stmts:
        return '<div class="empty-state"><div class="empty-icon">📄</div><div>No statements generated yet.</div></div>'

    cards = []
    for r in stmts:
        # Embed statement as an iframe-like block
        cards.append(f"""
        <div class="stmt-card">
          <div class="stmt-card-header">
            <span>📄 {r.get('client_name','')} — {r.get('_run_date','')}</span>
            <span class="muted">{fmt_money(r.get('account_value',0))}</span>
          </div>
          <div class="stmt-preview">
            <iframe srcdoc="{r['statement_html'].replace(chr(34), '&quot;').replace(chr(39),'&#39;')}"
                    style="width:100%;height:600px;border:none;border-radius:4px">
            </iframe>
          </div>
        </div>""")
    return "\n".join(cards)


def generate_html(results, escalations, generated_at):
    total       = len(results)
    orders      = sum(1 for r in results if r.get("issue_type") in ("buy","sell","full_close"))
    escalated   = len(escalations)
    stmts       = sum(1 for r in results if r.get("statement_file"))
    pending     = sum(1 for r in results if r.get("pending"))
    full_closes = sum(1 for r in results if r.get("issue_type") == "full_close")
    compliance  = sum(1 for r in results if r.get("policy_applied") == "TOP_5_ONLY")

    issue_cards    = build_issue_cards(results)
    esc_tab        = build_escalations_tab(escalations)
    txn_tab        = build_transactions_tab(results)
    stmt_tab       = build_statements_tab(results)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>🌀 Orbit | Obsidian Capital</title>
<style>
:root {{
  --bg:#0d1117;--bg2:#161b22;--bg3:#1c2128;--bg4:#21262d;
  --border:#30363d;--border2:#21262d;
  --text:#e6edf3;--muted:#7d8590;
  --blue:#58a6ff;--blue-dim:#1f6feb;
  --green:#3fb950;--green-dim:#1a4731;
  --red:#f85149;--red-dim:#3d1a1a;
  --gold:#e3b341;--purple:#bc8cff;
  --orbit:#38bdf8;--esc:#f97316;
}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'SF Mono','Cascadia Code',monospace;background:var(--bg);
     color:var(--text);font-size:13px;}}

.header{{background:var(--bg2);border-bottom:1px solid var(--border);
         padding:14px 24px;display:flex;align-items:center;
         justify-content:space-between;position:sticky;top:0;z-index:100;}}
.header-left{{display:flex;align-items:center;gap:12px;}}
.orbit-icon{{font-size:24px;}}
.header-title{{font-size:17px;font-weight:700;color:var(--orbit);}}
.header-sub{{font-size:11px;color:var(--muted);margin-top:2px;}}
.header-right{{font-size:11px;color:var(--muted);text-align:right;}}
.header-right strong{{color:var(--text);}}

.scoreboard{{display:flex;gap:10px;padding:16px 24px;flex-wrap:wrap;
             border-bottom:1px solid var(--border2);}}
.score-card{{background:var(--bg2);border:1px solid var(--border);
             border-radius:8px;padding:12px 16px;min-width:110px;
             border-top:2px solid var(--border);}}
.score-card.blue   {{border-top-color:var(--blue);}}
.score-card.green  {{border-top-color:var(--green);}}
.score-card.esc    {{border-top-color:var(--esc);}}
.score-card.purple {{border-top-color:var(--purple);}}
.score-card.gold   {{border-top-color:var(--gold);}}
.score-card.red    {{border-top-color:var(--red);}}
.score-label{{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;}}
.score-value{{font-size:22px;font-weight:700;margin-top:4px;}}

.tabs{{display:flex;border-bottom:1px solid var(--border);padding:0 24px;
       background:var(--bg2);}}
.tab{{padding:9px 18px;font-size:12px;font-weight:600;color:var(--muted);
      cursor:pointer;border-bottom:2px solid transparent;text-transform:uppercase;
      letter-spacing:.06em;background:none;border-top:none;border-left:none;
      border-right:none;font-family:inherit;transition:all .15s;}}
.tab:hover{{color:var(--text);}}
.tab.active{{color:var(--orbit);border-bottom-color:var(--orbit);}}

.panel{{display:none;padding:20px 24px;}}
.panel.active{{display:block;}}

/* Issue cards */
.issue-card{{background:var(--bg2);border:1px solid var(--border);
             border-radius:10px;margin-bottom:18px;overflow:hidden;}}
.card-resolved  {{border-left:3px solid var(--green);}}
.card-escalated {{border-left:3px solid var(--esc);}}
.card-pending   {{border-left:3px solid var(--gold);}}
.card-error     {{border-left:3px solid var(--red);}}

.card-header{{display:flex;justify-content:space-between;align-items:flex-start;
              padding:12px 16px 10px;border-bottom:1px solid var(--border2);}}
.card-title{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;}}
.type-icon{{font-size:18px;}}
.type-label{{font-size:11px;font-weight:700;color:var(--muted);
             text-transform:uppercase;letter-spacing:.07em;}}
.client-name{{font-size:14px;font-weight:700;color:var(--orbit);}}
.card-meta{{display:flex;flex-direction:column;align-items:flex-end;gap:4px;}}
.acct-value{{font-size:13px;font-weight:600;color:var(--gold);}}
.run-date,.muted{{color:var(--muted);font-size:11px;}}

.badge-resolved {{color:var(--green);font-weight:700;font-size:12px;}}
.badge-escalated{{color:var(--esc);font-weight:700;font-size:12px;}}
.badge-pending  {{color:var(--gold);font-weight:700;font-size:12px;}}
.badge-error    {{color:var(--red);font-weight:700;font-size:12px;}}

.txn-strip{{padding:8px 16px;background:var(--bg3);border-bottom:1px solid var(--border2);
            display:flex;gap:14px;align-items:center;font-size:12px;}}
.txn-buy{{color:var(--green);font-weight:700;}}
.txn-sell{{color:var(--red);font-weight:700;}}
.txn-esc{{color:var(--esc);font-size:11px;}}
.txn-close{{color:var(--red);font-size:11px;}}

.esc-block{{padding:12px 16px;background:#1a0e05;border-bottom:1px solid var(--border2);}}
.esc-header{{font-weight:700;color:var(--esc);font-size:12px;margin-bottom:6px;}}
.esc-detail{{font-size:11px;color:var(--muted);margin-bottom:8px;}}

.top5-block{{padding:12px 16px;background:var(--bg3);border-bottom:1px solid var(--border2);}}
.block-label{{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px;}}
.policy-note{{font-size:10px;color:var(--gold);margin-top:8px;}}

.mini-table{{width:100%;border-collapse:collapse;font-size:12px;}}
.mini-table td{{padding:4px 8px;border-bottom:1px solid var(--border2);}}
.mini-table tr:last-child td{{border-bottom:none;}}

.orbit-response{{padding:14px 16px;}}
.resp-label{{font-size:10px;color:var(--orbit);text-transform:uppercase;
             letter-spacing:.07em;margin-bottom:8px;font-weight:600;}}
.resp-body{{font-size:12px;line-height:1.7;color:var(--text);}}

.stmt-note{{padding:8px 16px;font-size:11px;color:var(--muted);
            border-top:1px solid var(--border2);background:var(--bg3);}}

/* Escalation records */
.esc-record{{background:var(--bg2);border:1px solid var(--border);
             border-left:3px solid var(--esc);border-radius:8px;
             margin-bottom:12px;padding:14px 16px;}}
.esc-rec-header{{display:flex;justify-content:space-between;margin-bottom:8px;}}
.esc-id{{font-size:12px;font-weight:600;color:var(--esc);}}
.esc-rec-body{{font-size:12px;color:var(--text);margin-bottom:8px;line-height:1.8;}}
.esc-actions{{display:flex;flex-wrap:wrap;gap:6px;}}
.esc-action{{background:var(--bg3);border:1px solid var(--border);
             border-radius:4px;padding:3px 8px;font-size:11px;color:var(--muted);}}

/* Statements */
.stmt-card{{background:var(--bg2);border:1px solid var(--border);
            border-radius:8px;margin-bottom:20px;overflow:hidden;}}
.stmt-card-header{{padding:12px 16px;background:var(--bg3);border-bottom:1px solid var(--border);
                   display:flex;justify-content:space-between;font-size:13px;font-weight:600;}}
.stmt-preview{{padding:16px;}}

/* Table */
.table-wrap{{border:1px solid var(--border);border-radius:8px;overflow-x:auto;}}
table{{width:100%;border-collapse:collapse;font-size:12px;}}
thead tr{{background:var(--bg3);}}
th{{padding:9px 12px;text-align:left;color:var(--muted);font-size:10px;
    text-transform:uppercase;letter-spacing:.07em;border-bottom:1px solid var(--border);}}
td{{padding:9px 12px;border-bottom:1px solid var(--border2);}}
tbody tr:last-child td{{border-bottom:none;}}
tbody tr:hover td{{background:var(--bg3);}}
.num{{text-align:right;}}

.empty-state{{text-align:center;padding:60px 20px;color:var(--muted);}}
.empty-icon{{font-size:40px;margin-bottom:12px;}}
.empty-state code{{display:block;margin-top:12px;background:var(--bg2);
                   padding:8px 16px;border-radius:6px;font-size:12px;color:var(--orbit);}}

.footer{{padding:14px 24px;font-size:10px;color:var(--muted);
         border-top:1px solid var(--border2);margin-top:24px;}}
</style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <div class="orbit-icon">🌀</div>
    <div>
      <div class="header-title">Orbit — Client Services</div>
      <div class="header-sub">Obsidian Capital &nbsp;·&nbsp; Orders · Statements · Changes · Holdings</div>
    </div>
  </div>
  <div class="header-right">
    <div>Generated: <strong>{generated_at}</strong></div>
    <div>Runs in outbox: <strong>{total} issues</strong></div>
  </div>
</div>

<div class="scoreboard">
  <div class="score-card blue">
    <div class="score-label">Issues Processed</div>
    <div class="score-value">{total}</div>
  </div>
  <div class="score-card green">
    <div class="score-label">Orders Executed</div>
    <div class="score-value">{orders}</div>
  </div>
  <div class="score-card esc">
    <div class="score-label">Neptune Escalations</div>
    <div class="score-value">{escalated}</div>
  </div>
  <div class="score-card red">
    <div class="score-label">Full Closes</div>
    <div class="score-value">{full_closes}</div>
  </div>
  <div class="score-card gold">
    <div class="score-label">Statements Sent</div>
    <div class="score-value">{stmts}</div>
  </div>
  <div class="score-card purple">
    <div class="score-label">Compliance: Top-5</div>
    <div class="score-value">{compliance}</div>
  </div>
  <div class="score-card">
    <div class="score-label">Pending Auth</div>
    <div class="score-value">{pending}</div>
  </div>
</div>

<div class="tabs">
  <button class="tab active" onclick="showTab('issues',this)">🌀 Issues</button>
  <button class="tab" onclick="showTab('escalations',this)">⚡ Escalations</button>
  <button class="tab" onclick="showTab('transactions',this)">💸 Transactions</button>
  <button class="tab" onclick="showTab('statements',this)">📄 Statements</button>
</div>

<div id="issues"       class="panel active">{issue_cards}</div>
<div id="escalations"  class="panel">{esc_tab}</div>
<div id="transactions" class="panel">{txn_tab}</div>
<div id="statements"   class="panel">{stmt_tab}</div>

<div class="footer">
  Orbit Client Services Agent &nbsp;·&nbsp; Obsidian Capital &nbsp;·&nbsp;
  Rebuild: <code>python3 build_orbit_dashboard.py</code>
</div>

<script>
function showTab(id,btn){{
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  btn.classList.add('active');
}}
</script>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="Orbit Dashboard Builder")
    parser.add_argument("--output", default=OUTPUT_FILE)
    args = parser.parse_args()

    print("=" * 55)
    print(" 🌀 Orbit Dashboard Builder")
    print(f"    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 55)

    results     = load_all_runs()
    escalations = load_escalations()
    print(f"\n📋 Issues loaded: {len(results)}")
    print(f"⚡ Escalations  : {len(escalations)}")

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    html = generate_html(results, escalations, generated_at)

    with open(args.output, "w") as f:
        f.write(html)
    print(f"\n✅ Dashboard: {args.output}")


if __name__ == "__main__":
    main()
