#!/usr/bin/env python3
"""
fa_agent.py — Financial Advisor Gatekeeper Agent
Reads emails from venus_outbox/, looks up the FA profile,
and uses gemma4 (via Hermes) to simulate the FA's response.

The FA agent plays the role of the FA (or their assistant) —
a busy professional deciding whether Venus's pitch is worth
their boss's time.

Usage:
    python3 fa_agent.py
    python3 fa_agent.py --email venus_outbox/email_20260618_FA-0023.json
    python3 fa_agent.py --all   # process all unanswered emails
    python3 fa_agent.py --dry-run
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
import glob
from datetime import datetime

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
OUTBOX_DIR   = os.path.join(SCRIPT_DIR, "venus_outbox")
RESPONSES_DIR = os.path.join(SCRIPT_DIR, "fa_responses")
CRM_FILE     = os.path.join(SCRIPT_DIR, "crm_advisors.json")
FUND_FILE    = os.path.join(SCRIPT_DIR, "ocrff_fund.json")

os.makedirs(RESPONSES_DIR, exist_ok=True)


def load_json(path):
    with open(path) as f:
        return json.load(f)


def get_fa_profile(fa_id, advisors):
    for a in advisors:
        if a["fa_id"] == fa_id:
            return a
    return None


def build_fa_prompt(email_record, fa, fund):
    """
    Build the FA gatekeeper prompt.
    The FA agent knows everything a real assistant would know:
    the FA's interests, how busy they are, what they're looking for.
    """
    interests = ", ".join(fa.get("interests", []))
    is_busy   = fa["book_size"] > 100
    is_new_fa = fa["book_size"] < 30
    has_asst  = bool(fa.get("assistant_name"))

    busy_desc = (
        "very busy with a full book and gets pitched constantly" if is_busy
        else "building their book and open to new opportunities" if is_new_fa
        else "moderately busy but selective about new investments"
    )

    # FA personality from interests
    personality_notes = []
    if "Baseball" in fa.get("interests", []):
        personality_notes.append("loves baseball and responds well to game invitations")
    if "Basketball" in fa.get("interests", []):
        personality_notes.append("into basketball and responds well to game invitations")
    if "Football" in fa.get("interests", []):
        personality_notes.append("a football fan and responds well to game-day invitations")
    if "Golf" in fa.get("interests", []):
        personality_notes.append("golfer — responds well to networking on the course")
    if "Surfing" in fa.get("interests", []):
        personality_notes.append("surfer — casual tone works well")
    if fa["tier"] == 1:
        personality_notes.append("works at a major wirehouse and is used to high-end service")
    if is_new_fa:
        personality_notes.append("newer advisor actively looking to grow AUM with quality products")

    personality = ". ".join(personality_notes) if personality_notes else "Professional, focused on client outcomes."

    # Who is responding
    if has_asst and fa.get("contact_via_assistant"):
        responder = f"You are {fa['assistant_name']}, assistant to {fa['name']} at {fa['firm']}."
        role_desc = f"Your job is to screen investment pitches and decide if this is worth {fa['first_name']}'s time."
    else:
        responder = f"You are {fa['name']}, a financial advisor at {fa['firm']}."
        role_desc = "You decide whether to respond to investment solicitations."

    prompt = f"""{responder}
{role_desc}

ABOUT {fa['first_name'].upper()}:
- AUM: ${fa['aum']:,.0f} | {fa['book_size']} clients
- {busy_desc.capitalize()}
- Interests: {interests}
- Personality: {personality}
- Territory: {fa['territory']}, CA

YOU JUST RECEIVED THIS EMAIL:
Subject: {email_record['subject']}

{email_record['body']}

---

FUND CONTEXT (if you want to look it up):
The Obsidian Capital Research Fund (OCRFF) is an AI-driven equity fund.
YTD: +{fund['performance']['ytd']['ocrff']}% vs S&P +{fund['performance']['ytd']['sp500']}%
1-Year: +{fund['performance']['one_year']['ocrff']}% vs S&P +{fund['performance']['one_year']['sp500']}%

YOUR TASK:
Decide: is this pitch worth {fa['first_name']}'s time?
Reply as you naturally would — brief, realistic, in character.

RESPOND IN THIS FORMAT:
DECISION: YES or NO
REPLY:
[Your 2-4 sentence reply email, in character]"""

    return prompt


def call_fa_llm(prompt, verbose=False):
    """Call the FA agent via Hermes or Ollama (Mac-compatible)."""
    if verbose:
        print("  [LLM] FA agent thinking...")
    try:
        if _USE_OLLAMA:
            return _call_llm("fa", prompt)
        cmd = ["fa", "-z", prompt, "--ignore-rules"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return "DECISION: NO\nREPLY:\nThanks for reaching out. Not interested at this time."
    except FileNotFoundError:
        return "DECISION: NO\nREPLY:\n[Hermes not available]"


def parse_fa_response(response):
    """Parse the FA agent's structured response."""
    lines    = response.strip().split('\n')
    decision = "NO"
    reply_lines = []
    in_reply = False

    for line in lines:
        if line.startswith("DECISION:"):
            d = line.replace("DECISION:", "").strip().upper()
            decision = "YES" if "YES" in d else "NO"
        elif line.startswith("REPLY:"):
            in_reply = True
        elif in_reply:
            reply_lines.append(line)

    reply = "\n".join(reply_lines).strip()
    if not reply:
        reply = response

    return decision, reply


