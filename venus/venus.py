#!/usr/bin/env python3
"""
venus.py — Venus AI Sales Agent | Obsidian Capital
Single entry point for the full 5-week FA sales pipeline.

Each week builds on the previous. Run them in order:

    python3 venus.py --week 1   # Draft outreach emails to 5 prospects
    python3 venus.py --week 2   # Read FA replies → propose meetings
    python3 venus.py --week 3   # FA Agent confirms meeting slot
    python3 venus.py --week 4   # Simulate face-to-face, close or no-close
    python3 venus.py --week 5   # Venus debrief email to Jay

Pre-requisite for Week 1:
    python3 fetch_prospects.py  # Select and enrich 5 new prospects

Each week is idempotent for its stage — running it twice won't re-process
records already advanced to the next stage.

Options:
    --week N          Which pipeline week to run (1-5)
    --dry-run         Preview actions without saving any files
    --verbose / -v    Show full LLM output and detailed logging
    --prospects FILE  Override prospect file path (Week 1 only)
    --smtp            Send Week 1 emails via local SMTP (Week 1 only)
"""

import json
import os
import sys
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

import argparse
import smtplib
from email.mime.text import MIMEText
from datetime import datetime

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
OUTBOX_DIR  = os.path.join(SCRIPT_DIR, "venus_outbox")
PROSPECTS_F = os.path.join(SCRIPT_DIR, "venus_prospects_latest.json")
PIPELINE_F  = os.path.join(SCRIPT_DIR, "pipeline_state.json")
SMTP_HOST   = "localhost"
SMTP_PORT   = 2525
VENUS_EMAIL = "venus@obsidiancapital.ai"
JAY_EMAIL   = "jay@obsidiancapital.ai"

os.makedirs(OUTBOX_DIR, exist_ok=True)


# ── Shared utilities ──────────────────────────────────────────────────────

def load_json(path):
    with open(path) as f:
        return json.load(f)


