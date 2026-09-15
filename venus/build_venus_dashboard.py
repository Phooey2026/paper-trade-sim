#!/usr/bin/env python3
"""
build_venus_dashboard.py — Venus Sales Dashboard Generator
Reads venus_outbox/, pipeline_state.json, crm_advisors.json, and
pipeline_reports/ to produce a standalone HTML dashboard showing the
full 5-week Venus sales pipeline.

Tabs:
  💬 Dialogue   — Full email thread per FA (all 5 weeks)
  📊 Pipeline   — Kanban view: each FA at their current week
  🏆 Clients    — Closed deals with narratives and deal values
  📣 Debrief    — Venus's Week 5 debrief email to Jay
  📋 Prospects  — Full prospect table

Usage:
    python3 build_venus_dashboard.py
    python3 build_venus_dashboard.py --output /path/to/output.html
"""

import json
import os
import glob
import argparse
from datetime import datetime

SCRIPT_DIR     = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR     = os.path.dirname(SCRIPT_DIR)
OUTBOX_DIR     = os.path.join(SCRIPT_DIR, "venus_outbox")
PIPELINE_FILE  = os.path.join(SCRIPT_DIR, "pipeline_state.json")
REPORTS_DIR    = os.path.join(SCRIPT_DIR, "pipeline_reports")
FUND_FILE      = os.path.join(SCRIPT_DIR, "ocrff_fund.json")
OUTPUT_FILE    = os.path.join(SCRIPT_DIR, "venus_dashboard.html")

_crm_parent = os.path.join(PARENT_DIR, "crm_advisors.json")
_crm_local  = os.path.join(SCRIPT_DIR, "crm_advisors.json")
CRM_FILE    = _crm_parent if os.path.exists(_crm_parent) else _crm_local


def load_json(path):
    with open(path) as f:
        return json.load(f)


def safe_load(path):
    try:
        return load_json(path)
    except Exception:
        return None


def load_all_emails():
    """Load all outbox emails from venus_outbox/ only (not archive)."""
    emails = []
    for ef in sorted(glob.glob(os.path.join(OUTBOX_DIR, "email_*.json")), reverse=True):
        try:
            rec = load_json(ef)
            emails.append(rec)
        except Exception:
            pass
    return emails


def load_latest_debrief():
    reports = sorted(glob.glob(os.path.join(REPORTS_DIR, "pipeline_report_*.json")))
    if not reports:
        return None
    return safe_load(reports[-1])


def fmt_currency(val):
    if val is None:
        return "—"
    return f"${val:,.0f}"


def fmt_pct(val):
    if val is None:
        return "—"
    return f"{val:.1f}%"


def activity_icon(activity):
    return {
        "Baseball": "⚾", "Golf": "⛳", "Surfing": "🏄", "Sailing": "⛵",
        "Skiing": "⛷️", "Fishing": "🎣", "Hiking": "🥾",
        "Mountain Biking": "🚵", "Tennis": "🎾", "Lunch": "🍽️", "Coffee": "☕",
    }.get(activity, "📍")


def pipeline_emails_only(emails):
    return [e for e in emails
            if e.get("source") != "debrief" and e.get("fa_id") != "INTERNAL"]


# ── Scoreboard ────────────────────────────────────────────────────────────

def build_scoreboard(emails):
    pe = pipeline_emails_only(emails)
    total     = len(pe)
    yes_count = sum(1 for e in pe if e.get("response") == "YES")
    no_count  = sum(1 for e in pe if e.get("response") == "NO")
    pending   = sum(1 for e in pe if e.get("response") is None)
    responded = total - pending

    closed_emails = [e for e in pe if e.get("meeting_outcome", {}).get("closed")]
    new_aum       = sum(e["meeting_outcome"].get("deal_value", 0) for e in closed_emails)
    meetings_held = len([e for e in pe if e.get("meeting_outcome")])
    close_rate    = round(len(closed_emails) / meetings_held * 100, 1) if meetings_held else 0
    conv_rate     = round(yes_count / max(responded, 1) * 100, 1)

    baseball_yes  = sum(1 for e in pe if e.get("response") == "YES"
                        and any("baseball" in n.lower() for n in e.get("strategy_notes", [])))
    warm_yes      = sum(1 for e in pe if e.get("response") == "YES"
                        and any("warm" in n.lower() for n in e.get("strategy_notes", [])))
    orbit_alerts  = sum(1 for e in emails if e.get("source") == "orbit_alert")

    return f"""
    <div class="scoreboard">
      <div class="score-card">
        <div class="score-label">Emails Sent</div>
        <div class="score-value">{total}</div>
      </div>
      <div class="score-card green">
        <div class="score-label">FA Replied YES</div>
        <div class="score-value">{yes_count}</div>
      </div>
      <div class="score-card red">
        <div class="score-label">Declined</div>
        <div class="score-value">{no_count}</div>
      </div>
      <div class="score-card gold">
        <div class="score-label">Pending</div>
        <div class="score-value">{pending}</div>
      </div>
      <div class="score-card blue">
        <div class="score-label">Conversion Rate</div>
        <div class="score-value">{conv_rate}%</div>
      </div>
      <div class="score-card purple">
        <div class="score-label">Baseball Hook ✅</div>
        <div class="score-value">{baseball_yes}</div>
      </div>
      <div class="score-card purple">
        <div class="score-label">Warm Lead ✅</div>
        <div class="score-value">{warm_yes}</div>
      </div>
      <div class="score-card red">
        <div class="score-label">Orbit Alerts 🚨</div>
        <div class="score-value">{orbit_alerts}</div>
      </div>
      <div class="score-card teal">
        <div class="score-label">Closed This Cycle</div>
        <div class="score-value">{len(closed_emails)}</div>
      </div>
      <div class="score-card teal">
        <div class="score-label">New AUM</div>
        <div class="score-value">{fmt_currency(new_aum)}</div>
      </div>
      <div class="score-card teal">
        <div class="score-label">Close Rate</div>
        <div class="score-value">{fmt_pct(close_rate)}</div>
      </div>
    </div>"""


