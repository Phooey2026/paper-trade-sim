#!/usr/bin/env python3
"""
venus_week4.py — Pipeline Week 4: Simulate the Face-to-Face Meeting
Imported and called by venus.py --week 4

For each outbox email at pipeline_week == 3 (meeting confirmed):
  - Calculates close probability from: warm lead, book size, tier, activity modifier
  - Rolls the dice
  - Generates a 200-250 word narrative of the meeting (win or loss)
  - On a CLOSE: calculates deal value (2% of AUM), opens new client accounts at NAV
    (venus_new_clients.py: shares issued, cash added, NAV refreshed), updates FA status to CLIENT
  - Updates pipeline_state.json
"""

import json
import os
import random
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
PARENT_DIR    = os.path.dirname(SCRIPT_DIR)
OUTBOX_DIR    = os.path.join(SCRIPT_DIR, "venus_outbox")
PIPELINE_FILE = os.path.join(SCRIPT_DIR, "pipeline_state.json")
CRM_FILE      = os.path.join(SCRIPT_DIR, "crm_advisors.json")
VENUES_FILE   = os.path.join(SCRIPT_DIR, "venues.json")

# neptune_holdings.json lives in paper_trade/ (parent of venus/)
# Fall back to script dir if running standalone
_holdings_parent = os.path.join(PARENT_DIR, "neptune_holdings.json")
_holdings_local  = os.path.join(SCRIPT_DIR, "neptune_holdings.json")
HOLDINGS_FILE    = _holdings_parent if os.path.exists(_holdings_parent) else _holdings_local

DEAL_PCT      = 0.02   # 2% of AUM

# A close creates new client accounts at the current NAV (see venus_new_clients.py).
# nav_engine.py / neptune_holdings.json live in paper_trade/ (parent of venus/)
try:
    import venus_new_clients as _newc
except ImportError:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("venus_new_clients", os.path.join(SCRIPT_DIR, "venus_new_clients.py"))
    _newc = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_newc)


# ── Close Probability Calculation ─────────────────────────────────────────

def calculate_close_probability(fa, activity, venues_data):
    """
    Base probability + modifiers → clamped to [0.25, 0.85]
    
    Base:
      warm lead   → 0.45
      cold lead   → 0.25
    
    Modifiers:
      tier 1      → +0.05  (high stakes, high commitment)
      new FA      → +0.10  (more receptive, book_size < 30)
      activity    → from venues.json activity_close_modifiers
    """
    base = 0.45 if fa.get("warm_lead") else 0.25

    modifiers = 0.0

    # Tier modifier
    if fa.get("tier") == 1:
        modifiers += 0.05

    # New FA modifier
    if fa.get("book_size", 100) < 30:
        modifiers += 0.10

    # Activity modifier
    activity_mods = venues_data.get("activity_close_modifiers", {})
    modifiers += activity_mods.get(activity, 0.0)

    prob = base + modifiers
    prob = max(0.25, min(0.85, prob))  # clamp to [25%, 85%]
    return round(prob, 4)


# ── Meeting Narrative Generation ──────────────────────────────────────────

# No-close reasons — varied pool to avoid formulaic repetition
_NO_CLOSE_REASONS = [
    lambda fa: (f"{fa['first_name']} has a pending compliance review and can't add new products until it clears. "
                f"Probably 6-8 weeks. Worth a follow-up then."),
    lambda fa: (f"{fa['first_name']} is locked into a competing product with a redemption period — "
                f"can't move client money until that matures. Timing issue, not a fit issue."),
    lambda fa: (f"{fa['first_name']} liked the story but needs to see one more quarter of performance "
                f"before presenting something new to clients. Said Q3 numbers would probably do it."),
    lambda fa: (f"{fa['first_name']} has a strong existing relationship with another wholesaler at a competing fund. "
                f"Not locked in but loyal. Needs a compelling reason to diversify."),
    lambda fa: (f"{fa['first_name']} is in the middle of a branch transition — new manager, new directives. "
                f"Doesn't want to bring in new products until things settle down internally."),
    lambda fa: (f"{fa['first_name']} is interested but their firm's approved product list is under review. "
                f"Can't add OCRFF until it clears the home office. Not a {fa['first_name']} problem — a firm problem."),
    lambda fa: (f"{fa['first_name']} had a bad experience with a quant fund a few years back and is cautious. "
                f"The AI angle is actually a hesitation point. Needs more education before they're comfortable."),
]