def banner(week, dry_run):
    week_labels = {
        1: "Week 1 — Outreach Emails",
        2: "Week 2 — Read Replies & Propose Meetings",
        3: "Week 3 — FA Confirms Meeting",
        4: "Week 4 — Simulate Meeting & Close",
        5: "Week 5 — Pipeline Debrief",
    }
    print("=" * 60)
    print(f" 💫 Venus | Obsidian Capital")
    print(f"    {week_labels.get(week, f'Week {week}')}")
    print(f"    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if dry_run:
        print("    *** DRY RUN — no files will be modified ***")
    print("=" * 60)


def check_prerequisites(week):
    """Verify prior week's work is in place before running current week."""
    import glob

    if week == 1:
        if not os.path.exists(PROSPECTS_F):
            print(f"\n❌ Prospect file not found: {PROSPECTS_F}")
            print(f"   Run: python3 fetch_prospects.py")
            sys.exit(1)
        data = load_json(PROSPECTS_F)
        if not data.get("prospects"):
            print(f"\n❌ No prospects in {PROSPECTS_F}")
            print(f"   Run: python3 fetch_prospects.py")
            sys.exit(1)
        return

    if week == 2:
        email_files = glob.glob(os.path.join(OUTBOX_DIR, "email_*.json"))
        w1_with_response = []
        for ef in email_files:
            try:
                rec = load_json(ef)
                if rec.get("pipeline_week", 1) == 1 and rec.get("response") is not None:
                    w1_with_response.append(ef)
            except Exception:
                pass
        if not w1_with_response:
            print(f"\n❌ No Week 1 emails with FA responses found.")
            print(f"   Run: python3 fa_agent.py --all")
            print(f"   Then re-run: python3 venus.py --week 2")
            sys.exit(1)
        return

    if week == 3:
        email_files = glob.glob(os.path.join(OUTBOX_DIR, "email_*.json"))
        w2_ready = [
            ef for ef in email_files
            if _safe_load_week(ef) == 2
        ]
        if not w2_ready:
            print(f"\n❌ No Week 2 emails found (meeting proposals pending).")
            print(f"   Run: python3 venus.py --week 2   first")
            sys.exit(1)
        return

    if week == 4:
        email_files = glob.glob(os.path.join(OUTBOX_DIR, "email_*.json"))
        w3_ready = [ef for ef in email_files if _safe_load_week(ef) == 3]
        if not w3_ready:
            print(f"\n❌ No Week 3 emails found (confirmed meetings pending).")
            print(f"   Run: python3 venus.py --week 3   first")
            sys.exit(1)
        return

    if week == 5:
        email_files = glob.glob(os.path.join(OUTBOX_DIR, "email_*.json"))
        w4_ready = [ef for ef in email_files if _safe_load_week(ef) == 4]
        if not w4_ready:
            print(f"\n❌ No Week 4 emails found (simulated meetings pending).")
            print(f"   Run: python3 venus.py --week 4   first")
            sys.exit(1)
        return


def _safe_load_week(email_file):
    try:
        with open(email_file) as f:
            rec = json.load(f)
        if rec.get("source") in ("orbit_alert", "debrief"):
            return -1
        return rec.get("pipeline_week", 1)
    except Exception:
        return -1


# ── Week 1: Draft outreach emails ─────────────────────────────────────────

def build_venus_prompt(prospect):
    fa       = prospect["profile"]
    baseball = prospect["baseball"]
    perf     = prospect["ocrff_performance"]
    notes    = prospect["strategy_notes"]
    next_home = baseball["upcoming"].get("next_home")
    home_games = baseball["upcoming"].get("home_games", [])[:3]

    if next_home and "Baseball" in fa.get("interests", []):
        bb_context = f"""
BASEBALL HOOK:
{fa['first_name']} is a fan of the {baseball['team_name']}.
Upcoming home games:
{chr(10).join('  - ' + g['label'] for g in home_games)}
Jay has access to tickets. Use this as a relationship hook if relevant.
"""
    else:
        bb_context = "No baseball hook available for this FA."

    ytd    = perf["ytd"]
    one_yr = perf["one_year"]
    five_yr = perf["five_year_ann"]
    interests = ", ".join(fa.get("interests", []))
    connections = fa.get("connections", [])
    warm_ref = (
        f"WARM INTRODUCTION: {fa['first_name']} knows {', '.join(connections)}, "
        f"who is already an Obsidian client. Mention this connection."
        if connections else ""
    )
    contact_is_assistant = bool(fa.get("contact_via_assistant") and fa.get("assistant_name"))
    strategy_block = "\n".join(f"  - {n}" for n in notes) if notes else "  - Standard outreach"

    return f"""You are Venus, an AI sales assistant for Obsidian Capital, an AI-driven investment research firm based in California.

Your job: draft a short, personalized outreach email to get a face-to-face meeting.

WRITING STYLE:
- Casual but professional. Think of how a savvy wholesaler writes — not a corporate robot.
- Short: 4-6 sentences max. No walls of text.
- Personal: reference their interests, the baseball schedule, or a mutual connection.
- One clear call to action: ask for a meeting or a call.
- Sign off as: — Venus | Obsidian Capital
- Subject line should be punchy and personal, not generic.

PROSPECT PROFILE:
Name: {fa['name']}
Firm: {fa['firm']}
Territory: {fa['territory']}, CA
AUM: ${fa['aum']:,.0f}
Book size: {fa['book_size']} clients
Interests: {interests}
Education: {fa.get('education', 'N/A')}
{"You are writing to assistant: " + fa['assistant_name'] if contact_is_assistant else "You are writing directly to the FA."}

{warm_ref}

{bb_context}

OCRFF PERFORMANCE (Obsidian Capital Research Fund):
- YTD: OCRFF +{ytd['ocrff']}% vs S&P 500 +{ytd['sp500']}% (alpha: +{ytd['alpha']}%)
- 1-Year: OCRFF +{one_yr['ocrff']}% vs S&P 500 +{one_yr['sp500']}%
- 5-Year Annualized: OCRFF +{five_yr['ocrff']}% vs S&P 500 +{five_yr['sp500']}%
Use performance data selectively — only if it strengthens the email. Don't force it.

STRATEGY NOTES:
{strategy_block}

OUTPUT FORMAT — respond with ONLY these two things, nothing else:
SUBJECT: [subject line]
BODY:
[email body]"""


# Strings that indicate a failed/empty LLM response — trigger a retry
_FALLBACK_SIGNALS = (
    "[LLM not available]",
    "SUBJECT: Placeholder",
    "SUBJECT: Follow up",
    "Hi, I'd love to connect about Obsidian Capital",
)

def _is_bad_response(raw):
    """Return True if the LLM response is blank or a known fallback."""
    if not raw or not raw.strip():
        return True
    return any(sig in raw for sig in _FALLBACK_SIGNALS)


def call_venus_llm(prompt, verbose=False, retries=2):
    """Call Venus LLM with automatic retry on blank or fallback responses."""
    if verbose:
        print("  [LLM] Drafting email via Hermes/Ollama...")
    for attempt in range(1, retries + 1):
        try:
            if _USE_OLLAMA:
                raw = _call_llm("venus", prompt)
            else:
                cmd = ["venus", "-z", prompt, "--ignore-rules"]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
                raw = result.stdout.strip()

            if not _is_bad_response(raw):
                return raw

            # Bad response — retry if attempts remain
            if attempt < retries:
                print(f"  ⚠️  Empty/fallback response (attempt {attempt}/{retries}) — retrying...")
            else:
                print(f"  ❌ LLM returned empty after {retries} attempts — skipping this FA")
                return ""

        except subprocess.TimeoutExpired:
            print(f"  ⚠️  LLM timeout (attempt {attempt}/{retries})")
            if attempt >= retries:
                return ""
        except FileNotFoundError:
            print("  ⚠️  Hermes not found — Ollama unavailable")
            return ""
    return ""


def parse_email_response(response):
    lines = response.strip().split('\n')
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
    body = "\n".join(body_lines).strip()
    if not subject:
        subject = "Obsidian Capital — Quick Note"
    if not body:
        body = response
    return subject, body


def send_via_smtp(to_email, subject, body, from_email=VENUS_EMAIL):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"]    = from_email
    msg["To"]      = to_email
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=5) as s:
            s.sendmail(from_email, [to_email], msg.as_string())
        return True
    except Exception as e:
        print(f"  ⚠️  SMTP failed: {e} — falling back to outbox file")
        return False