# ── Orbit Alert Cards ────────────────────────────────────────────────────────

def build_orbit_alert_card(em):
    """Render a single Orbit full-close alert as a warning card.
    Real schema: client info is in subject/body/strategy_notes, not separate fields.
    """
    sent_at    = em.get("sent_at", "")[:16].replace("T", " ")
    acct_value = em.get("account_value", 0)
    fa_name    = em.get("fa_name", "")
    firm       = em.get("firm", "")
    territory  = em.get("territory", "")
    body       = em.get("body", "").replace("\n", "<br>")
    subject    = em.get("subject", "Orbit Alert")

    # Strategy notes contain the client detail lines
    notes      = em.get("strategy_notes", [])
    notes_html = "".join(
        f'<div>⚠️ {n}</div>' for n in notes
    )

    return f"""
        <div class="prospect-card card-orbit-alert">
          <div class="card-header">
            <div class="card-title">
              <span class="fa-name" style="color:var(--red)">🚨 {subject}</span>
              <span class="fa-firm">{fa_name} · {firm} · {territory}</span>
            </div>
            <div class="card-meta">
              <span class="week-pill" style="border-color:var(--red);color:var(--red)">ORBIT</span>
              <span class="badge-no">⚠️ ACTION REQUIRED</span>
              <span class="score-badge">{sent_at}</span>
            </div>
          </div>
          <div class="strategy-note" style="color:var(--red);background:rgba(248,81,73,0.07)">
            💰 Account value lost: {fmt_currency(acct_value)}
            {notes_html}
          </div>
          <div class="dialogue">
            <div class="message-block" style="background:#1a0808;border:1px solid #5a1515;max-width:100%;align-self:stretch">
              <div class="msg-header">
                <span class="msg-from" style="color:var(--red)">🌀 Orbit &lt;orbit@obsidiancapital.ai&gt;</span>
                <span class="msg-time">{sent_at}</span>
              </div>
              <div class="msg-body">{body}</div>
            </div>
          </div>
        </div>"""


# ── Dialogue Tab ──────────────────────────────────────────────────────────

