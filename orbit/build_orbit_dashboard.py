#!/usr/bin/env python3
"""
build_orbit_dashboard.py — Orbit Dashboard Generator
Reads orbit_outbox/ runs and produces a standalone dark-theme HTML dashboard.

Usage:
    python3 build_orbit_dashboard.py
    python3 build_orbit_dashboard.py --output /path/output.html
"""

import html as _html
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

def esc(v):
    return _html.escape(str(v if v is not None else ""), quote=True)

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
            status_html = '<span class="badge-pending" title="Status when this issue was processed. Live paperwork queue: Forms tab.">⏳ AUTH REQUESTED (when processed)</span>'
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


CLIENTS_FILE = os.path.join(PAPER_DIR, "venus", "crm_clients.json")


def load_form_runs():
    """Form results from every saved run (newest first)."""
    out = []
    for rf in sorted(glob.glob(os.path.join(OUTBOX_DIR, "orbit_run_*.json")), reverse=True):
        try:
            data = load_json(rf) or {}
            for r in data.get("form_results", []) or []:
                r["_run_date"] = data.get("run_date", "")[:16].replace("T", " ")
                r["_mode"] = data.get("form_mode", "")
                out.append(r)
        except Exception:
            pass
    return out


def _thumb(path, width=300):
    """Small embedded JPEG so the dashboard is portable; link points at the full-size file."""
    import base64, io
    if not path or not os.path.isfile(path):
        return ""
    try:
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((width, int(width * 1.5)))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=70)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


def _chips(items, cls):
    return "".join('<span class="chip %s">%s</span>' % (cls, esc(i)) for i in items) or '<span class="muted">-</span>'


def build_forms_tab(form_results):
    crm = load_json(CLIENTS_FILE) or {"clients": []}
    queue = {}
    for c in crm["clients"]:
        for k, p in (c.get("pending_changes") or {}).items():
            if isinstance(p, dict) and p.get("status"):
                queue[p["status"]] = queue.get(p["status"], 0) + 1

    ok = [r for r in form_results if r.get("decision")]
    applied = sum(1 for r in ok if r.get("outcome") == "APPLIED")
    returned = sum(1 for r in ok if r.get("outcome") == "RETURNED")
    inj = sum(len(r["score"]["injected"]) for r in ok)
    det = sum(len(r["score"]["detected"]) for r in ok)
    extra = sum(len(r["score"]["extra"]) for r in ok)
    missed = sum(len(r["score"]["missed"]) for r in ok)
    planted_forms = sum(1 for r in ok if r["score"]["injected"])
    cost = 0.0
    for r in ok:
        try:
            cost += float(((r.get("reader") or {}).get("cost")) or 0)
        except Exception:
            pass

    summary = """
    <div class="fscore">
      <div class="fs"><div class="fl">Forms reviewed</div><div class="fv">%d</div></div>
      <div class="fs g"><div class="fl">Approved &rarr; CRM changed</div><div class="fv">%d</div></div>
      <div class="fs r"><div class="fl">Returned</div><div class="fv">%d</div></div>
      <div class="fs p"><div class="fl">Planted defects caught</div><div class="fv">%d / %d</div></div>
      <div class="fs o"><div class="fl">Planted defects missed</div><div class="fv">%d</div></div>
      <div class="fs o"><div class="fl">Unplanted flaws found</div><div class="fv">%d</div></div>
      <div class="fs"><div class="fl">Queue now</div><div class="fv sm">%s</div></div>
    </div>
    <div class="fnote">Planted = defects form_agent injected on purpose (a share of forms set by --defect-rate; none when it is 0). Unplanted = flaws the image
    model made by accident (blank signature box, garbled date, wrong box) &mdash; Orbit rejects those too.
    Review-model cost in these runs: $%.3f.</div>""" % (
        len(ok), applied, returned, det, inj, missed, extra,
        " &middot; ".join("%s %d" % (k.replace("_", " ").title(), v) for k, v in sorted(queue.items())) or "empty",
        cost)

    if not form_results:
        return summary + """<div class="empty-state"><div class="empty-icon">📎</div>
        <div>No forms processed yet.</div><code>python3 orbit.py --forms mock</code></div>"""

    cards = []
    for r in form_results:
        img = r.get("image_path") or ""
        th = _thumb(img)
        rel = os.path.relpath(img, SCRIPT_DIR) if img and os.path.isfile(img) else ""
        pic = ('<a href="%s" target="_blank"><img src="%s" alt="form"></a>' % (esc(rel), th)) if th else \
              '<div class="nopic">no image</div>'
        outcome = r.get("outcome", "?")
        badge = {"APPLIED": "ok", "RETURNED": "bad", "DEFERRED": "wait", "ERROR": "bad"}.get(outcome, "wait")
        sc = r.get("score") or {"injected": [], "detected": [], "missed": [], "extra": []}
        kind = r.get("kind", "")
        body = []
        if r.get("change"):
            body.append('<div class="ba"><span class="muted">Before</span> %s<br><span class="muted">After&nbsp;</span> %s</div>'
                        % (esc(r["change"]["before"]), esc(r["change"]["after"])))
        for f in r.get("findings") or []:
            body.append('<div class="find">✗ <b>%s</b> — %s</div>' % (esc(f["code"]), esc(f["detail"])))
        if r.get("error") or r.get("note"):
            body.append('<div class="find">%s</div>' % esc(r.get("error") or r.get("note")))
        notes = [str(c) for c in (r.get("concerns") or []) if "future" not in str(c).lower()]  # model thinks 2026 is the future
        if notes:
            body.append('<div class="muted">Reader noted: %s</div>' % esc("; ".join(notes)[:300]))
        if r.get("read_disagreement"):
            body.append('<div class="muted">⚠ The two reads disagreed on: %s</div>' % esc(", ".join(r["read_disagreement"])))
        chips = ""
        if r.get("score") and (sc["injected"] or sc["extra"]):
            chips = ('<div class="chips"><span class="clab">planted</span>%s<span class="clab">caught</span>%s'
                     '<span class="clab">missed</span>%s<span class="clab">unplanted</span>%s</div>') % (
                _chips(sc["injected"], "plant"), _chips(sc["detected"], "hit"),
                _chips(sc["missed"], "miss"), _chips(sc["extra"], "extra"))
        letter = r.get("letter") or ""
        rerq = r.get("re_request_letter") or ""
        letters = ""
        if rerq:
            letters += '<details><summary>Orbit re-request note</summary><pre>%s</pre></details>' % esc(rerq)
        if letter:
            letters += '<details><summary>Orbit letter</summary><pre>%s</pre></details>' % esc(letter)
        cards.append("""
        <div class="fcard">
          <div class="fpic">%s</div>
          <div class="fbody">
            <div class="fhead"><span>%s <b>%s</b> · %s</span><span class="fbadge %s">%s</span></div>
            <div class="muted">%s · attempt %s · %s%s · %s</div>
            %s%s%s
          </div>
        </div>""" % (pic, "📮" if kind == "address" else "👤", esc(r.get("client_name", "")), kind,
                     badge, esc(outcome), esc(r.get("account_id", "")), r.get("attempt", ""),
                     esc(r.get("form_id", "") or ""), " · legacy request" if r.get("legacy") else "",
                     esc(r.get("_run_date", "")), "".join(body), chips, letters))
    return summary + '<div class="fgrid">' + "".join(cards) + "</div>"