def save_to_outbox(fa_id, to_email, subject, body, prospect):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(OUTBOX_DIR, f"email_{ts}_{fa_id}.json")
    email_record = {
        "email_id":       f"VNS-{fa_id}-{ts}",
        "fa_id":          fa_id,
        "fa_name":        prospect["profile"]["name"],
        "firm":           prospect["profile"]["firm"],
        "territory":      prospect["profile"]["territory"],
        "to_email":       to_email,
        "from_email":     VENUS_EMAIL,
        "subject":        subject,
        "body":           body,
        "sent_at":        datetime.now().isoformat(),
        "status":         "sent",
        "pipeline_week":  1,
        "response":       None,
        "prospect_score": prospect.get("prospect_score", 0),
        "strategy_notes": prospect.get("strategy_notes", []),
    }
    with open(filename, "w") as f:
        json.dump(email_record, f, indent=2)
    return filename


def run_week1(args):
    """Draft and send initial outreach emails to prospects."""
    data      = load_json(args.prospects)
    prospects = data["prospects"]

    # Check for already-emailed FAs in this batch (prevent duplicate Week 1 sends)
    import glob
    existing_fa_ids = set()
    for ef in glob.glob(os.path.join(OUTBOX_DIR, "email_*.json")):
        try:
            rec = load_json(ef)
            if rec.get("pipeline_week", 1) >= 1:
                existing_fa_ids.add(rec["fa_id"])
        except Exception:
            pass

    print(f"\n📋 Prospects loaded: {len(prospects)}")
    print(f"   OCRFF YTD: +{data['ocrff_ytd']}% vs S&P +{data['sp500_ytd']}%\n")

    outbox_files = []
    skipped      = 0

    for i, prospect in enumerate(prospects):
        fa    = prospect["profile"]
        fa_id = prospect["fa_id"]

        if fa_id in existing_fa_ids:
            print(f"  ⏭️  [{i+1}/{len(prospects)}] {fa['name']} — already in pipeline, skipping")
            skipped += 1
            continue

        to_email = prospect.get("contact_email", fa["email"])
        to_name  = prospect.get("contact_name", fa["name"])

        print(f"{'─'*60}")
        print(f"  [{i+1}/{len(prospects)}] {fa['name']} | {fa['firm']} | {fa['territory']}")
        print(f"  To: {to_name} <{to_email}>")
        print(f"  Score: {prospect.get('prospect_score', 0)} | Book: {fa['book_size']} | "
              f"{'⚾ ' if 'Baseball' in fa.get('interests',[]) else ''}"
              f"{'🔥 Warm' if fa.get('warm_lead') else ''}")
        print()

        prompt   = build_venus_prompt(prospect)
        response = call_venus_llm(prompt, verbose=args.verbose)
        if args.verbose:
            print(f"  [Raw LLM]\n{response}\n")

        # Skip this FA entirely if LLM returned nothing after retries
        if not response or not response.strip():
            print(f"  ⏭️  Skipping {fa['name']} — no usable LLM response after retries")
            skipped += 1
            continue

        subject, body = parse_email_response(response)

        # Secondary guard: skip if body is blank or still a placeholder
        if not body or _is_bad_response(body):
            print(f"  ⏭️  Skipping {fa['name']} — email body empty or placeholder")
            skipped += 1
            continue

        print(f"  📧 Subject: {subject}")
        print(f"  {'─'*50}")
        print(f"  {body[:300]}{'...' if len(body) > 300 else ''}\n")

        if not args.dry_run:
            sent_smtp = False
            if args.smtp:
                sent_smtp = send_via_smtp(to_email, subject, body)
            outbox_file = save_to_outbox(fa_id, to_email, subject, body, prospect)
            outbox_files.append(outbox_file)
            method = "SMTP + file" if sent_smtp else "outbox file"
            print(f"  💾 Saved ({method}): {os.path.basename(outbox_file)}")

    print(f"\n{'='*60}")
    print(f"  Emails drafted: {len(prospects) - skipped}")
    if skipped:
        print(f"  Skipped (already in pipeline): {skipped}")
    if outbox_files:
        print(f"  Saved to: {OUTBOX_DIR}/")
        print(f"\n▶️  Next: python3 fa_agent.py --all")
        print(f"      then: python3 venus.py --week 2")
    print(f"{'='*60}")


