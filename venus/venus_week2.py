#!/usr/bin/env python3
"""
venus_week2.py — Pipeline Week 2: Venus Reads Replies & Proposes Meetings
Imported and called by venus.py --week 2

For each outbox email where:
  - fa_agent.py has written a response (response == "YES" or "NO")
  - pipeline_week == 1 (hasn't been advanced yet)

Venus does the following:
  - For YES: generates a realistic FA weekly calendar, picks an activity
    based on interests, selects a real venue, proposes 2-3 time slots,
    drafts a follow-up email to schedule the face-to-face meeting.
  - For NO: drafts a gracious close email, marks pipeline complete.

Updates: outbox email JSON + pipeline_state.json
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
from datetime import datetime, date, timedelta

SCRIPT_DIR     = os.path.dirname(os.path.abspath(__file__))
OUTBOX_DIR     = os.path.join(SCRIPT_DIR, "venus_outbox")
CALENDARS_DIR  = os.path.join(SCRIPT_DIR, "fa_calendars")
PIPELINE_FILE  = os.path.join(SCRIPT_DIR, "pipeline_state.json")
VENUES_FILE    = os.path.join(SCRIPT_DIR, "venues.json")
VENUS_EMAIL    = "venus@obsidiancapital.ai"

os.makedirs(CALENDARS_DIR, exist_ok=True)


# ── Venue & Activity Selection ────────────────────────────────────────────

# Team-specific keywords — matching one of these tells us exactly which
# team/stadium was discussed, not just which sport. SoFi Stadium is
# deliberately excluded here since it's shared by the Rams and Chargers
# and would be ambiguous; a bare "SoFi" mention falls through to the
# generic sport keywords below instead.
_SPORT_TEAM_KEYWORDS = [
    ("Baseball",   "mlb", "LAD", ["dodgers", "dodger stadium"]),
    ("Baseball",   "mlb", "SDP", ["padres", "petco park", "petco"]),
    ("Baseball",   "mlb", "LAA", ["angels", "angel stadium"]),
    ("Baseball",   "mlb", "SFG", ["giants", "oracle park"]),
    ("Basketball", "nba", "LAL", ["lakers", "crypto.com arena"]),
    ("Basketball", "nba", "LAC", ["clippers", "intuit dome"]),
    ("Basketball", "nba", "SAC", ["kings", "golden 1 center"]),
    ("Basketball", "nba", "GSW", ["warriors", "chase center"]),
    ("Football",   "nfl", "LAR", ["rams"]),
    ("Football",   "nfl", "LAC", ["chargers"]),
    ("Football",   "nfl", "SF",  ["49ers", "niners", "levi's stadium"]),
]

# Generic sport mentions with no team named — resolved against the FA's own
# team for that league (if they follow one).
_SPORT_GENERIC_KEYWORDS = [
    ("Baseball",   "mlb", ["baseball"]),
    ("Basketball", "nba", ["basketball", "nba game"]),
    ("Football",   "nfl", ["football", "nfl game"]),
]

_OTHER_ACTIVITY_KEYWORDS = [
    ("Golf",            ["golf", "golf course", "round of golf", "tee time"]),
    ("Surfing",         ["surfing", "surf", "waves", "board"]),
    ("Sailing",         ["sailing", "sail", "yacht", "yacht club", "st. francis"]),
    ("Fishing",         ["fishing", "fisherman", "fish", "charters", "wharf"]),
    ("Hiking",          ["hiking", "hike", "trail", "canyon", "temescal", "marin"]),
    ("Mountain Biking", ["mountain biking", "mountain bike", "mtb", "singletrack"]),
    ("Tennis",          ["tennis", "court"]),
    ("Skiing",          ["skiing", "ski", "tahoe", "mammoth", "big bear"]),
    ("Lunch",           ["lunch", "restaurant", "spago", "nobu", "dinner"]),
    ("Coffee",          ["coffee", "cafe", "quick chat"]),
]


def _detect_activity_in_text(text, fa=None):
    """
    Scan email text for a specific activity already discussed.
    Returns {"activity": ..., "league": ... or None, "team_code": ... or None}
    if found, None otherwise. Used to preserve continuity when Venus already
    pitched a specific activity in Week 1.

    A team name (e.g. "Lakers") resolves to that exact team. A bare sport
    word with no team named (e.g. "basketball") resolves to the FA's own
    team for that league, if `fa` is given and they follow one.
    """
    if not text:
        return None
    text_lower = text.lower()

    for activity, league, team_code, keywords in _SPORT_TEAM_KEYWORDS:
        if any(kw in text_lower for kw in keywords):
            return {"activity": activity, "league": league, "team_code": team_code}

    for activity, league, keywords in _SPORT_GENERIC_KEYWORDS:
        if any(kw in text_lower for kw in keywords):
            team_code = (fa or {}).get(f"{league}_team")
            if team_code:
                return {"activity": activity, "league": league, "team_code": team_code}

    for activity, keywords in _OTHER_ACTIVITY_KEYWORDS:
        if any(kw in text_lower for kw in keywords):
            return {"activity": activity, "league": None, "team_code": None}

    return None


def _stadium_for(venues, league, team_code):
    """League-namespaced stadium lookup (stadiums.mlb/nba/nfl.*) — flat
    lookups broke once venues.json was restructured to disambiguate the
    NBA/NFL 'LAC' collision (Clippers vs Chargers)."""
    return venues.get("stadiums", {}).get(league or "", {}).get(team_code or "")


def _live_sport_hooks(sports_hooks):
    """
    Hooks with an actual upcoming home game on file — these are the ones
    worth offering as a meeting activity right now. A team an FA follows
    with no game coming up (off-season, or just nothing in the next 30
    days) isn't a live hook and falls through to other activities instead.
    """
    return [h for h in (sports_hooks or []) if h.get("upcoming", {}).get("next_home")]


def select_activity_and_venue(fa, venues, sports_hooks=None, email_record=None):
    """
    Pick the best activity for this FA, plus any other options that are
    equally live right now.
    Priority for the PRIMARY pick:
      0. Honor activity already discussed in Week 1 email/FA reply (continuity)
      1. Live sport hooks (an upcoming home game on file, any of MLB/NBA/NFL) —
         if the FA follows multiple teams that all have games coming up,
         every one of them comes back as an option so Venus's email can
         offer all of them and let the FA choose, instead of the pipeline
         silently picking one by fixed priority.
      2. Specific non-sport interest match
      3. Lunch
      4. Coffee
    Returns (activity_type, venue_dict, close_modifier, alt_options) where
    alt_options is a list of (activity_type, venue_dict, close_modifier)
    for any additional live sport hooks beyond the primary pick.
    """
    interests    = fa.get("interests", [])
    territory    = fa.get("territory", "Los Angeles")
    modifiers    = venues.get("activity_close_modifiers", {})
    interest_map = venues.get("activity_to_interest_map", {})
    live_hooks   = _live_sport_hooks(sports_hooks)

    # 0. Check if a specific activity was already discussed in Week 1
    if email_record:
        week1_body  = email_record.get("body", "")
        fa_reply    = email_record.get("response_body", "")
        # Check FA reply first (stronger signal — they responded to it)
        discussed = _detect_activity_in_text(fa_reply, fa) or _detect_activity_in_text(week1_body, fa)
        if discussed:
            act = discussed["activity"]
            outdoor_venues = venues.get("outdoor_activities", {})
            if discussed["league"]:
                stadium = _stadium_for(venues, discussed["league"], discussed["team_code"])
                if stadium:
                    return act, stadium, modifiers.get(act, 0.20), []
            elif act in outdoor_venues:
                locs = outdoor_venues.get(act, {}).get(territory, [])
                if locs:
                    return act, random.choice(locs), modifiers.get(act, 0.08), []
            elif act == "Lunch":
                restaurants = venues.get("restaurants", {}).get(territory, {})
                upscale = restaurants.get("upscale", [])
                if upscale:
                    return "Lunch", random.choice(upscale[:3]), modifiers.get("Lunch", 0.05), []
            elif act == "Coffee":
                restaurants = venues.get("restaurants", {}).get(territory, {})
                coffees = restaurants.get("coffee", [])
                if coffees:
                    return "Coffee", random.choice(coffees), modifiers.get("Coffee", 0.00), []
            # If we couldn't resolve a venue for the discussed activity
            # (e.g. missing stadium data), fall through to normal priority.

    # 1. Live sport hooks — offer ALL of them when more than one is live
    hook_options = []
    for hook in live_hooks:
        stadium = _stadium_for(venues, hook["league"], hook["team_code"])
        if stadium:
            act = hook["interest_tag"]
            hook_options.append((act, stadium, modifiers.get(act, 0.20)))
    if hook_options:
        primary, *alternates = hook_options
        return primary[0], primary[1], primary[2], alternates

    # 2. Match specific outdoor/sport interests in priority order
    outdoor_priority = ["Golf", "Surfing", "Sailing", "Fishing", "Tennis", "Skiing", "Hiking"]
    outdoor_venues   = venues.get("outdoor_activities", {})

    for activity in outdoor_priority:
        trigger_interests = interest_map.get(activity, [activity])
        if any(i in interests for i in trigger_interests):
            locs = outdoor_venues.get(activity, {}).get(territory, [])
            if locs:
                venue = random.choice(locs)
                return activity, venue, modifiers.get(activity, 0.08), []

    # 3. Lunch if food/social interests
    lunch_triggers = interest_map.get("Lunch", [])
    if any(i in interests for i in lunch_triggers):
        restaurants = venues.get("restaurants", {}).get(territory, {})
        upscale = restaurants.get("upscale", [])
        if upscale:
            venue = random.choice(upscale[:3])  # pick from top 3
            return "Lunch", venue, modifiers.get("Lunch", 0.05), []

    # 4. Coffee as final fallback
    restaurants = venues.get("restaurants", {}).get(territory, {})
    coffees = restaurants.get("coffee", [])
    if coffees:
        venue = random.choice(coffees)
        return "Coffee", venue, modifiers.get("Coffee", 0.00), []

    # Last resort
    return "Coffee", {"name": "a local coffee shop", "address": territory}, 0.00, []


def get_next_business_week_dates():
    """Return Mon-Fri dates for the next full business week from today."""
    today = date.today()
    days_until_monday = (7 - today.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7
    monday = today + timedelta(days=days_until_monday)
    return [monday + timedelta(days=i) for i in range(5)]


# ── FA Calendar Generation ────────────────────────────────────────────────

def _build_calendar_from_titles(fa, open_days, titles):
    """
    Build a structured calendar dict from a flat list of meeting title strings.
    We assign times ourselves — the LLM only provides the titles.
    Open slots are always Monday 10:30 AM and Tuesday 1:00 PM (randomized order
    in the meeting proposal separately).
    """
    import random as _random
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

    # Time slots per day — open slots are pre-reserved on Mon/Tue
    day_slots = {
        "Monday":    ["9:00 AM", "11:30 AM", "2:00 PM", "3:30 PM"],
        "Tuesday":   ["9:00 AM", "10:30 AM", "3:00 PM", "4:00 PM"],
        "Wednesday": ["9:00 AM", "10:00 AM", "1:00 PM", "2:30 PM", "4:00 PM"],
        "Thursday":  ["9:30 AM", "11:00 AM", "1:00 PM", "3:00 PM"],
        "Friday":    ["9:00 AM", "10:30 AM", "2:00 PM"],
    }
    durations = ["60 min", "60 min", "90 min", "30 min", "60 min"]

    # Shuffle titles and distribute across days
    titles = list(titles)
    _random.shuffle(titles)
    title_idx = 0

    calendar = {}
    for day in days:
        slots = day_slots[day]
        events = []
        for slot in slots:
            if title_idx < len(titles):
                events.append({
                    "time":     slot,
                    "duration": _random.choice(durations),
                    "title":    titles[title_idx],
                    "type":     "blocked"
                })
                title_idx += 1
        calendar[day] = events

    open_slots = [
        {"day": "Monday",  "date": open_days[0].isoformat(), "time": "10:30 AM", "duration": "90 min"},
        {"day": "Tuesday", "date": open_days[1].isoformat(), "time": "1:00 PM",  "duration": "90 min"},
    ]

    return {
        "week_of":      open_days[0].isoformat(),
        "fa_name":      fa["name"],
        "fa_id":        fa["fa_id"],
        "generated_at": datetime.now().isoformat(),
        "calendar":     calendar,
        "open_slots":   open_slots,
    }


def generate_fa_calendar(fa, open_days, venues_data):
    """
    Generate a realistic weekly calendar for the FA.
    LLM provides meeting titles only (plain text list) — we build the JSON.
    This avoids the complex nested JSON schema that causes LLM parse failures.
    Calendar is persisted to fa_calendars/FA-XXXX_calendar.json.
    """
    cal_file = os.path.join(CALENDARS_DIR, f"{fa['fa_id']}_calendar.json")

    # Return cached calendar if generated this week
    if os.path.exists(cal_file):
        with open(cal_file) as f:
            cached = json.load(f)
        if cached.get("week_of") == open_days[0].isoformat():
            return cached

    busy_level = "very busy" if fa["book_size"] > 100 else \
                 "moderately busy" if fa["book_size"] > 40 else "building their book"

    # Ask for ONLY meeting titles — plain text, one per line, no JSON
    prompt = f"""List 8 realistic calendar meeting titles for {fa['name']}, a financial advisor 