def build_history_tab():
    crm = load_json(CLIENTS_FILE) or {"clients": []}
    rows = []
    for c in crm["clients"]:
        for h in c.get("change_history") or []:
            rows.append((h.get("effective_at", ""), c, h))
    rows.sort(key=lambda t: t[0], reverse=True)
    if not rows:
        return '<div class="empty-state"><div class="empty-icon">🗂</div><div>No approved changes yet.</div></div>'

    def show(field, v):
        if field == "address" and isinstance(v, dict):
            return fmt_addr(v)
        if field == "beneficiary" and isinstance(v, list):
            return "; ".join("%s (%s) %g%%%s" % (b.get("name"), b.get("relationship"), b.get("percent", 0),
                                                 "" if b.get("type") == "primary" else " [contingent]") for b in v)
        return str(v)

    def fmt_addr(a):
        return "%s%s, %s %s %s" % (a.get("street", ""), (" " + a["unit"]) if a.get("unit") else "",
                                   a.get("city", ""), a.get("state", ""), a.get("zip", ""))
    body = "".join("""<tr><td class='muted'>%s</td><td><b>%s</b><div class='muted'>%s</div></td><td>%s</td>
        <td>%s</td><td>%s</td><td class='muted'>%s</td></tr>""" % (
        esc(t[0][:16].replace("T", " ")), esc(t[1]["name"]), esc(t[1]["account_id"]), esc(t[2]["field"]),
        esc(show(t[2]["field"], t[2].get("old"))), esc(show(t[2]["field"], t[2].get("new"))),
        esc(t[2].get("source_form_id") or "")) for t in rows[:200])
    return """<div class="table-wrap"><table><thead><tr><th>When</th><th>Client</th><th>Field</th><th>Before</th>
        <th>After</th><th>Source form</th></tr></thead><tbody>%s</tbody></table></div>""" % body