def process_email(email_file, advisors, fund, dry_run=False, verbose=False):
    """Process one outbox email through the FA agent."""
    email_record = load_json(email_file)

    # Skip already-answered
    if email_record.get("response"):
        print(f"  ⏭️  {os.path.basename(email_file)} — already answered")
        return None

    fa_id = email_record["fa_id"]
    fa    = get_fa_profile(fa_id, advisors)
    if not fa:
        print(f"  ❌ FA {fa_id} not found in CRM")
        return None

    print(f"\n  FA: {fa['name']} | {fa['firm']} | {fa['territory']}")
    print(f"  Email subject: {email_record['subject']}")
    print(f"  Book size: {fa['book_size']} | AUM: ${fa['aum']:,.0f}")

    prompt   = build_fa_prompt(email_record, fa, fund)
    response = call_fa_llm(prompt, verbose=verbose)

    if verbose:
        print(f"\n  [Raw FA Response]\n{response}\n")

    decision, reply = parse_fa_response(response)

    badge = "✅ YES" if decision == "YES" else "❌ NO"
    print(f"  Decision: {badge}")
    print(f"  Reply: {reply[:150]}{'...' if len(reply)>150 else ''}")

    result = {
        "response_id": f"RSP-{fa_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "email_id":    email_record.get("email_id"),
        "fa_id":       fa_id,
        "fa_name":     fa["name"],
        "firm":        fa["firm"],
        "decision":    decision,
        "reply":       reply,
        "responded_at": datetime.now().isoformat(),
        "email_subject": email_record["subject"],
        "venus_body":    email_record["body"],
        "strategy_notes": email_record.get("strategy_notes", []),
        "prospect_score": email_record.get("prospect_score", 0),
    }

    if not dry_run:
        # Save response file
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        resp_file = os.path.join(RESPONSES_DIR, f"response_{ts}_{fa_id}.json")
        with open(resp_file, "w") as f:
            json.dump(result, f, indent=2)

        # Update the outbox email with the response
        email_record["response"] = decision
        email_record["response_body"] = reply
        email_record["responded_at"] = result["responded_at"]
        with open(email_file, "w") as f:
            json.dump(email_record, f, indent=2)

        print(f"  💾 {os.path.basename(resp_file)}")

    return result


def main():
    parser = argparse.ArgumentParser(description="FA Gatekeeper Agent")
    parser.add_argument("--email", type=str, help="Process a specific email file")
    parser.add_argument("--all", action="store_true", help="Process all unanswered emails")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    print("=" * 60)
    print(" 🏦 FA Gatekeeper Agent | Obsidian Capital")
    print(f"    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if args.dry_run:
        print("    *** DRY RUN ***")
    print("=" * 60)

    crm      = load_json(CRM_FILE)
    fund     = load_json(FUND_FILE)
    advisors = crm["advisors"]

    if args.email:
        email_files = [args.email]
    elif args.all:
        email_files = sorted(glob.glob(os.path.join(OUTBOX_DIR, "email_*.json")))
        # Filter to unanswered
        unanswered = []
        for ef in email_files:
            try:
                rec = load_json(ef)
                if not rec.get("response") and rec.get("source") != "orbit_alert":
                    unanswered.append(ef)
            except:
                pass
        email_files = unanswered
        print(f"\n📬 Unanswered emails: {len(email_files)}")
    else:
        # Default: latest unanswered email
        all_emails = sorted(glob.glob(os.path.join(OUTBOX_DIR, "email_*.json")), reverse=True)
        email_files = []
        for ef in all_emails:
            try:
                rec = load_json(ef)
                if not rec.get("response") and rec.get("source") != "orbit_alert":
                    email_files = [ef]
                    break
            except:
                pass
        if not email_files:
            print("\n📭 No unanswered emails in outbox. Run venus.py first.")
            return

    results = []
    for ef in email_files:
        print(f"\n{'─'*60}")
        r = process_email(ef, advisors, fund,
                         dry_run=args.dry_run, verbose=args.verbose)
        if r:
            results.append(r)

    yes_count = sum(1 for r in results if r["decision"] == "YES")
    no_count  = sum(1 for r in results if r["decision"] == "NO")

    print(f"\n{'='*60}")
    print(f"  Processed: {len(results)} | ✅ YES: {yes_count} | ❌ NO: {no_count}")
    if len(results) > 0:
        rate = round(yes_count / len(results) * 100, 1)
        print(f"  Conversion rate: {rate}%")
    print(f"\n▶️  Build dashboard: python3 build_venus_dashboard.py")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
