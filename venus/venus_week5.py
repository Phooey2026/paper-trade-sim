#!/usr/bin/env python3
"""
venus_week5.py — Pipeline Week 5: Venus Debrief to Jay
Imported and called by venus.py --week 5

Venus reads all Week 4 outcomes from the current pipeline cycle,
generates an enthusiastic spoken-word-style debrief email to Jay
covering: closes, near-misses, total AUM added, what worked, 
what didn't, and recommended next moves.

Also writes venus_pipeline_report_[date].json for dashboard use.
Marks completed pipeline records as pipeline_week=5.
"""

import json
import os
import glob
import subprocess
# ollama_client provides Hermes→Ollama fallback for Mac compatibility
# ollama_client.py lives in paper_trade/ (parent of venus/)
try:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from ollama_client import call_llm_hermes as _call_llm
    _USE_OLLAMA = True
except ImportError:
    _USE_OLLAMA = False

from datetime import datetime

SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
OUTBOX_DIR    = os.path.join(SCRIPT_DIR, "venus_outbox")
PIPELINE_FILE = os.path.join(SCRIPT_DIR, "pipeline_state.json")
CRM_FILE      = os.path.join(SCRIPT_DIR, "crm_advisors.json")
REPORTS_DIR   = os.path.join(SCRIPT_DIR, "pipeline_reports")
JAY_EMAIL     = "jay@obsidiancapital.ai"
VENUS_EMAIL   = "venus@obsidiancapital.ai"

os.makedirs(REPORTS_DIR, exist_ok=True)


def load_pipeline_state():
    if os.path.exists(PIPELINE_FILE):
        with open(PIPELINE_FILE) as f:
            return json.load(f)
    return {"cycles": [], "last_updated": None}


