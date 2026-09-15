#!/usr/bin/env python3
"""
venus_week3.py — Pipeline Week 3: FA Agent Confirms Meeting
Imported and called by venus.py --week 3

For each outbox email at pipeline_week == 2:
  - FA Agent reads Venus's meeting proposal
  - Consults the persisted FA calendar (fa_calendars/FA-XXXX_calendar.json)
  - Confirms one of the offered time slots
  - Writes meeting_confirmed block back to the outbox record
  - Updates pipeline_state.json

The FA agent plays it realistic: they may ask to shift the time slightly,
confirm enthusiastically, or ask a follow-up question about the fund.
"""

import json
import os
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

import random
from datetime import datetime

SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
OUTBOX_DIR    = os.path.join(SCRIPT_DIR, "venus_outbox")
CALENDARS_DIR = os.path.join(SCRIPT_DIR, "fa_calendars")
PIPELINE_FILE = os.path.join(SCRIPT_DIR, "pipeline_state.json")
CRM_FILE      = os.path.join(SCRIPT_DIR, "crm_advisors.json")


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


def load_fa_calendar(fa_id):
    cal_file = os.path.join(CALENDARS_DIR, f"{fa_id}_calendar.json")
    if os.path.exists(cal_file):
        with open(cal_file) as f:
            return json.load(f)
    return None


def build_confirmation_prompt(rec, fa, calendar):
    """
    Build the FA Agent prompt to confirm a meeting slot.
    We pre-select the slot in Python to avoid LLM recency bias
    (models tend to always pick the last option offered).
    The FA Agent just needs to write a natural confirmation reply.
    """
    import random as _random

    proposal    = rec.get("meeting_proposal", {})
    activity    = proposal.get("activity", "Coffee")
    venue_name  = proposal.get("venue_name", "a local spot")
    open_slots  = proposal.get("open_slots", [])
    follow_up   = rec.get("follow_up_body", "")
    fa_reply    = rec.get("response_body", "")

    # Pre-select the slot randomly — don't let the LLM decide, it always picks the last one
    selected_slot = _random.choice(open_slots) if open_slots else None
    confirmed_day  = selected_slot["day"]  if selected_slot else "Monday"
    confirmed_date = selected_slot["date"] if selected_slot else ""
    confirmed_time = selected_slot["time"] if selected_slot else "10:30 AM"

    # Calendar summary for context
    cal_summary = ""
    if calendar:
        cal_entries = []
        for day, events in calendar.get("calendar", {}).items():
            for ev in events:
                cal_entries.append(f"  {day} {ev['time']}: {ev['title']}")
        cal_summary = "\nYOUR CALENDAR THIS WEEK:\n" + "\n".join(cal_entries[:12])

    is_assistant = fa.get("contact_via_assistant") and fa.get("assistant_name")
    if is_assistant:
        responder = f"You are {fa['assistant_name']}, assistant to {fa['name']} at {fa['firm']}."
        role = f"You are confirming a meeting time on behalf of {fa['first_name']}."
    else:
        responder = f"You are {fa['name']}, financial advisor at {fa['firm']}."
        role = "You are confirming a meeting time directly."

    prompt = f"""{responder}
{role}

CONTEXT:
Venus from Obsidian Capital reached out and you/your boss expressed interest.
Venus followed up proposing a face-to-face meeting with Jay.

VENUS'S FOLLOW-UP EMAIL:
{follow_up}

YOUR PREVIOUS REPLY (for context):
{fa_reply}

MEETING CONFIRMED:
Activity: {activity} at {venue_name}
You are available on {confirmed_day}, {confirmed_date} at {confirmed_time}.
{cal_summary}

YOUR TASK:
Write a brief, natural confirmation reply as {fa['first_name']} (or their assistant).
Confirm {confirmed_day} at {confirmed_time}. You may ask one brief logistics question
(parking, dress code, what to bring). Mention you're looking forward to meeting Jay.

RESPOND IN THIS FORMAT:
CONFIRMED_SLOT: {confirmed_day}, {confirmed_date} at {confirmed_time}
REPLY:
[2-4 sentence confirmation email, in character]"""

    return prompt, selected_slot


def call_fa_llm(prompt, verbose=False):
    """Call FA agent LLM via Hermes."""
    cmd = ["fa", "-z", prompt, "--ignore-rules"]
    if verbose:
        print("   [LLM] FA Agent confirming meeting...")
    try:
        if _USE_OLLAMA:
            return _call_llm("fa", prompt)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return "CONFIRMED_SLOT: Monday at 10:30 AM\nREPLY:\nSounds great, I'll put it in the calendar. Looking forward to it."
    except FileNotFoundError:
        return "CONFIRMED_SLOT: Monday at 10:30 AM\nREPLY:\n[Hermes not available — using fallback]"