def build_dialogue_cards(emails):
    pe = pipeline_emails_only(emails)

    # Orbit alerts rendered first as warning banners
    orbit_alerts = [e for e in emails if e.get("source") == "orbit_alert"]
    orbit_cards  = [build_orbit_alert_card(e) for e in orbit_alerts]

    if not pe and not orbit_alerts:
        return """<div class="empty-state">
            <div class="empty-icon">💫</div>
            <div>No emails sent yet.</div>
            <code>python3 fetch_prospects.py &amp;&amp; python3 venus.py --week 1</code>
        </div>"""

    cards = list(orbit_cards)  # orbit alerts at the top
    for em in pe:
        week     = em.get("pipeline_week", 1)
        status   = em.get("pipeline_status", "")
        decision = em.get("response")
        outcome  = em.get("meeting_outcome", {})
        closed   = outcome.get("closed")

        if closed:
            card_cls  = "card-closed"
            dec_badge = '<span class="badge-closed">💰 CLOSED</span>'
        elif status == "closed_no":
            card_cls  = "card-no"
            dec_badge = '<span class="badge-no">❌ DECLINED</span>'
        elif week >= 3:
            card_cls  = "card-yes"
            dec_badge = '<span class="badge-confirmed">📅 MEETING CONFIRMED</span>'
        elif week == 2:
            card_cls  = "card-yes"
            dec_badge = '<span class="badge-week2">📧 PROPOSAL SENT</span>'
        elif decision == "YES":
            card_cls  = "card-yes"
            dec_badge = '<span class="badge-yes">✅ MEETING REQUESTED</span>'
        elif decision == "NO":
            card_cls  = "card-no"
            dec_badge = '<span class="badge-no">❌ DECLINED</span>'
        else:
            card_cls  = "card-pending"
            dec_badge = '<span class="badge-pending">⏳ AWAITING RESPONSE</span>'

        sent_at    = em.get("sent_at", "")[:16].replace("T", " ")
        score      = em.get("prospect_score", "—")
        notes_html = "".join(
            f'<div class="strategy-note">💡 {n}</div>'
            for n in em.get("strategy_notes", [])[:2]
        )

        thread = []

        # Week 1 — Venus outreach
        venus_body = em.get("body", "").replace("\n", "<br>")
        thread.append(f"""
          <div class="message-block venus-message">
            <div class="msg-header">
              <span class="msg-from">💫 Venus &lt;venus@obsidiancapital.ai&gt;</span>
              <span class="msg-time">{sent_at}</span>
            </div>
            <div class="msg-subject">Subject: {em.get('subject','')}</div>
            <div class="msg-body">{venus_body}</div>
          </div>""")

        # Week 1 — FA reply
        if em.get("response_body"):
            fa_time  = em.get("responded_at", "")[:16].replace("T", " ")
            fa_body  = em.get("response_body", "").replace("\n", "<br>")
            thread.append(f"""
          <div class="message-block fa-message">
            <div class="msg-header">
              <span class="msg-from">🏦 {em.get('fa_name','FA')} ({em.get('firm','')})</span>
              <span class="msg-time">{fa_time}</span>
            </div>
            <div class="msg-body">{fa_body}</div>
          </div>""")
        elif decision is None:
            thread.append("""
          <div class="message-block fa-pending">
            <div class="msg-body muted">Waiting for FA agent response…
              <code>python3 fa_agent.py --all</code></div>
          </div>""")

        # Week 2 — Venus meeting proposal
        if em.get("follow_up_body"):
            w2_time  = em.get("week2_at", "")[:16].replace("T", " ")
            fu_body  = em.get("follow_up_body", "").replace("\n", "<br>")
            proposal = em.get("meeting_proposal", {})
            activity = proposal.get("activity", "")
            venue    = proposal.get("venue_name", "")
            act_line = (f'<div class="msg-activity">{activity_icon(activity)} {activity} @ {venue}</div>'
                        if activity else "")
            thread.append(f"""
          <div class="message-block venus-message">
            <div class="msg-header">
              <span class="msg-from">💫 Venus &lt;venus@obsidiancapital.ai&gt;</span>
              <span class="msg-time">{w2_time}</span>
            </div>
            <div class="msg-subject">Subject: {em.get('follow_up_subject','')}</div>
            {act_line}
            <div class="msg-body">{fu_body}</div>
          </div>""")

        # Week 3 — FA confirmation
        confirmed = em.get("meeting_confirmed", {})
        if confirmed.get("fa_reply"):
            w3_time   = em.get("week3_at", "")[:16].replace("T", " ")
            conf_body = confirmed["fa_reply"].replace("\n", "<br>")
            meet_line = (f'<div class="msg-activity">📅 Confirmed: '
                         f'{confirmed.get("day","")} {confirmed.get("date","")} '
                         f'at {confirmed.get("time","")}</div>')
            thread.append(f"""
          <div class="message-block fa-message">
            <div class="msg-header">
              <span class="msg-from">🏦 {em.get('fa_name','FA')} ({em.get('firm','')})</span>
              <span class="msg-time">{w3_time}</span>
            </div>
            {meet_line}
            <div class="msg-body">{conf_body}</div>
          </div>""")

        # Week 4 — Meeting narrative
        if outcome.get("narrative"):
            w4_time   = em.get("week4_at", "")[:16].replace("T", " ")
            narrative = outcome["narrative"].replace("\n", "<br>")
            deal_val  = outcome.get("deal_value", 0)
            prob      = outcome.get("close_probability", 0)
            roll      = outcome.get("dice_roll", 0)
            out_badge = (f'<span class="badge-closed">💰 CLOSED — {fmt_currency(deal_val)}</span>'
                         if closed else '<span class="badge-no">❌ No close</span>')
            thread.append(f"""
          <div class="message-block narrative-block {'narrative-closed' if closed else 'narrative-lost'}">
            <div class="msg-header">
              <span class="msg-from">🎙️ Jay — Meeting Debrief</span>
              <span class="msg-time">{w4_time}</span>
            </div>
            <div class="narrative-outcome">
              {out_badge}
              <span class="msg-odds">Close prob: {prob*100:.0f}% · Roll: {roll:.4f}</span>
            </div>
            <div class="msg-body narrative-text">{narrative}</div>
          </div>""")

        cards.append(f"""
        <div class="prospect-card {card_cls}">
          <div class="card-header">
            <div class="card-title">
              <span class="fa-name">{em.get('fa_name','')}</span>
              <span class="fa-firm">{em.get('firm','')}</span>
              <span class="fa-territory">{em.get('territory','')}</span>
            </div>
            <div class="card-meta">
              <span class="week-pill">WK {week}</span>
              {dec_badge}
              <span class="score-badge">Score: {score}</span>
            </div>
          </div>
          {notes_html}
          <div class="dialogue">{"".join(thread)}</div>
        </div>""")

    return "\n".join(cards)


# ── Pipeline Kanban Tab ───────────────────────────────────────────────────

def build_pipeline_tab(emails):
    pe = pipeline_emails_only(emails)
    if not pe:
        return '<div class="empty-state">No pipeline activity yet.</div>'

    stage_keys = [
        "Week 1 — Outreach",
        "Week 2 — Proposed",
        "Week 3 — Confirmed",
        "Week 4 — Met",
        "Week 5 — Complete",
    ]
    stages = {k: [] for k in stage_keys}

    for em in pe:
        week      = em.get("pipeline_week", 1)
        status    = em.get("pipeline_status", "")
        outcome   = em.get("meeting_outcome", {})
        closed    = outcome.get("closed")
        confirmed = em.get("meeting_confirmed", {})
        proposal  = em.get("meeting_proposal", {})
        activity  = confirmed.get("activity") or proposal.get("activity", "")
        deal_val  = outcome.get("deal_value", 0) if closed else 0

        if closed:
            indicator = f'<span class="ki-closed">💰 {fmt_currency(deal_val)}</span>'
        elif status == "closed_no":
            indicator = '<span class="ki-lost">❌ Declined</span>'
        elif outcome and not closed:
            indicator = '<span class="ki-lost">❌ No close</span>'
        elif week >= 3 and confirmed:
            indicator = f'<span class="ki-confirmed">📅 {confirmed.get("date","")}</span>'
        elif em.get("response") == "YES":
            indicator = '<span class="ki-yes">✅ Interested</span>'
        elif em.get("response") == "NO":
            indicator = '<span class="ki-lost">❌ No</span>'
        else:
            indicator = '<span class="ki-pending">⏳ Waiting</span>'

        act_tag = (f'<span class="ki-activity">{activity_icon(activity)} {activity}</span>'
                   if activity else "")

        card = f"""
          <div class="kanban-card">
            <div class="ki-name">{em.get('fa_name','')}</div>
            <div class="ki-firm">{em.get('firm','')}</div>
            {act_tag}
            {indicator}
          </div>"""

        stages[stage_keys[min(week - 1, 4)]].append(card)

    stage_colors = ["#58a6ff", "#e3b341", "#bc8cff", "#39c5cf", "#3fb950"]
    cols = []
    for i, (label, cards) in enumerate(stages.items()):
        inner = "\n".join(cards) if cards else '<div class="ki-empty">—</div>'
        cols.append(f"""
      <div class="kanban-col">
        <div class="kanban-header" style="border-top-color:{stage_colors[i]}">
          <span class="kanban-label">{label}</span>
          <span class="kanban-count">{len(cards)}</span>
        </div>
        {inner}
      </div>""")

    return f'<div class="kanban">\n{"".join(cols)}\n</div>'