def save_pipeline_state(state):
    state["last_updated"] = datetime.now().isoformat()
    with open(PIPELINE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def upsert_pipeline_record(state, fa_id, email_id, updates):
    for record in state["cycles"]:
        if record["email_id"] == email_id:
            record.update(updates)
            return
    state["cycles"].append({"email_id": email_id, "fa_id": fa_id, **updates})


def build_debrief_prompt(cycle_summary):
    """Build Venus's debrief email prompt."""
    closes     = cycle_summary["closes"]
    no_closes  = cycle_summary["no_closes"]
    nos        = cycle_summary["declined"]
    total_aum  = cycle_summary["total_new_aum"]
    close_rate = cycle_summary["close_rate"]

    # Closed deals narrative
    close_lines = []
    for c in closes:
        close_lines.append(
            f"  - {c['fa_name']} ({c['firm']}, {c['territory']}): "
            f"${c['deal_value']:,.0f} via {c.get('meeting_activity','meeting')} "
            f"at {c.get('meeting_venue','TBD')} — {c.get('narrative_snippet','')}"
        )

    no_close_lines = []
    for c in no_closes:
        no_close_lines.append(
            f"  - {c['fa_name']} ({c['firm']}): {c.get('meeting_activity','meeting')} "
            f"at {c.get('meeting_venue','TBD')} — close prob was {c['close_probability']*100:.0f}% — didn't commit today"
        )

    declined_lines = []
    for d in nos:
        declined_lines.append(f"  - {d['fa_name']} ({d['firm']}): declined in Week 2")

    close_block    = "\n".join(close_lines)    or "  None this cycle"
    no_close_block = "\n".join(no_close_lines) or "  None"
    declined_block = "\n".join(declined_lines) or "  None"

    prompt = f"""You are Venus, AI sales assistant for Obsidian Capital.

You just completed a full 5-week sales pipeline cycle. It's time to call Jay — 
but since he's out in the field, you're sending a voice-memo-style email instead.

CYCLE RESULTS:
CLOSED DEALS ({len(closes)}):
{close_block}

NO CLOSE — met but didn't commit ({len(no_closes)}):
{no_close_block}

DECLINED IN WEEK 2 ({len(nos)}):
{declined_block}

TOTALS:
- Total new AUM this cycle: ${total_aum:,.0f}
- Close rate (meetings to close): {close_rate:.1f}%
- Prospects contacted: {cycle_summary['total_contacted']}

YOUR TASK:
Write a 300-400 word debrief email to Jay. This is Venus calling Jay right after 
the last meeting closed — excited, professional, specific.

TONE & STYLE:
- Voice-memo energy: like you're talking out loud, not writing a report
- Lead with the good news first (closes) — be genuinely enthusiastic on wins
- Be specific: name the FA, the venue, what clicked
- For no-closes: be honest about why and what you'd do differently
- End with 2-3 specific recommendations for the next cycle (who to re-engage, 
  what activity worked best, any patterns you noticed)
- Sign as: — Venus | Obsidian Capital Sales

SUBJECT LINE: Make it feel urgent and personal, like a voice message subject.

RESPOND WITH ONLY:
SUBJECT: [subject line]
BODY:
[email body]"""

    return prompt


def run(dry_run=False, verbose=False):
    print("\n📣 Week 5: Venus pipeline debrief...")

    with open(CRM_FILE) as f:
        crm = json.load(f)
    advisors = {a["fa_id"]: a for a in crm["advisors"]}
    state = load_pipeline_state()

    # Gather all Week 4 completed records
    email_files = sorted(glob.glob(os.path.join(OUTBOX_DIR, "email_*.json")))
    eligible    = []
    for ef in email_files:
        try:
            with open(ef) as f:
                rec = json.load(f)
            if rec.get("pipeline_week") == 4 and rec.get("source") != "orbit_alert":
                eligible.append((ef, rec))
        except Exception:
            pass

    # Also gather Week 5 closed_no records (declined in Week 2)
    declined_records = []
    for ef in email_files:
        try:
            with open(ef) as f:
                rec = json.load(f)
            if (rec.get("pipeline_week") == 5
                    and rec.get("pipeline_status") == "closed_no"
                    and rec.get("source") != "orbit_alert"):
                declined_records.append(rec)
        except Exception:
            pass

    if not eligible and not declined_records:
        print("   ℹ️  No completed pipeline records found.")
        print("      Run --week 4 first, or check that meetings have been simulated.")
        return 0

    # Build cycle summary
    closes    = []
    no_closes = []
    declined  = []

    for _, rec in eligible:
        fa_id   = rec["fa_id"]
        fa      = advisors.get(fa_id, {})
        outcome = rec.get("meeting_outcome", {})
        conf    = rec.get("meeting_confirmed", {})

        # Find pipeline state record for this email
        pipe_rec = next((r for r in state["cycles"] if r["email_id"] == rec["email_id"]), {})

        entry = {
            "fa_id":            fa_id,
            "fa_name":          fa.get("name", rec.get("fa_name", "")),
            "firm":             fa.get("firm", rec.get("firm", "")),
            "territory":        fa.get("territory", ""),
            "deal_value":       outcome.get("deal_value", 0),
            "close_probability": outcome.get("close_probability", 0),
            "meeting_activity": conf.get("activity", pipe_rec.get("meeting_activity", "")),
            "meeting_venue":    conf.get("venue_name", pipe_rec.get("meeting_venue", "")),
            "narrative":        outcome.get("narrative", ""),
            "narrative_snippet": outcome.get("narrative", "")[:120],
        }

        if outcome.get("closed"):
            closes.append(entry)
        else:
            no_closes.append(entry)

    for rec in declined_records:
        fa_id = rec["fa_id"]
        fa    = advisors.get(fa_id, {})
        declined.append({
            "fa_id":   fa_id,
            "fa_name": fa.get("name", rec.get("fa_name", "")),
            "firm":    fa.get("firm", rec.get("firm", "")),
        })

    total_new_aum    = sum(c["deal_value"] for c in closes)
    total_contacted  = len(eligible) + len(declined_records)
    meetings_held    = len(eligible)
    close_rate       = (len(closes) / meetings_held * 100) if meetings_held > 0 else 0.0

    cycle_summary = {
        "cycle_date":       datetime.now().strftime("%Y-%m-%d"),
        "total_contacted":  total_contacted,
        "meetings_held":    meetings_held,
        "closes":           closes,
        "no_closes":        no_closes,
        "declined":         declined,
        "total_new_aum":    total_new_aum,
        "close_rate":       close_rate,
    }

    print(f"   Cycle summary:")
    print(f"     Contacted  : {total_contacted}")
    print(f"     Meetings   : {meetings_held}")
    print(f"     Closed     : {len(closes)}  (${total_new_aum:,.0f} new AUM)")
    print(f"     No close   : {len(no_closes)}")
    print(f"     Declined   : {len(declined)}")
    print(f"     Close rate : {close_rate:.1f}%")

    # Generate Venus debrief email via LLM
    print(f"\n   ✍️  Venus drafting debrief email to Jay...")
    prompt = build_debrief_prompt(cycle_summary)

    try:
        if _USE_OLLAMA:
            raw = _call_llm("venus", prompt)
        else:
            result = subprocess.run(
                ["venus", "-z", prompt, "--ignore-rules"],
                capture_output=True, text=True, timeout=180
            )
            raw = result.stdout.strip()
    except Exception as e:
        print(f"   ⚠️  LLM failed: {e} — using summary template")
        raw = _fallback_debrief(cycle_summary)

    # Parse subject/body
    lines = raw.strip().split('\n')
    subject = ""
    body_lines = []
    in_body = False
    for line in lines:
        if line.startswith("SUBJECT:"):
            subject = line.replace("SUBJECT:", "").strip()
        elif line.startswith("BODY:"):
            in_body = True
        elif in_body:
            body_lines.append(line)
    subject = subject or f"Pipeline debrief — {datetime.now().strftime('%B %d')}"
    body    = "\n".join(body_lines).strip() or raw

    print(f"\n   📧 Subject: {subject}")
    if verbose:
        print(f"\n{body}\n")

    # Build report
    report = {
        "report_date":    datetime.now().isoformat(),
        "cycle_summary":  cycle_summary,
        "debrief_subject": subject,
        "debrief_body":   body,
        "generated_by":   "venus_week5",
    }

    if not dry_run:
        # Save pipeline report
        date_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_file = os.path.join(REPORTS_DIR, f"pipeline_report_{date_tag}.json")
        with open(report_file, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\n   💾 Report saved: {report_file}")

        # Save debrief email to outbox so it appears in Venus dashboard
        debrief_email = {
            "email_id":    f"VNS-DEBRIEF-{date_tag}",
            "fa_id":       "INTERNAL",
            "fa_name":     "Jay — Obsidian Capital",
            "firm":        "Obsidian Capital",
            "territory":   "Internal",
            "to_email":    JAY_EMAIL,
            "from_email":  VENUS_EMAIL,
            "subject":     subject,
            "body":        body,
            "sent_at":     datetime.now().isoformat(),
            "status":      "sent",
            "source":      "debrief",
            "pipeline_week": 5,
            "response":    None,
        }
        debrief_file = os.path.join(OUTBOX_DIR, f"email_{date_tag}_DEBRIEF.json")
        with open(debrief_file, "w") as f:
            json.dump(debrief_email, f, indent=2)

        # Advance all Week 4 records to Week 5 / complete
        for email_file, rec in eligible:
            rec["pipeline_week"]   = 5
            rec["pipeline_status"] = "complete"
            rec["week5_at"]        = datetime.now().isoformat()
            with open(email_file, "w") as f:
                json.dump(rec, f, indent=2)

            upsert_pipeline_record(state, rec["fa_id"], rec["email_id"], {
                "pipeline_week":   5,
                "pipeline_status": "complete",
                "week5_at":        datetime.now().isoformat(),
            })

        save_pipeline_state(state)
        print(f"   ✅ Pipeline cycle complete — all records marked week=5")
        print(f"\n   🎉 New AUM added to VMRXX: ${total_new_aum:,.0f}")
        print(f"   ▶️  Run fetch_prospects.py to start the next cycle")

    return len(eligible)


def _fallback_debrief(summary):
    """Simple text debrief if LLM is unavailable."""
    closes    = summary["closes"]
    no_closes = summary["no_closes"]
    total_aum = summary["total_new_aum"]
    date_str  = datetime.now().strftime("%B %d, %Y")

    lines = [
        f"SUBJECT: Pipeline debrief — {date_str} — {len(closes)} closed, ${total_aum:,.0f} new AUM",
        "BODY:",
        f"Jay — Venus here. Quick debrief on this cycle ({date_str}).",
        "",
    ]
    if closes:
        lines.append(f"Great news — we closed {len(closes)} deal(s) this cycle:")
        for c in closes:
            lines.append(f"  • {c['fa_name']} at {c['firm']}: ${c['deal_value']:,.0f} via {c['meeting_activity']} at {c['meeting_venue']}")
        lines.append(f"Total new AUM: ${total_aum:,.0f} — all added to VMRXX.")
        lines.append("")
    if no_closes:
        lines.append(f"We met with {len(no_closes)} FA(s) who didn't commit this time:")
        for c in no_closes:
            lines.append(f"  • {c['fa_name']}: {c['meeting_activity']} at {c['meeting_venue']} — worth a follow-up next cycle")
        lines.append("")
    lines.append("More soon. — Venus | Obsidian Capital")
    return "\n".join(lines)