def generate_meeting_narrative(fa, confirmed, close_probability, closed, deal_value, verbose=False):
    """Generate a 200-250 word narrative of the meeting."""
    import random as _random
    activity   = confirmed.get("activity", "Coffee")
    venue_name = confirmed.get("venue_name", "a local spot")
    venue_addr = confirmed.get("venue_address", "")
    meet_date  = confirmed.get("date", "")
    meet_time  = confirmed.get("time", "")

    if closed:
        outcome_hint = (
            f"The meeting went well and {fa['first_name']} agreed to allocate "
            f"${deal_value:,.0f} (2% of their AUM) to the Obsidian Capital Research Fund. "
            f"They were impressed by the alpha story and wanted to get started before quarter-end. "
            f"Jay left with a commitment — this one is closed."
        )
    else:
        # Pick a varied no-close reason
        reason_fn = _random.choice(_NO_CLOSE_REASONS)
        outcome_hint = (
            f"Despite a good personal connection, {fa['first_name']} did not commit today. "
            f"{reason_fn(fa)}"
        )

    prompt = f"""Write a 200-250 word first-person voice memo from Jay's perspective describing 
a sales meeting with {fa['name']}, a financial advisor at {fa['firm']} in {fa['territory']}, CA.

MEETING DETAILS:
- Activity: {activity} at {venue_name}
- Address: {venue_addr}
- Date/Time: {meet_date} at {meet_time}
- FA interests: {', '.join(fa.get('interests', []))}
- FA book size: {fa['book_size']} clients | AUM: ${fa['aum']:,.0f} | Tier {fa['tier']}

OUTCOME:
{outcome_hint}

NARRATIVE STYLE:
- Jay is speaking — first person, as if dictating into his phone right after the meeting
- Conversational, specific, vivid — describe the setting, the energy, one moment that stood out
- If baseball: the inning, a big play, the crowd, what clicked in the conversation
- If basketball: the quarter, a highlight play, the arena energy, what clicked in the conversation
- If football: the quarter, a big play or drive, the tailgate/stadium energy, what clicked in the conversation
- If outdoor activity: conditions (wind, trail, surf), the physical setting, pace of conversation
- If lunch/coffee: the room, what was ordered, the vibe of the conversation
- On a close: end with the handshake moment and what happens next
- On no close: end with a clear read on the real reason and the specific next step
- DO NOT sign off — this is a voice memo, not an email
- DO NOT say "he/she is a prospect" — if they closed, they are now a client
- DO NOT use the word "narrative" or reference being an AI
- DO NOT include "— Venus" or any Venus signature"""

    try:
        if _USE_OLLAMA:
            narrative = _call_llm("venus", prompt)
        else:
            result = subprocess.run(
                ["venus", "-z", prompt, "--ignore-rules"],
                capture_output=True, text=True, timeout=120
            )
            narrative = result.stdout.strip()
        if len(narrative) < 50:
            raise ValueError("narrative too short")
        return narrative
    except Exception as e:
        if verbose:
            print(f"   ⚠️  Narrative LLM failed: {e}")
        # Fallback narrative
        outcome_word = "closed" if closed else "did not close"
        return (
            f"Met {fa['first_name']} {fa['last_name']} at {venue_name} on {meet_date}. "
            f"We had a good {activity.lower()} and discussed the OCRFF performance story. "
            f"{fa['first_name']} was {'receptive and committed to moving forward' if closed else 'engaged but not ready to commit today'}. "
            f"{'Deal value: $' + f'{deal_value:,.0f}' + ' opened across new client accounts.' if closed else 'Will follow up next quarter.'}"
        )


# ── Holdings Update ───────────────────────────────────────────────────────
# Cash is no longer injected on its own. venus_new_clients.apply_close() creates the new
# client accounts, issues their shares at NAV, adds the cash and refreshes the NAV together,
# so a close can never move the NAV.


def update_fa_status_to_client(fa_id, deal_value, pipeline_record, account_ids=None):
    """Mark the FA a CLIENT in crm_advisors.json. AUM and client count ADD to what is already there."""
    with open(CRM_FILE) as f:
        crm = json.load(f)

    account_ids = account_ids or []
    for fa in crm["advisors"]:
        if fa["fa_id"] == fa_id:
            fa["status"]     = "CLIENT"
            fa["oc_aum"]     = round((fa.get("oc_aum") or 0) + deal_value, 2)
            fa["oc_clients"] = (fa.get("oc_clients") or 0) + (len(account_ids) or 1)
            fa["oc_account_ids"] = sorted(set(fa.get("oc_account_ids", [])) | set(account_ids))
            fa["notes"]      = (
                f"Converted {datetime.now().strftime('%Y-%m-%d')} via "
                f"{pipeline_record.get('meeting_activity', 'meeting')} at "
                f"{pipeline_record.get('meeting_venue', 'TBD')}. "
                f"Allocation: ${deal_value:,.0f} across {len(account_ids) or 1} new client account(s)."
            )
            fa["pipeline_history"] = pipeline_record
            break

    tmp = CRM_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(crm, f, indent=2)
    os.replace(tmp, CRM_FILE)


# ── Pipeline State ────────────────────────────────────────────────────────

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


def get_pipeline_record(state, email_id):
    for record in state["cycles"]:
        if record["email_id"] == email_id:
            return record
    return {}


# ── Main Week 4 Runner ────────────────────────────────────────────────────