at {fa['firm']} in {fa['territory']}, CA. They manage {fa['book_size']} clients ({busy_level}).

Include a mix of: client portfolio reviews, analyst calls, internal team meetings, 
compliance tasks, and lunch with existing clients. Use specific client names and 
firm-appropriate language.

Respond with ONLY 8 meeting titles, one per line, no numbers, no bullets, no extra text.
Example format:
Q2 Portfolio Review — The Henderson Family
{fa['firm']} weekly team call
Compliance training — product updates
Lunch with existing client — La Jolla"""

    titles = []
    try:
        if _USE_OLLAMA:
            raw = _call_llm("venus", prompt)
        else:
            result = subprocess.run(
                ["venus", "-z", prompt, "--ignore-rules"],
                capture_output=True, text=True, timeout=120
            )
            raw = result.stdout.strip()

        # Parse: one title per non-empty line, strip bullets/numbers
        import re as _re
        for line in raw.strip().splitlines():
            line = _re.sub(r'^[\d\.\-\*\•\s]+', '', line).strip()
            if len(line) > 5:
                titles.append(line)
        titles = titles[:10]  # cap at 10

    except Exception as e:
        print(f"   ⚠️  Calendar title generation failed ({e}), using defaults")

    # Fall back to generic titles if LLM returned nothing useful
    if len(titles) < 4:
        titles = [
            f"Q2 Portfolio Review — existing client",
            f"{fa['firm']} weekly team call",
            "Compliance training — product updates",
            "Analyst call — equity outlook",
            "Lunch with existing client",
            "New client onboarding call",
            "Internal strategy meeting",
            "End-of-week recap",
        ]

    calendar = _build_calendar_from_titles(fa, open_days, titles)

    # Persist
    with open(cal_file, "w") as f:
        json.dump(calendar, f, indent=2)

    return calendar


def _template_calendar(fa, open_days):
    """Fallback template calendar if LLM call fails."""
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    blocked = {
        "Monday":    [{"time": "9:00 AM",  "duration": "60 min", "title": "Weekly team meeting", "type": "blocked"},
                      {"time": "2:00 PM",  "duration": "60 min", "title": "Client portfolio review", "type": "blocked"}],
        "Tuesday":   [{"time": "9:00 AM",  "duration": "60 min", "title": "Analyst call — equity outlook", "type": "blocked"},
                      {"time": "3:00 PM",  "duration": "60 min", "title": "Compliance training", "type": "blocked"}],
        "Wednesday": [{"time": "9:30 AM",  "duration": "90 min", "title": "Q2 review — Henderson account", "type": "blocked"},
                      {"time": "2:30 PM",  "duration": "60 min", "title": "New client onboarding call", "type": "blocked"}],
        "Thursday":  [{"time": "10:00 AM", "duration": "60 min", "title": "Internal product update", "type": "blocked"},
                      {"time": "1:00 PM",  "duration": "60 min", "title": "Lunch — existing client", "type": "blocked"}],
        "Friday":    [{"time": "9:00 AM",  "duration": "30 min", "title": "EOW recap — branch manager", "type": "blocked"}],
    }
    return {
        "week_of":      open_days[0].isoformat(),
        "fa_name":      fa["name"],
        "fa_id":        fa["fa_id"],
        "generated_at": datetime.now().isoformat(),
        "calendar":     blocked,
        "open_slots": [
            {"day": days[0], "date": open_days[0].isoformat(), "time": "10:30 AM", "duration": "90 min"},
            {"day": days[1], "date": open_days[1].isoformat(), "time": "1:00 PM",  "duration": "90 min"},
        ]
    }


# ── Venus Follow-Up Email Drafting ────────────────────────────────────────

def _detect_materials_request(reply_text):
    """
    Return True if the FA reply is asking for materials (deck, one-pager, etc.)
    before committing to a meeting. Venus should acknowledge this in her follow-up.
    """
    if not reply_text:
        return False
    signals = [
        "one-pager", "one pager", "deck", "pitch deck", "materials",
        "send over", "send me", "more information", "more info",
        "take a look", "review", "before we", "first", "learn more",
    ]
    lower = reply_text.lower()
    return any(s in lower for s in signals)


_SPORT_ACTIVITIES = ("Baseball", "Basketball", "Football")
_OUTDOOR_ACTIVITIES = ("Surfing", "Golf", "Hiking", "Mountain Biking", "Sailing", "Fishing", "Tennis", "Skiing")


def _venue_desc(activity, venue):
    """One-line venue description for the meeting prompt. Shared between
    the primary pick and any alternate live sport hooks."""
    if activity in _SPORT_ACTIVITIES:
        return (
            f"{venue.get('name')} — {venue.get('club_level', 'great seats')}. "
            f"Obsidian Capital has tickets and Jay would love to take you to the game."
        )
    elif activity in _OUTDOOR_ACTIVITIES:
        return f"{venue.get('name')} — {venue.get('notes', '')}."
    else:
        return f"{venue.get('name')} — {venue.get('address', '')}."


def draft_meeting_proposal(email_record, fa, calendar, activity, venue, venues_data, alt_options=None):
    """Draft Venus follow-up email proposing the meeting with Jay.
    alt_options (optional): list of (activity, venue, close_modifier) for
    other live sport hooks the FA also follows — when present, Venus's
    email offers all of them and lets the FA pick, rather than defaulting
    to just the primary one."""
    import random as _random

    open_slots = calendar.get("open_slots", [])
    # Randomize slot order so FA Agent doesn't always default to the last option
    slots_shuffled = open_slots[:]
    _random.shuffle(slots_shuffled)
    slot_lines = "\n".join(
        f"  - {s['day']}, {s['date']} at {s['time']} ({s['duration']})"
        for s in slots_shuffled
    )

    # Build venue description — Jay is attending, not Venus
    venue_desc = _venue_desc(activity, venue)
    if activity in _SPORT_ACTIVITIES:
        activity_pitch = f"catch a {activity.lower()} game at {venue.get('name')} with Jay"
    elif activity in _OUTDOOR_ACTIVITIES:
        activity_pitch = f"get out for some {activity.lower()} at {venue.get('name')} with Jay"
    else:
        activity_pitch = f"have Jay meet you at {venue.get('name')}"

    multi_hook_instruction = ""
    if alt_options:
        fa_first_name = fa["first_name"]
        alt_lines = [f"  - {activity} at {venue.get('name')} — {venue_desc}"]
        for alt_activity, alt_venue, _alt_mod in alt_options:
            alt_lines.append(f"  - {alt_activity} at {alt_venue.get('name')} — {_venue_desc(alt_activity, alt_venue)}")
        multi_hook_instruction = f"""