def parse_confirmation(raw, open_slots):
    """Parse FA Agent's confirmation response."""
    lines        = raw.strip().split('\n')
    confirmed_slot_raw = ""
    reply_lines  = []
    in_reply     = False

    for line in lines:
        if line.startswith("CONFIRMED_SLOT:"):
            confirmed_slot_raw = line.replace("CONFIRMED_SLOT:", "").strip()
        elif line.startswith("REPLY:"):
            in_reply = True
        elif in_reply:
            reply_lines.append(line)

    reply = "\n".join(reply_lines).strip() or raw

    # Try to match against offered slots
    confirmed_slot = None
    for slot in open_slots:
        if slot["day"].lower() in confirmed_slot_raw.lower():
            confirmed_slot = slot
            break

    # Fallback: just pick first slot
    if not confirmed_slot and open_slots:
        confirmed_slot = open_slots[0]

    return confirmed_slot, reply


def run(dry_run=False, verbose=False):
    import glob

    print("\n📅 Week 3: FA Agent confirming meeting slots...")

    with open(CRM_FILE) as f:
        crm = json.load(f)
    advisors = {a["fa_id"]: a for a in crm["advisors"]}
    state = load_pipeline_state()

    # Find all Week 2 emails (YES path, meeting proposed, awaiting confirmation)
    email_files = sorted(glob.glob(os.path.join(OUTBOX_DIR, "email_*.json")))
    eligible    = []
    for ef in email_files:
        try:
            with open(ef) as f:
                rec = json.load(f)
            if (rec.get("pipeline_week") == 2
                    and rec.get("meeting_proposal")
                    and rec.get("source") != "orbit_alert"):
                eligible.append((ef, rec))
        except Exception:
            pass

    if not eligible:
        print("   ℹ️  No Week 2 emails awaiting confirmation.")
        print("      Run: python3 venus.py --week 2   first")
        return 0

    print(f"   Found {len(eligible)} email(s) ready for Week 3\n")
    processed = 0

    for email_file, rec in eligible:
        fa_id    = rec["fa_id"]
        fa       = advisors.get(fa_id)
        if not fa:
            print(f"   ❌ FA {fa_id} not found — skipping")
            continue

        proposal   = rec.get("meeting_proposal", {})
        activity   = proposal.get("activity", "Coffee")
        venue_name = proposal.get("venue_name", "")
        open_slots = proposal.get("open_slots", [])

        print(f"   {'─'*55}")
        print(f"   {fa['name']} | {fa['firm']}")
        print(f"   Activity: {activity} @ {venue_name}")

        calendar = load_fa_calendar(fa_id)
        if not calendar:
            print(f"   ⚠️  No calendar found for {fa_id} — using open_slots directly")

        prompt, pre_selected_slot = build_confirmation_prompt(rec, fa, calendar)
        raw    = call_fa_llm(prompt, verbose=verbose)

        if verbose:
            print(f"\n   [Raw FA Confirmation]\n{raw}\n")

        # Use pre-selected slot (avoids LLM recency bias)
        # parse_confirmation still extracts the reply text
        _, reply = parse_confirmation(raw, open_slots)
        confirmed_slot = pre_selected_slot or (open_slots[0] if open_slots else None)

        print(f"   ✅ Confirmed: {confirmed_slot['day'] if confirmed_slot else 'Unknown'} "
              f"at {confirmed_slot['time'] if confirmed_slot else 'TBD'}")
        print(f"   Reply: {reply[:120]}{'...' if len(reply)>120 else ''}")

        meeting_confirmed = {
            "date":         confirmed_slot.get("date", "") if confirmed_slot else "",
            "day":          confirmed_slot.get("day",  "") if confirmed_slot else "",
            "time":         confirmed_slot.get("time", "") if confirmed_slot else "",
            "duration":     confirmed_slot.get("duration", "90 min") if confirmed_slot else "90 min",
            "activity":     activity,
            "venue_name":   venue_name,
            "venue_address": proposal.get("venue_address", ""),
            "venue_details": proposal.get("venue_details", {}),
            "fa_reply":     reply,
            "confirmed_at": datetime.now().isoformat(),
        }

        if not dry_run:
            rec["pipeline_week"]      = 3
            rec["meeting_confirmed"]  = meeting_confirmed
            rec["week3_at"]           = datetime.now().isoformat()
            with open(email_file, "w") as f:
                json.dump(rec, f, indent=2)

            upsert_pipeline_record(state, fa_id, rec["email_id"], {
                "pipeline_week":    3,
                "meeting_date":     meeting_confirmed["date"],
                "meeting_day":      meeting_confirmed["day"],
                "meeting_time":     meeting_confirmed["time"],
                "meeting_activity": activity,
                "meeting_venue":    venue_name,
                "week3_at":         datetime.now().isoformat(),
            })

        processed += 1

    if not dry_run:
        save_pipeline_state(state)

    print(f"\n   ✅ Week 3 complete — {processed} meeting(s) confirmed")
    print(f"   ▶️  Next: python3 venus.py --week 4")
    return processed