# ── Clients Tab ───────────────────────────────────────────────────────────

def build_clients_tab(emails, crm_data):
    closed_emails = [e for e in pipeline_emails_only(emails)
                     if e.get("meeting_outcome", {}).get("closed")]
    crm_clients   = []
    if crm_data:
        crm_clients = [a for a in crm_data.get("advisors", []) if a["status"] == "CLIENT"]

    total_oc_aum  = sum(c.get("oc_aum", 0) for c in crm_clients)
    total_new_aum = sum(e["meeting_outcome"].get("deal_value", 0) for e in closed_emails)

    if not closed_emails and not crm_clients:
        return '<div class="empty-state">No closed deals yet. Run the pipeline through Week 4.</div>'

    new_cards = []
    for em in closed_emails:
        outcome   = em.get("meeting_outcome", {})
        confirmed = em.get("meeting_confirmed", {})
        deal_val  = outcome.get("deal_value", 0)
        activity  = confirmed.get("activity", "")
        venue     = confirmed.get("venue_name", "")
        meet_date = confirmed.get("date", "")
        narrative = outcome.get("narrative", "").replace("\n", "<br>")
        prob      = outcome.get("close_probability", 0)

        new_cards.append(f"""
        <div class="client-card new-client">
          <div class="client-header">
            <div>
              <div class="client-name">{em.get('fa_name','')}</div>
              <div class="client-firm">{em.get('firm','')} · {em.get('territory','')}</div>
            </div>
            <div class="client-deal">
              <div class="deal-value">{fmt_currency(deal_val)}</div>
              <div class="deal-label">New AUM · 2% of book</div>
            </div>
          </div>
          <div class="client-meeting">
            {activity_icon(activity)} {activity} @ {venue} &nbsp;·&nbsp;
            {meet_date} &nbsp;·&nbsp; Close prob: {prob*100:.0f}%
          </div>
          <div class="client-narrative">{narrative}</div>
        </div>""")

    crm_rows = "".join(f"""
        <tr>
          <td><strong>{c['name']}</strong></td>
          <td class="muted">{c['firm']}</td>
          <td class="muted">{c['territory']}</td>
          <td class="num">{fmt_currency(c.get('aum',0))}</td>
          <td class="num teal">{fmt_currency(c.get('oc_aum',0))}</td>
          <td class="num muted">{c.get('oc_clients',0)}</td>
          <td class="muted">Tier {c.get('tier','')}</td>
        </tr>"""
        for c in sorted(crm_clients, key=lambda x: x.get("oc_aum", 0), reverse=True))

    new_section = ""
    if new_cards:
        new_section = f"""
    <h3 class="section-heading">New Closes — This Cycle ({len(new_cards)} deals)</h3>
    {"".join(new_cards)}"""

    crm_section = ""
    if crm_rows:
        crm_section = f"""
    <h3 class="section-heading" style="margin-top:24px">All Clients — CRM Roster ({len(crm_clients)} FAs)</h3>
    <div class="summary-row">
      <span>Total OC AUM: <strong class="teal">{fmt_currency(total_oc_aum)}</strong></span>
      <span>New this cycle: <strong class="teal">{fmt_currency(total_new_aum)}</strong></span>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>FA Name</th><th>Firm</th><th>Territory</th>
          <th class="num">FA AUM</th><th class="num">OC AUM</th>
          <th class="num">OC Clients</th><th>Tier</th>
        </tr></thead>
        <tbody>{crm_rows}</tbody>
      </table>
    </div>"""

    return new_section + crm_section


# ── Debrief Tab ───────────────────────────────────────────────────────────

