#!/usr/bin/env python3
"""
venus_closure_notice.py — Venus tells an advisor when their client closed out.

Orbit writes a `source: "orbit_alert"` record to venus_outbox/ whenever a client
fully closes an account. Those alerts are internal (Orbit -> Venus). This module
turns each new alert into a short, warm note from Venus to the advisor
(`source: "closure_notice"`, pipeline_week 0 so Weeks 2-5 ignore it), and marks
the alert as notified so it is only ever sent once.

fa_agent.py then answers the note with a brief "thanks for letting me know".

Usage:
    python3 venus.py --notify-closures          # standalone
    python3 venus.py --week 1                   # also runs automatically
    python3 venus_closure_notice.py [--dry-run] [-v]
"""

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
OUTBOX_DIR = os.path.join(SCRIPT_DIR, "venus_outbox")
_parent = os.path.join(os.path.dirname(SCRIPT_DIR), "crm_advisors.json")
CRM_FILE = _parent if os.path.exists(_parent) else os.path.join(SCRIPT_DIR, "crm_advisors.json")
VENUS_EMAIL = "venus@obsidiancapital.ai"


def _load(path):
    with open(path) as f:
        return json.load(f)


def _norm(name):
    return re.sub(r"[^a-z]", "", (name or "").lower())


def resolve_advisor(alert, advisors):
    """Find the CRM advisor for an alert: fa_id first, then name (legacy alerts have fa_id '')."""
    fa_id = alert.get("fa_id")
    if fa_id:
        for a in advisors:
            if a["fa_id"] == fa_id:
                return a
    target = _norm(alert.get("fa_name"))
    if target:
        for a in advisors:
            if _norm(a.get("name")) == target:
                return a
    return None


def client_name_of(alert):
    if alert.get("client_name"):
        return alert["client_name"]
    m = re.match(r"\s*\W*\s*Client Closure\s*[—-]\s*(.+?)\s*\(", alert.get("subject", ""))
    return m.group(1) if m else "your client"


def draft_notice(alert, advisor, client_name, verbose=False):
    """Ask Venus (LLM) for a short, human note; fall back to a template."""
    first = advisor.get("first_name") or advisor["name"].split()[0]
    fallback_subject = f"Quick heads-up on {client_name}'s account"
    fallback_body = (
        f"Hi {first},\n\n"
        f"I wanted you to hear this from us directly: {client_name} has closed their "
        f"Obsidian Capital account and the full balance has been returned. "
        f"Nothing is needed from you right now. If it would help to talk through "
        f"anything, I'm happy to make time.\n\n— Venus | Obsidian Capital"
    )
    prompt = f"""You are Venus, Obsidian Capital's sales colleague. Write a short, warm
note to a financial advisor letting them know their client has fully closed their account.

Advisor: {advisor['name']} ({advisor['firm']})
Client: {client_name}

Rules: 3-4 sentences. Plain and respectful. Do NOT mention dollar amounts or the
client's reason. Do not pitch anything. Offer to talk if helpful. Sign as: — Venus | Obsidian Capital

RESPOND IN THIS FORMAT:
SUBJECT: [subject line]
BODY:
[the note]"""
    try:
        import venus
        raw = venus.call_venus_llm(prompt, verbose=verbose)
        if raw:
            subject, body = venus.parse_email_response(raw)
            if body and "Hermes not available" not in body and subject != "Obsidian Capital — Quick Note":
                return subject, body
    except Exception as e:
        if verbose:
            print(f"  ⚠️  LLM draft failed ({type(e).__name__}: {e}) — using template")
    return fallback_subject, fallback_body


def run(dry_run=False, verbose=False):
    print("\n📨 Closure notices to advisors")
    advisors = _load(CRM_FILE)["advisors"]
    sent = skipped = 0
    for path in sorted(glob.glob(os.path.join(OUTBOX_DIR, "email_*.json"))):
        try:
            alert = _load(path)
        except Exception:
            continue
        if alert.get("source") != "orbit_alert" or alert.get("alert_type") != "full_close":
            continue
        if alert.get("advisor_notified_at"):
            continue

        client = client_name_of(alert)
        advisor = resolve_advisor(alert, advisors)
        if not advisor:
            print(f"  ⚠️  {client}: no advisor match for '{alert.get('fa_name')}' — skipped")
            skipped += 1
            continue

        subject, body = draft_notice(alert, advisor, client, verbose)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        notice = {
            "email_id":      f"VEN-CLOSE-{advisor['fa_id']}-{ts}",
            "fa_id":         advisor["fa_id"],
            "fa_name":       advisor["name"],
            "firm":          advisor["firm"],
            "territory":     advisor.get("territory", ""),
            "to_email":      advisor.get("email", ""),
            "from_email":    VENUS_EMAIL,
            "subject":       subject,
            "body":          body,
            "sent_at":       datetime.now().isoformat(),
            "status":        "sent",
            "source":        "closure_notice",
            "pipeline_week": 0,
            "response":      None,
            "client_name":   client,
            "alert_email_id": alert.get("email_id"),
            "prospect_score": 0,
            "strategy_notes": [f"Client {client} closed their account — courtesy notice to advisor"],
        }
        print(f"  ✉️  {advisor['name']} ({advisor['firm']}) ← {client}")
        if dry_run:
            continue
        with open(os.path.join(OUTBOX_DIR, f"email_{ts}_CLOSURE_{advisor['fa_id']}.json"), "w") as f:
            json.dump(notice, f, indent=2)
        alert["advisor_notified_at"] = notice["sent_at"]
        alert["fa_id"] = alert.get("fa_id") or advisor["fa_id"]
        with open(path, "w") as f:
            json.dump(alert, f, indent=2)
        sent += 1

    if sent == 0 and skipped == 0:
        print("  (no new closures to report)")
    else:
        print(f"  Sent: {sent} | Skipped: {skipped}")
    return sent


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verbose", "-v", action="store_true")
    a = ap.parse_args()
    run(dry_run=a.dry_run, verbose=a.verbose)