MULTIPLE LIVE HOOKS:
{fa_first_name} follows more than one team with a game coming up right now:
{chr(10).join(alt_lines)}
Briefly mention that Jay has a couple of options and ask which one sounds best —
don't just pick one for them. Keep it light, not a bulleted list in the email itself.
"""

    is_assistant = fa.get("contact_via_assistant") and fa.get("assistant_name")
    contact_name = fa.get("assistant_name") if is_assistant else fa["first_name"]
    fa_first     = fa["first_name"]

    if is_assistant:
        scheduling_context = (
            f"You are writing to {contact_name}, assistant to {fa_first}. "
            f"You are trying to get time on {fa_first}'s calendar for a meeting with Jay. "
            f"Ask {contact_name} to check {fa_first}'s availability. "
            f"Do NOT say 'I have openings' or 'I have slots' — you don't own their calendar."
        )
    else:
        scheduling_context = (
            f"You are writing directly to {fa_first}. "
            f"You are scheduling a meeting between {fa_first} and Jay. "
            f"Ask which of these times works for {fa_first}."
        )

    # Detect if FA asked for materials before meeting
    fa_reply         = email_record.get('response_body', '')
    wants_materials  = _detect_materials_request(fa_reply)

    materials_instruction = ""
    if wants_materials:
        materials_instruction = """