# ── Main dispatcher ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Venus — Obsidian Capital 5-Week Sales Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Pipeline flow:
  python3 fetch_prospects.py          # Pre-requisite: select 5 FAs
  python3 venus.py --week 1           # Send outreach emails
  python3 fa_agent.py --all           # FA Agent reads and replies
  python3 venus.py --week 2           # Venus reads replies, proposes meetings
  python3 venus.py --week 3           # FA confirms meeting slot
  python3 venus.py --week 4           # Simulate meeting, close or no-close
  python3 venus.py --week 5           # Venus debrief email to Jay
        """
    )
    parser.add_argument("--week", type=int, choices=[1,2,3,4,5], required=True,
                        help="Pipeline week to run (1-5)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without saving any files")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show full LLM output")
    parser.add_argument("--prospects", type=str, default=PROSPECTS_F,
                        help="Prospect file override (Week 1 only)")
    parser.add_argument("--smtp", action="store_true",
                        help="Send via local SMTP (Week 1 only)")
    args = parser.parse_args()

    banner(args.week, args.dry_run)
    check_prerequisites(args.week)

    if args.week == 1:
        run_week1(args)

    elif args.week == 2:
        sys.path.insert(0, SCRIPT_DIR)
        import venus_week2
        venus_week2.run(dry_run=args.dry_run, verbose=args.verbose)

    elif args.week == 3:
        sys.path.insert(0, SCRIPT_DIR)
        import venus_week3
        venus_week3.run(dry_run=args.dry_run, verbose=args.verbose)

    elif args.week == 4:
        sys.path.insert(0, SCRIPT_DIR)
        import venus_week4
        venus_week4.run(dry_run=args.dry_run, verbose=args.verbose)

    elif args.week == 5:
        sys.path.insert(0, SCRIPT_DIR)
        import venus_week5
        venus_week5.run(dry_run=args.dry_run, verbose=args.verbose)


if __name__ == "__main__":
    main()