def build_debrief_tab(emails, debrief_report):
    debrief_email = next((e for e in emails if e.get("source") == "debrief"), None)

    if not debrief_email and not debrief_report:
        return """<div class="empty-state">
            <div class="empty-icon">📣</div>
            <div>No debrief yet. Complete the pipeline through Week 5.</div>
            <code>python3 venus.py --week 5</code>
        </div>"""

    subject = ""
    body    = ""
    sent_at = ""
    summary_html = ""

    if debrief_email:
        subject = debrief_email.get("subject", "")
        body    = debrief_email.get("body", "").replace("\n", "<br>")
        sent_at = debrief_email.get("sent_at", "")[:16].replace("T", " ")

    if debrief_report:
        cs         = debrief_report.get("cycle_summary", {})
        closes     = cs.get("closes", [])
        no_closes  = cs.get("no_closes", [])
        declined   = cs.get("declined", [])
        total_aum  = cs.get("total_new_aum", 0)
        close_rate = cs.get("close_rate", 0)

        if not subject:
            subject = debrief_report.get("debrief_subject", "Pipeline Debrief")
        if not body:
            body = debrief_report.get("debrief_body", "").replace("\n", "<br>")

        close_rows = "".join(f"""
          <tr>
            <td><strong>{c['fa_name']}</strong></td>
            <td class="muted">{c.get('firm','')}</td>
            <td>{activity_icon(c.get('meeting_activity',''))} {c.get('meeting_activity','')}</td>
            <td class="muted">{c.get('meeting_venue','')}</td>
            <td class="num teal">{fmt_currency(c.get('deal_value',0))}</td>
          </tr>""" for c in closes)

        no_close_rows = "".join(f"""
          <tr>
            <td><strong>{c['fa_name']}</strong></td>
            <td class="muted">{c.get('firm','')}</td>
            <td>{activity_icon(c.get('meeting_activity',''))} {c.get('meeting_activity','')}</td>
            <td class="muted">{c.get('meeting_venue','')}</td>
            <td class="num muted">{c.get('close_probability',0)*100:.0f}%</td>
          </tr>""" for c in no_closes)

        summary_html = f"""
        <div class="debrief-summary">
          <div class="debrief-stat"><div class="ds-val teal">{fmt_currency(total_aum)}</div><div class="ds-label">New AUM</div></div>
          <div class="debrief-stat"><div class="ds-val green">{len(closes)}</div><div class="ds-label">Closed</div></div>
          <div class="debrief-stat"><div class="ds-val muted">{len(no_closes)}</div><div class="ds-label">No Close</div></div>
          <div class="debrief-stat"><div class="ds-val red">{len(declined)}</div><div class="ds-label">Declined</div></div>
          <div class="debrief-stat"><div class="ds-val blue">{fmt_pct(close_rate)}</div><div class="ds-label">Close Rate</div></div>
        </div>"""

        if close_rows:
            summary_html += f"""
        <h3 class="section-heading">Closed Deals</h3>
        <div class="table-wrap">
          <table>
            <thead><tr><th>FA</th><th>Firm</th><th>Activity</th><th>Venue</th><th class="num">Deal Value</th></tr></thead>
            <tbody>{close_rows}</tbody>
          </table>
        </div>"""

        if no_close_rows:
            summary_html += f"""
        <h3 class="section-heading" style="margin-top:20px">Meetings — No Close</h3>
        <div class="table-wrap">
          <table>
            <thead><tr><th>FA</th><th>Firm</th><th>Activity</th><th>Venue</th><th class="num">Close Prob</th></tr></thead>
            <tbody>{no_close_rows}</tbody>
          </table>
        </div>"""

    return f"""
    {summary_html}
    <div class="debrief-email">
      <div class="debrief-header">
        <div class="debrief-from">💫 Venus &lt;venus@obsidiancapital.ai&gt; → Jay &lt;jay@obsidiancapital.ai&gt;</div>
        <div class="debrief-time">{sent_at}</div>
      </div>
      <div class="debrief-subject">Subject: {subject}</div>
      <div class="debrief-body">{body}</div>
    </div>"""


# ── Prospects Tab ─────────────────────────────────────────────────────────

def build_prospects_tab(emails):
    pe = pipeline_emails_only(emails)
    if not pe:
        return '<div class="empty-state">No prospects yet.</div>'

    rows = []
    for em in pe:
        week    = em.get("pipeline_week", 1)
        status  = em.get("pipeline_status", "")
        outcome = em.get("meeting_outcome", {})
        closed  = outcome.get("closed")
        activity = (em.get("meeting_confirmed", {}).get("activity")
                    or em.get("meeting_proposal", {}).get("activity", ""))

        if closed:
            sb = f'<span class="badge-closed">💰 {fmt_currency(outcome.get("deal_value",0))}</span>'
        elif status == "closed_no":
            sb = '<span class="badge-no">❌ Declined W2</span>'
        elif outcome and not closed:
            sb = '<span class="badge-no">❌ No close</span>'
        elif week >= 3:
            sb = '<span class="badge-confirmed">📅 Confirmed</span>'
        elif em.get("response") == "YES":
            sb = '<span class="badge-yes">✅ YES</span>'
        elif em.get("response") == "NO":
            sb = '<span class="badge-no">❌ NO</span>'
        else:
            sb = '<span class="badge-pending">⏳ Pending</span>'

        act_cell = f'{activity_icon(activity)} {activity}' if activity else '—'
        rows.append(f"""
        <tr>
          <td><strong>{em.get('fa_name','')}</strong></td>
          <td class="muted">{em.get('firm','')}</td>
          <td class="muted">{em.get('territory','')}</td>
          <td class="num">WK {week}</td>
          <td>{act_cell}</td>
          <td class="num">{em.get('prospect_score','—')}</td>
          <td>{sb}</td>
          <td class="muted">{em.get('sent_at','')[:10]}</td>
        </tr>""")

    return f"""
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>FA Name</th><th>Firm</th><th>Territory</th>
          <th class="num">Week</th><th>Activity</th>
          <th class="num">Score</th><th>Status</th><th>Sent</th>
        </tr></thead>
        <tbody>{"".join(rows)}</tbody>
      </table>
    </div>"""


# ── Full HTML ─────────────────────────────────────────────────────────────