FORMS_CSS = """
.fscore{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px}
.fs{background:var(--bg2);border:1px solid var(--border);border-top:2px solid var(--border);border-radius:8px;padding:10px 14px;min-width:120px}
.fs.g{border-top-color:var(--green)}.fs.r{border-top-color:var(--red)}.fs.p{border-top-color:var(--purple)}.fs.o{border-top-color:var(--esc)}
.fl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.fv{font-size:20px;font-weight:700;margin-top:3px}.fv.sm{font-size:12px;font-weight:600;margin-top:6px}
.fnote{font-size:11px;color:var(--muted);margin:4px 0 14px;max-width:900px;line-height:1.5}
.fgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(520px,1fr));gap:12px}
.fcard{display:flex;gap:12px;background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:10px}
.fpic img{width:150px;border-radius:4px;border:1px solid var(--border);display:block}
.fpic .nopic{width:150px;height:200px;display:flex;align-items:center;justify-content:center;color:var(--muted);border:1px dashed var(--border);border-radius:4px}
.fbody{flex:1;min-width:0;font-size:12px;line-height:1.45}
.fhead{display:flex;justify-content:space-between;gap:8px;margin-bottom:2px}
.fbadge{font-size:10px;font-weight:700;padding:2px 8px;border-radius:10px;height:fit-content}
.fbadge.ok{background:var(--green-dim);color:var(--green)}.fbadge.bad{background:var(--red-dim);color:var(--red)}.fbadge.wait{background:var(--bg4);color:var(--muted)}
.ba{margin:6px 0;padding:6px 8px;background:var(--bg3);border-radius:4px;word-break:break-word}
.find{margin:3px 0;color:#ffb1ab}
.chips{margin-top:6px;line-height:1.9}.clab{font-size:9px;text-transform:uppercase;color:var(--muted);margin:0 4px 0 8px}
.chip{font-size:10px;padding:1px 7px;border-radius:9px;margin-right:3px;background:var(--bg4)}
.chip.plant{background:#2d2a4a;color:var(--purple)}.chip.hit{background:var(--green-dim);color:var(--green)}
.chip.miss{background:#4a2d1a;color:var(--esc)}.chip.extra{background:#4a3d1a;color:var(--gold)}
.fbody details{margin-top:6px}.fbody summary{cursor:pointer;color:var(--orbit);font-size:11px}
.fbody pre{white-space:pre-wrap;font-family:inherit;background:var(--bg3);padding:8px;border-radius:4px;margin-top:4px}
@media(max-width:640px){.fgrid{grid-template-columns:1fr}.fcard{flex-direction:column}.fpic img{width:100%}}
"""


def generate_html(results, escalations, generated_at, form_results=None):
    total       = len(results)
    orders      = sum(1 for r in results if r.get("issue_type") in ("buy","sell","full_close"))
    escalated   = len(escalations)
    stmts       = sum(1 for r in results if r.get("statement_file"))
    # live count: changes still waiting on paperwork (APPLIED ones are done) — not a tally of old issues
    _crm = load_json(CLIENTS_FILE) or {"clients": []}
    pending = sum(1 for _c in _crm["clients"] for _p in (_c.get("pending_changes") or {}).values()
                  if (_p.get("status") if isinstance(_p, dict) else _p) in ("AWAITING_FORM", "FORM_RECEIVED", "RETURNED"))
    full_closes = sum(1 for r in results if r.get("issue_type") == "full_close")
    compliance  = sum(1 for r in results if r.get("policy_applied") == "TOP_5_ONLY")

    issue_cards    = build_issue_cards(results)
    esc_tab        = build_escalations_tab(escalations)
    txn_tab        = build_transactions_tab(results)
    stmt_tab       = build_statements_tab(results)
    forms_tab      = build_forms_tab(form_results or [])
    history_tab    = build_history_tab()

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
{FORMS_CSS}
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
    <div class="score-label">Pending Auth (live)</div>
    <div class="score-value">{pending}</div>
  </div>
</div>

<div class="tabs">
  <button class="tab active" onclick="showTab('issues',this)">🌀 Issues</button>
  <button class="tab" onclick="showTab('escalations',this)">⚡ Escalations</button>
  <button class="tab" onclick="showTab('transactions',this)">💸 Transactions</button>
  <button class="tab" onclick="showTab('statements',this)">📄 Statements</button>
  <button class="tab" onclick="showTab('forms',this)">📎 Forms</button>
  <button class="tab" onclick="showTab('history',this)">🗂 Change History</button>
</div>

<div id="issues"       class="panel active">{issue_cards}</div>
<div id="escalations"  class="panel">{esc_tab}</div>
<div id="transactions" class="panel">{txn_tab}</div>
<div id="statements"   class="panel">{stmt_tab}</div>
<div id="forms"        class="panel">{forms_tab}</div>
<div id="history"      class="panel">{history_tab}</div>

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
    form_results = load_form_runs()
    print(f"📎 Form reviews : {len(form_results)}")
    html = generate_html(results, escalations, generated_at, form_results)

    with open(args.output, "w") as f:
        f.write(html)
    print(f"\n✅ Dashboard: {args.output}")


if __name__ == "__main__":
    main()