def run(dry_run=False, verbose=False):
    import glob

    print("\n🤝 Week 4: Simulating face-to-face meetings...")

    venues_data = {}
    if os.path.exists(VENUES_FILE):
        with open(VENUES_FILE) as f:
            venues_data = json.load(f)

    with open(CRM_FILE) as f:
        crm = json.load(f)
    advisors = {a["fa_id"]: a for a in crm["advisors"]}
    state = load_pipeline_state()

    # Find all Week 3 emails (meeting confirmed, awaiting simulation)
    email_files = sorted(glob.glob(os.path.join(OUTBOX_DIR, "email_*.json")))
    eligible    = []
    for ef in email_files:
        try:
            with open(ef) as f:
                rec = json.load(f)
            if (rec.get("pipeline_week") == 3
                    and rec.get("meeting_confirmed")
                    and rec.get("source") != "orbit_alert"):
                eligible.append((ef, rec))
        except Exception:
            pass

    if not eligible:
        print("   ℹ️  No Week 3 emails with confirmed meetings found.")
        print("      Run: python3 venus.py --week 3   first")
        return 0

    print(f"   Found {len(eligible)} confirmed meeting(s) to simulate\n")
    processed  = 0
    closes     = 0
    total_aum  = 0.0

    for email_file, rec in eligible:
        fa_id     = rec["fa_id"]
        fa        = advisors.get(fa_id)
        if not fa:
            print(f"   ❌ FA {fa_id} not found — skipping")
            continue

        confirmed  = rec["meeting_confirmed"]
        activity   = confirmed.get("activity", "Coffee")
        venue_name = confirmed.get("venue_name", "")

        print(f"   {'─'*55}")
        print(f"   {fa['name']} | {fa['firm']}")
        print(f"   Meeting: {activity} @ {venue_name}")
        print(f"   Date: {confirmed.get('date', 'TBD')} at {confirmed.get('time', 'TBD')}")

        # Calculate close probability
        close_prob = calculate_close_probability(fa, activity, venues_data)
        deal_value = round(fa["aum"] * DEAL_PCT, 2)

        print(f"   Close probability: {close_prob*100:.1f}%")
        print(f"   Deal value if closed: ${deal_value:,.0f}")

        # Roll the dice
        roll   = random.random()
        closed = roll <= close_prob

        print(f"   Dice roll: {roll:.4f} {'✅ CLOSED!' if closed else '❌ No close'}")

        # Generate narrative
        print(f"   ✍️  Generating meeting narrative...")
        narrative = generate_meeting_narrative(
            fa, confirmed, close_prob, closed, deal_value, verbose=verbose
        )

        if verbose:
            print(f"\n   [Narrative]\n{narrative}\n")

        meeting_outcome = {
            "closed":            closed,
            "close_probability": close_prob,
            "dice_roll":         round(roll, 4),
            "deal_value":        deal_value if closed else 0.0,
            "narrative":         narrative,
            "simulated_at":      datetime.now().isoformat(),
        }

        pipeline_rec = get_pipeline_record(state, rec["email_id"])

        if closed:
            closes    += 1
            total_aum += deal_value
            print(f"   💰 Opening new client accounts for ${deal_value:,.0f} at NAV...")

            if not dry_run:
                try:
                    res = _newc.apply_close(fa, deal_value, narrative[:100])
                except Exception as e:
                    # nothing was written (apply_close rolls back) → don't count it as a close
                    print(f"   ❌ Could not book the close: {e} — recorded as NOT closed, rerun week 4")
                    closes -= 1
                    total_aum -= deal_value
                    continue
                ids = [a["account_id"] for a in res["accounts"]]
                print(f"   ✅ {len(ids)} accounts: {', '.join(ids)}  "
                      f"({res['shares_issued']:,.4f} shares @ NAV ${res['nav_before']:,.4f}; "
                      f"NAV after ${res['nav_after']:,.4f})")
                meeting_outcome["new_accounts"] = ids

                update_fa_status_to_client(fa_id, deal_value, {
                    **pipeline_rec,
                    "meeting_activity": activity,
                    "meeting_venue":    venue_name,
                    "meeting_date":     confirmed.get("date", ""),
                    "deal_value":       deal_value,
                    "narrative":        narrative,
                }, account_ids=ids)
                print(f"   ✅ {fa['name']} → CLIENT in CRM")

        if not dry_run:
            rec["pipeline_week"]    = 4
            rec["meeting_outcome"]  = meeting_outcome
            rec["week4_at"]         = datetime.now().isoformat()
            with open(email_file, "w") as f:
                json.dump(rec, f, indent=2)

            upsert_pipeline_record(state, fa_id, rec["email_id"], {
                "pipeline_week":    4,
                "pipeline_status":  "closed_won" if closed else "closed_lost",
                "close_probability": close_prob,
                "dice_roll":        round(roll, 4),
                "closed":           closed,
                "deal_value":       deal_value if closed else 0.0,
                "week4_at":         datetime.now().isoformat(),
            })

        processed += 1

    if not dry_run:
        save_pipeline_state(state)

    print(f"\n   ✅ Week 4 complete")
    print(f"   Meetings simulated : {processed}")
    print(f"   Closed             : {closes}")
    print(f"   New AUM            : ${total_aum:,.0f}")
    if processed > 0:
        print(f"   Close rate         : {closes/processed*100:.1f}%")
    print(f"   ▶️  Next: python3 venus.py --week 5")
    return processed