MATERIALS REQUEST:
They asked for a deck or one-pager before committing. Address this directly:
- Confirm you're sending (or have sent) the materials they requested
- Then pivot naturally to proposing the meeting as the follow-up step
- Keep it light — don't make it feel like two separate asks
- Example flow: "I'll get that one-pager over to you today. In the meantime, 
  Jay would love to walk you through it in person — would either of these times work?"
"""

    prompt = f"""You are Venus, AI sales assistant for Obsidian Capital.

You received a positive reply to your outreach. Follow up to lock in a face-to-face meeting
between the FA and Jay (Obsidian Capital's portfolio manager). You will NOT be at this meeting.

THE ORIGINAL EMAIL THEY REPLIED TO:
{email_record.get('body', '')}

THEIR REPLY:
{fa_reply}

YOUR TASK:
Draft a short, warm follow-up email proposing the meeting with specific time options.
{materials_instruction}
MEETING DETAILS:
- Activity: {activity} at {venue.get('name', 'TBD')}
- Venue: {venue_desc}
- Time options (ask what works for THEM, do not say you have these open):
{slot_lines}
{multi_hook_instruction}
SCHEDULING CONTEXT:
{scheduling_context}

WRITING RULES:
- Reference something specific from their reply — show you read it carefully.
- Jay attends the meeting, not you. Never write "I'll see you there" or "I'd love to meet."
- Never claim to own their calendar. Ask what works for THEM.
- Keep it to 4-5 sentences. One clear ask (or a quick either/or if multiple hooks are live).
- Baseball/Basketball/Football: Obsidian Capital has the tickets and Jay would love to take them to the game.
- Sign off as: — Venus | Obsidian Capital

RESPOND WITH ONLY:
SUBJECT: [subject line]
BODY:
[email body]"""

    try:
        if _USE_OLLAMA:
            raw = _call_llm("venus", prompt)
        else:
            result = subprocess.run(
                ["venus", "-z", prompt, "--ignore-rules"],
                capture_output=True, text=True, timeout=300
            )
            raw = result.stdout.strip()
    except Exception:
        raw = (f"SUBJECT: Let's find a time\nBODY:\nHi {contact_name}, great to hear from you! "
               f"Jay would love to {activity_pitch}. Which of these times works for you?\n"
               f"{slot_lines}\n\n— Venus | Obsidian Capital")

    # Parse subject/body
    lines_out = raw.strip().split('\n')
    subject   = ""
    body_lines = []
    in_body   = False
    for line in lines_out:
        if line.startswith("SUBJECT:"):
            subject = line.replace("SUBJECT:", "").strip()
        elif line.startswith("BODY:"):
            in_body = True
        elif in_body:
            body_lines.append(line)

    subject = subject or "Let's find a time to connect"
    body    = "\n".join(body_lines).strip() or raw

    return subject, body


def draft_gracious_close(email_record, fa):
    """Draft Venus's close email for a NO response."""
    contact_name = fa.get("assistant_name") if fa.get("contact_via_assistant") else fa["first_name"]

    prompt = f"""You are Venus, AI sales assistant for Obsidian Capital.

{contact_name} replied to your outreach and declined (or wasn't interested).

THEIR REPLY:
{email_record.get('response_body', 'Thanks but not interested at this time.')}

Draft a short, gracious 2-3 sentence close email. 
- No hard feelings, no pushing.
- Leave the door open for the future.
- Offer to send a one-page fund summary if they ever want to revisit.
- Sign off as: — Venus | Obsidian Capital

RESPOND WITH ONLY:
SUBJECT: [subject line]
BODY:
[email body]"""

    try:
        if _USE_OLLAMA:
            raw = _call_llm("venus", prompt)
        else:
            result = subprocess.run(
                ["venus", "-z", prompt, "--ignore-rules"],
                capture_output=True, text=True, timeout=120
            )
            raw = result.stdout.strip()
    except Exception:
        raw = f"SUBJECT: Thanks for your time\nBODY:\nHi {contact_name}, completely understood — appreciate you getting back to me. I'll keep you in mind for future updates from Obsidian Capital, and feel free to reach out anytime. Best of luck this quarter.\n\n— Venus | Obsidian Capital"

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

    return subject or "Thanks for your time", "\n".join(body_lines).strip() or raw


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
    """Update or insert a pipeline record for this FA/email."""
    for record in state["cycles"]:
        if record["email_id"] == email_id:
            record.update(updates)
            return
    state["cycles"].append({
        "email_id": email_id,
        "fa_id":    fa_id,
        **updates
    })


# ── Main Week 2 Runner ────────────────────────────────────────────────────

def run(dry_run=False, verbose=False):
    import glob

    print("\n📬 Week 2: Reading FA replies and proposing meetings...")

    venues_data = {}
    if os.path.exists(VENUES_FILE):
        with open(VENUES_FILE) as f:
            venues_data = json.load(f)
    else:
        print(f"   ⚠️  venues.json not found at {VENUES_FILE}")

    crm_file = os.path.join(SCRIPT_DIR, "crm_advisors.json")
    with open(crm_file) as f:
        crm = json.load(f)
    advisors = {a["fa_id"]: a for a in crm["advisors"]}

    state = load_pipeline_state()

    # Find all Week 1 emails that have a response but haven't been advanced
    email_files = sorted(glob.glob(os.path.join(OUTBOX_DIR, "email_*.json")))
    eligible    = []
    for ef in email_files:
        try:
            with open(ef) as f:
                rec = json.load(f)
            if (rec.get("pipeline_week", 1) == 1
                    and rec.get("response") is not None
                    and rec.get("source") != "orbit_alert"):
                eligible.append((ef, rec))
        except Exception:
            pass

    if not eligible:
        print("   ℹ️  No Week 1 emails with FA responses found.")
        print("      Run: python3 fa_agent.py --all   then re-run venus.py --week 2")
        return 0

    print(f"   Found {len(eligible)} email(s) ready for Week 2\n")

    open_days = get_next_business_week_dates()
    processed = 0

    for email_file, rec in eligible:
        fa_id    = rec["fa_id"]
        fa       = advisors.get(fa_id)
        if not fa:
            print(f"   ❌ FA {fa_id} not found in CRM — skipping")
            continue

        decision = rec.get("response", "NO")
        print(f"   {'─'*55}")
        print(f"   {fa['name']} | {fa['firm']} | Decision: {'✅ YES' if decision == 'YES' else '❌ NO'}")

        if decision == "YES":
            # Generate/load FA calendar
            print(f"   📅 Generating calendar for {fa['first_name']}...")
            calendar = generate_fa_calendar(fa, open_days, venues_data)
            open_slots = calendar.get("open_slots", [])
            print(f"      Open slots: {[s['day'] + ' ' + s['time'] for s in open_slots]}")

            # Select activity and venue — sports_hooks (persisted at Week 1
            # from fetch_prospects.py) carries the real upcoming-game data,
            # so this can tell whether a hook is actually live right now.
            sports_hooks = rec.get("sports_hooks", [])
            activity, venue, close_mod, alt_options = select_activity_and_venue(
                fa, venues_data, sports_hooks=sports_hooks, email_record=rec
            )
            print(f"   🎯 Activity: {activity} @ {venue.get('name', 'TBD')}"
                  + (f"  (+{len(alt_options)} more live hook{'s' if len(alt_options) > 1 else ''})" if alt_options else ""))
            print(f"      Close modifier: +{close_mod*100:.0f}%")

            # Draft meeting proposal email
            subject, body = draft_meeting_proposal(rec, fa, calendar, activity, venue, venues_data, alt_options=alt_options)
            print(f"   📧 Proposal subject: {subject}")
            if verbose:
                print(f"\n{body}\n")

            meeting_proposal = {
                "activity":      activity,
                "venue_name":    venue.get("name", ""),
                "venue_address": venue.get("address", ""),
                "venue_details": venue,
                "alt_options": [
                    {"activity": a, "venue_name": v.get("name", ""), "venue_address": v.get("address", "")}
                    for a, v, _cm in alt_options
                ],
                "open_slots":    open_slots,
                "proposed_at":   datetime.now().isoformat(),
                "follow_up_subject": subject,
                "follow_up_body":    body,
            }

            if not dry_run:
                rec["pipeline_week"]     = 2
                rec["meeting_proposal"]  = meeting_proposal
                rec["follow_up_subject"] = subject
                rec["follow_up_body"]    = body
                rec["week2_at"]          = datetime.now().isoformat()
                with open(email_file, "w") as f:
                    json.dump(rec, f, indent=2)

                upsert_pipeline_record(state, fa_id, rec["email_id"], {
                    "fa_name":       fa["name"],
                    "firm":          fa["firm"],
                    "pipeline_week": 2,
                    "fa_decision":   "YES",
                    "activity":      activity,
                    "venue":         venue.get("name", ""),
                    "close_modifier": close_mod,
                    "week2_at":      datetime.now().isoformat(),
                })

        else:  # NO
            subject, body = draft_gracious_close(rec, fa)
            print(f"   📧 Close email subject: {subject}")

            if not dry_run:
                rec["pipeline_week"]    = 5  # skip to done
                rec["pipeline_status"]  = "closed_no"
                rec["close_email_subject"] = subject
                rec["close_email_body"]    = body
                rec["week2_at"]            = datetime.now().isoformat()
                with open(email_file, "w") as f:
                    json.dump(rec, f, indent=2)

                upsert_pipeline_record(state, fa_id, rec["email_id"], {
                    "fa_name":       fa["name"],
                    "firm":          fa["firm"],
                    "pipeline_week": 5,
                    "pipeline_status": "closed_no",
                    "fa_decision":   "NO",
                    "week2_at":      datetime.now().isoformat(),
                })

        processed += 1

    if not dry_run:
        save_pipeline_state(state)

    print(f"\n   ✅ Week 2 complete — {processed} email(s) processed")
    print(f"   ▶️  Next: python3 venus.py --week 3")
    return processed