def generate_html(emails, fund, crm_data, debrief_report, generated_at):
    scoreboard     = build_scoreboard(emails)
    dialogue_cards = build_dialogue_cards(emails)
    pipeline_tab   = build_pipeline_tab(emails)
    clients_tab    = build_clients_tab(emails, crm_data)
    debrief_tab    = build_debrief_tab(emails, debrief_report)
    prospects_tab  = build_prospects_tab(emails)

    ytd       = fund["performance"]["ytd"]
    fund_name = fund.get("fund_name", "OCRFF")
    nav       = fund.get("current_nav", 0)

    pe         = pipeline_emails_only(emails)
    new_closes = [e for e in pe if e.get("meeting_outcome", {}).get("closed")]

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>💫 Venus | Obsidian Capital</title>
<style>
  :root {{
    --bg:     #0d1117; --bg2: #161b22; --bg3: #1c2128;
    --border: #30363d; --border2: #21262d;
    --text:   #e6edf3; --muted: #7d8590;
    --blue:   #58a6ff; --green: #3fb950; --red: #f85149;
    --gold:   #e3b341; --purple: #bc8cff; --teal: #39c5cf;
    --venus:  #f778ba;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'SF Mono','Cascadia Code','Consolas',monospace;
          background: var(--bg); color: var(--text); font-size: 13px; line-height: 1.5; }}

  /* Header */
  .header {{ background: var(--bg2); border-bottom: 1px solid var(--border);
             padding: 16px 24px; display: flex; align-items: center;
             justify-content: space-between; position: sticky; top: 0; z-index: 100; }}
  .header-left  {{ display: flex; align-items: center; gap: 14px; }}
  .venus-icon   {{ font-size: 28px; }}
  .header-title {{ font-size: 18px; font-weight: 700; color: var(--venus); }}
  .header-sub   {{ font-size: 11px; color: var(--muted); margin-top: 2px; }}
  .header-right {{ font-size: 11px; color: var(--muted); text-align: right; line-height: 1.7; }}
  .header-right strong {{ color: var(--text); }}

  /* Fund banner */
  .fund-banner {{ background: var(--bg3); border-bottom: 1px solid var(--border);
                  padding: 8px 24px; display: flex; align-items: center;
                  gap: 24px; font-size: 12px; flex-wrap: wrap; }}
  .fund-stat  {{ display: flex; align-items: center; gap: 6px; }}
  .fund-label {{ color: var(--muted); }}
  .fund-val   {{ color: var(--teal); font-weight: 700; }}
  .fund-vs    {{ color: var(--muted); }}

  /* Scoreboard */
  .scoreboard {{ display: flex; gap: 10px; padding: 16px 24px; flex-wrap: wrap;
                 border-bottom: 1px solid var(--border); background: var(--bg2); }}
  .score-card {{ background: var(--bg3); border: 1px solid var(--border);
                 border-radius: 8px; padding: 10px 14px; min-width: 100px;
                 border-top: 2px solid var(--border); }}
  .score-card.green  {{ border-top-color: var(--green); }}
  .score-card.red    {{ border-top-color: var(--red); }}
  .score-card.gold   {{ border-top-color: var(--gold); }}
  .score-card.blue   {{ border-top-color: var(--blue); }}
  .score-card.purple {{ border-top-color: var(--purple); }}
  .score-card.teal   {{ border-top-color: var(--teal); }}
  .score-label {{ font-size: 9px; color: var(--muted); text-transform: uppercase; letter-spacing: .07em; }}
  .score-value {{ font-size: 20px; font-weight: 700; margin-top: 4px; }}

  /* Tabs */
  .tabs {{ display: flex; border-bottom: 1px solid var(--border); padding: 0 24px;
           background: var(--bg2); overflow-x: auto; }}
  .tab {{ padding: 10px 18px; font-size: 11px; font-weight: 600; color: var(--muted);
          cursor: pointer; border-bottom: 2px solid transparent; text-transform: uppercase;
          letter-spacing: .06em; background: none; border-top: none; border-left: none;
          border-right: none; font-family: inherit; transition: color .15s; white-space: nowrap; }}
  .tab:hover {{ color: var(--text); }}
  .tab.active {{ color: var(--venus); border-bottom-color: var(--venus); }}
  .panel {{ display: none; padding: 20px 24px; }}
  .panel.active {{ display: block; }}

  /* Orbit alert cards */
  .card-orbit-alert {{
    border-left: 3px solid var(--red);
    background: rgba(248,81,73,0.04);
  }}

  /* Prospect cards */
  .prospect-card {{ background: var(--bg2); border: 1px solid var(--border);
                    border-radius: 10px; margin-bottom: 20px; overflow: hidden; }}
  .card-yes     {{ border-left: 3px solid var(--green); }}
  .card-no      {{ border-left: 3px solid var(--red); }}
  .card-pending {{ border-left: 3px solid var(--gold); }}
  .card-closed  {{ border-left: 3px solid var(--teal); }}
  .card-header  {{ display: flex; justify-content: space-between; align-items: flex-start;
                   padding: 14px 16px 10px; border-bottom: 1px solid var(--border2); }}
  .card-title   {{ display: flex; flex-direction: column; gap: 3px; }}
  .fa-name      {{ font-weight: 700; font-size: 14px; color: var(--blue); }}
  .fa-firm, .fa-territory {{ font-size: 12px; color: var(--muted); }}
  .card-meta    {{ display: flex; flex-direction: column; align-items: flex-end; gap: 4px; }}
  .week-pill    {{ font-size: 9px; font-weight: 700; background: var(--bg3);
                   border: 1px solid var(--border); border-radius: 4px; padding: 2px 6px;
                   color: var(--muted); letter-spacing: .05em; }}
  .strategy-note {{ padding: 6px 16px; font-size: 11px; color: var(--gold);
                    border-bottom: 1px solid var(--border2); background: rgba(227,179,65,.05); }}

  /* Message thread */
  .dialogue {{ padding: 16px; display: flex; flex-direction: column; gap: 12px; }}
  .message-block {{ border-radius: 8px; padding: 12px 14px; max-width: 90%; }}
  .venus-message {{ background: #1a1030; border: 1px solid #3d2060; align-self: flex-start; }}
  .fa-message    {{ background: #0d2018; border: 1px solid #1a4030; align-self: flex-end; }}
  .fa-pending    {{ background: var(--bg3); border: 1px dashed var(--border);
                    align-self: flex-end; font-style: italic; }}
  .narrative-block  {{ max-width: 100%; align-self: stretch; }}
  .narrative-closed {{ background: rgba(57,197,207,.07); border: 1px solid rgba(57,197,207,.25); }}
  .narrative-lost   {{ background: rgba(248,81,73,.05);  border: 1px solid rgba(248,81,73,.15); }}
  .msg-header   {{ display: flex; justify-content: space-between; margin-bottom: 6px; font-size: 11px; }}
  .msg-from     {{ color: var(--venus); font-weight: 600; }}
  .fa-message .msg-from     {{ color: var(--green); }}
  .narrative-block .msg-from {{ color: var(--teal); }}
  .msg-time     {{ color: var(--muted); }}
  .msg-subject  {{ font-size: 11px; color: var(--gold); margin-bottom: 6px; }}
  .msg-activity {{ font-size: 11px; color: var(--purple); margin-bottom: 6px; font-weight: 600; }}
  .msg-body     {{ font-size: 12px; line-height: 1.65; color: var(--text); }}
  .msg-odds     {{ font-size: 10px; color: var(--muted); }}
  .narrative-outcome {{ display: flex; align-items: center; gap: 12px; margin-bottom: 10px; flex-wrap: wrap; }}
  .narrative-text {{ font-size: 12px; line-height: 1.75; color: #c9d1d9; font-style: italic;
                     border-top: 1px solid var(--border2); padding-top: 10px; margin-top: 4px; }}

  /* Badges */
  .badge-yes, .badge-no, .badge-pending, .badge-closed, .badge-confirmed, .badge-week2 {{
    font-weight: 700; font-size: 11px; }}
  .badge-yes       {{ color: var(--green); }}
  .badge-no        {{ color: var(--red); }}
  .badge-pending   {{ color: var(--gold); }}
  .badge-closed    {{ color: var(--teal); }}
  .badge-confirmed {{ color: var(--purple); }}
  .badge-week2     {{ color: var(--blue); }}
  .score-badge     {{ font-size: 10px; color: var(--muted); }}

  /* Kanban */
  .kanban {{ display: flex; gap: 14px; overflow-x: auto; padding-bottom: 8px; }}
  .kanban-col {{ flex: 0 0 200px; min-width: 180px; }}
  .kanban-header {{ border-top: 2px solid var(--border); padding: 8px 0 10px;
                    margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center; }}
  .kanban-label {{ font-size: 10px; font-weight: 700; text-transform: uppercase;
                   letter-spacing: .07em; color: var(--muted); }}
  .kanban-count {{ font-size: 11px; color: var(--muted); background: var(--bg3);
                   border: 1px solid var(--border); border-radius: 10px; padding: 1px 7px; }}
  .kanban-card {{ background: var(--bg2); border: 1px solid var(--border);
                  border-radius: 7px; padding: 10px 12px; margin-bottom: 8px;
                  display: flex; flex-direction: column; gap: 4px; }}
  .ki-name      {{ font-weight: 700; font-size: 12px; color: var(--blue); }}
  .ki-firm      {{ font-size: 10px; color: var(--muted); }}
  .ki-activity  {{ font-size: 10px; color: var(--purple); }}
  .ki-yes       {{ font-size: 10px; color: var(--green); font-weight: 600; }}
  .ki-lost      {{ font-size: 10px; color: var(--red); font-weight: 600; }}
  .ki-pending   {{ font-size: 10px; color: var(--gold); }}
  .ki-confirmed {{ font-size: 10px; color: var(--purple); font-weight: 600; }}
  .ki-closed    {{ font-size: 10px; color: var(--teal); font-weight: 700; }}
  .ki-empty     {{ font-size: 11px; color: var(--border); padding: 8px 0; }}

  /* Clients tab */
  .client-card {{ background: var(--bg2); border: 1px solid var(--border);
                  border-radius: 10px; padding: 16px; margin-bottom: 16px; }}
  .new-client  {{ border-left: 3px solid var(--teal); }}
  .client-header {{ display: flex; justify-content: space-between;
                    align-items: flex-start; margin-bottom: 10px; }}
  .client-name {{ font-weight: 700; font-size: 14px; color: var(--blue); }}
  .client-firm {{ font-size: 11px; color: var(--muted); margin-top: 2px; }}
  .client-deal {{ text-align: right; }}
  .deal-value  {{ font-size: 18px; font-weight: 700; color: var(--teal); }}
  .deal-label  {{ font-size: 10px; color: var(--muted); margin-top: 2px; }}
  .client-meeting {{ font-size: 11px; color: var(--purple); margin-bottom: 12px;
                     padding-bottom: 10px; border-bottom: 1px solid var(--border2); }}
  .client-narrative {{ font-size: 12px; line-height: 1.75; color: #c9d1d9; font-style: italic; }}
  .section-heading {{ font-size: 11px; text-transform: uppercase; letter-spacing: .08em;
                      color: var(--muted); margin-bottom: 12px; }}
  .summary-row {{ display: flex; gap: 24px; font-size: 12px; color: var(--muted); margin-bottom: 12px; }}
  .teal  {{ color: var(--teal); }}
  .green {{ color: var(--green); }}
  .red   {{ color: var(--red); }}
  .blue  {{ color: var(--blue); }}
  .muted {{ color: var(--muted); }}

  /* Debrief tab */
  .debrief-summary {{ display: flex; gap: 20px; padding: 16px 0 20px;
                      border-bottom: 1px solid var(--border); margin-bottom: 20px; flex-wrap: wrap; }}
  .debrief-stat  {{ text-align: center; min-width: 80px; }}
  .ds-val        {{ font-size: 22px; font-weight: 700; }}
  .ds-label      {{ font-size: 10px; color: var(--muted); text-transform: uppercase;
                    letter-spacing: .07em; margin-top: 2px; }}
  .debrief-email {{ background: #1a1030; border: 1px solid #3d2060; border-radius: 10px;
                    padding: 20px; margin-top: 20px; max-width: 780px; }}
  .debrief-header {{ display: flex; justify-content: space-between; font-size: 11px; margin-bottom: 8px; }}
  .debrief-from   {{ color: var(--venus); font-weight: 600; }}
  .debrief-time   {{ color: var(--muted); }}
  .debrief-subject {{ font-size: 12px; color: var(--gold); margin-bottom: 14px;
                      padding-bottom: 10px; border-bottom: 1px solid #3d2060; }}
  .debrief-body   {{ font-size: 13px; line-height: 1.8; color: var(--text); }}

  /* Table */
  .table-wrap {{ border: 1px solid var(--border); border-radius: 8px; overflow-x: auto; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  thead tr {{ background: var(--bg3); }}
  th {{ padding: 9px 12px; text-align: left; color: var(--muted); font-size: 10px;
        text-transform: uppercase; letter-spacing: .07em; border-bottom: 1px solid var(--border); }}
  td {{ padding: 9px 12px; border-bottom: 1px solid var(--border2); }}
  tbody tr:last-child td {{ border-bottom: none; }}
  tbody tr:hover td {{ background: var(--bg3); }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}

  /* Empty state */
  .empty-state {{ text-align: center; padding: 60px 20px; color: var(--muted); }}
  .empty-icon  {{ font-size: 40px; margin-bottom: 12px; }}
  .empty-state code {{ display: block; margin-top: 12px; background: var(--bg2);
                       padding: 8px 16px; border-radius: 6px; font-size: 12px; color: var(--venus); }}

  /* Footer */
  .footer {{ padding: 16px 24px; font-size: 10px; color: var(--muted);
             border-top: 1px solid var(--border2); margin-top: 24px; }}
</style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <div class="venus-icon">💫</div>
    <div>
      <div class="header-title">Venus — AI Sales Agent</div>
      <div class="header-sub">Obsidian Capital &nbsp;·&nbsp; FA Outreach &amp; Meeting Pipeline</div>
    </div>
  </div>
  <div class="header-right">
    <div>Generated: <strong>{generated_at}</strong></div>
    <div>Emails in outbox: <strong>{len(pe)}</strong></div>
    {'<div>New closes: <strong style="color:var(--teal)">' + str(len(new_closes)) + '</strong></div>' if new_closes else ''}
  </div>
</div>

<div class="fund-banner">
  <span style="color:var(--venus);font-weight:700">{fund_name}</span>
  <div class="fund-stat"><span class="fund-label">YTD:</span>
    <span class="fund-val">+{ytd['ocrff']}%</span>
    <span class="fund-vs">vs S&amp;P +{ytd['sp500']}%</span></div>
  <div class="fund-stat"><span class="fund-label">Alpha YTD:</span>
    <span class="fund-val">+{ytd['alpha']}%</span></div>
  <div class="fund-stat"><span class="fund-label">NAV:</span>
    <span class="fund-val">${nav:.2f}</span></div>
</div>

{scoreboard}

<div class="tabs">
  <button class="tab active" onclick="showTab('dialogue',this)">💬 Dialogue</button>
  <button class="tab" onclick="showTab('pipeline',this)">📊 Pipeline</button>
  <button class="tab" onclick="showTab('clients',this)">🏆 Clients</button>
  <button class="tab" onclick="showTab('debrief',this)">📣 Debrief</button>
  <button class="tab" onclick="showTab('prospects',this)">📋 Prospects</button>
</div>

<div id="dialogue" class="panel active">{dialogue_cards}</div>
<div id="pipeline" class="panel">{pipeline_tab}</div>
<div id="clients" class="panel">{clients_tab}</div>
<div id="debrief" class="panel">{debrief_tab}</div>
<div id="prospects" class="panel">{prospects_tab}</div>

<div class="footer">
  Venus AI Sales Agent &nbsp;·&nbsp; Obsidian Capital &nbsp;·&nbsp;
  Rebuild: <code>python3 build_venus_dashboard.py</code>
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


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Venus Dashboard Builder")
    parser.add_argument("--output", default=OUTPUT_FILE)
    args = parser.parse_args()

    print("=" * 55)
    print(" 💫 Venus Dashboard Builder")
    print(f"    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 55)

    fund     = safe_load(FUND_FILE) or {
        "performance": {"ytd": {"ocrff": 0, "sp500": 0, "alpha": 0}},
        "fund_name": "OCRFF", "current_nav": 0
    }
    emails   = load_all_emails()
    crm_data = safe_load(CRM_FILE)
    debrief  = load_latest_debrief()

    pe     = pipeline_emails_only(emails)
    yes_n  = sum(1 for e in pe if e.get("response") == "YES")
    no_n   = sum(1 for e in pe if e.get("response") == "NO")
    pend   = sum(1 for e in pe if e.get("response") is None)
    closed = sum(1 for e in pe if e.get("meeting_outcome", {}).get("closed"))

    print(f"\n📧 Pipeline emails : {len(pe)}")
    print(f"   ✅ YES: {yes_n}  ❌ NO: {no_n}  ⏳ Pending: {pend}")
    print(f"   💰 Closed: {closed}")
    if crm_data:
        clients = [a for a in crm_data.get("advisors", []) if a["status"] == "CLIENT"]
        print(f"   🏆 CRM Clients: {len(clients)}")
    if debrief:
        print(f"   📣 Debrief: {debrief['report_date'][:10]}")

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    html = generate_html(emails, fund, crm_data, debrief, generated_at)

    with open(args.output, "w") as f:
        f.write(html)

    size_kb = os.path.getsize(args.output) // 1024
    print(f"\n✅ Dashboard: {args.output}  ({size_kb} KB)")


if __name__ == "__main__":
    main()
