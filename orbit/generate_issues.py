#!/usr/bin/env python3
"""
generate_issues.py — Orbit Issue Generator
Pulls 5 random clients from crm_clients.json and assigns realistic
customer service issues. Outputs orbit_issues_YYYYMMDD_HHMM.json.

Only ACTIVE accounts with a balance are selected, and the same client is
never picked twice in one run.

Issue types and weights:
  BUY order         20% — client wants to invest more
  SELL order        25% — client wants partial redemption
  FULL CLOSE         5% — client closes entire position
  STATEMENT         20% — statement/balance request
  ADDRESS CHANGE    15% — update mailing address
  BENEFICIARY       10% — update beneficiary
  HOLDINGS INQUIRY   5% — what stocks do you hold?

Usage:
    python3 generate_issues.py
    python3 generate_issues.py --count 5 --force-type full_close
    python3 generate_issues.py --seed 42
"""

import json, os, sys, random, argparse
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from crm_schema import (SCHEMA_VERSION, random_address, make_designation,
                        designation_equal, format_address, format_designation)

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
PAPER_DIR   = os.path.dirname(SCRIPT_DIR)
VENUS_DIR   = os.path.join(PAPER_DIR, "venus")
FUND_FILE   = os.path.join(VENUS_DIR, "ocrff_fund.json")
CLIENTS_FILE= os.path.join(VENUS_DIR, "crm_clients.json")
OUT_FILE    = os.path.join(SCRIPT_DIR, "orbit_issues_latest.json")

# One stamp per run. Issue IDs used to be date + counter only, so two runs on the
# same day produced colliding IDs (ISS-20260928-001 twice) — which also collided
# transaction, request and form IDs downstream.
RUN_STAMP = datetime.now().strftime('%Y%m%d-%H%M%S')

ISSUE_WEIGHTS = {
    "buy":         20,
    "sell":        25,
    "full_close":   5,
    "statement":   20,
    "address":     15,
    "beneficiary": 10,
    "holdings":     5,
}

BUY_AMOUNTS  = [25000, 50000, 100000, 150000, 250000]
SELL_AMOUNTS = [25000, 50000, 75000, 100000, 150000, 200000, 250000]

HOLDINGS_ASKS = [
    "What stocks are you currently holding in the fund?",
    "Can you send me a complete list of all your portfolio holdings?",
    "I'd like to see exactly what you're invested in — full holdings list please.",
    "What are all the positions in the Obsidian Research Fund right now?",
    "My accountant is asking for a full breakdown of the fund's holdings.",
]

BUY_NARRATIVES = [
    "I'd like to invest an additional ${amount:,} into the fund.",
    "Please add ${amount:,} to my account.",
    "I want to put another ${amount:,} into Obsidian Research.",
    "I have ${amount:,} I'd like to invest. Please process this at today's NAV.",
    "Can you add ${amount:,} to my position? Market looks good right now.",
]

SELL_NARRATIVES = [
    "I need to redeem ${amount:,} from my account for a home purchase.",
    "Please liquidate ${amount:,} from my position. Need the funds.",
    "I'd like to withdraw ${amount:,} from my Obsidian account.",
    "Can you process a ${amount:,} redemption from my account?",
    "I need ${amount:,} wired to my bank. Please sell at today's NAV.",
]

CLOSE_NARRATIVES = [
    "I'd like to close my entire position in the fund. Please liquidate everything.",
    "Please redeem my full account balance. I'm moving everything to cash.",
    "I need to close my Obsidian account completely. Please process full redemption.",
    "Liquidate my entire position please. I appreciate everything but need to move on.",
]

STATEMENT_NARRATIVES = [
    "Can you send me my current account balance and YTD performance?",
    "I'd like to see my latest statement. How has the fund been doing?",
    "Please send my account statement. My accountant needs the year-to-date figures.",
    "Can I get an updated statement showing my current holdings and performance?",
    "What's my current account value? And how are we doing vs the S&P this year?",
]

def beneficiary_narrative(current, new):
    """A client-voice request that matches what actually changes."""
    has = lambda d, rel: any(b.get("relationship") == rel for b in (d or []))
    if has(new, "Trust") and not has(current, "Trust"):
        lead = "I've set up a family trust and want it named as my primary beneficiary."
    elif has(new, "Spouse") and not has(current, "Spouse"):
        lead = "I recently got married and need to update my beneficiaries."
    elif has(current, "Spouse") and not has(new, "Spouse"):
        lead = "My family situation has changed and I need to update my beneficiaries."
    elif len([b for b in new if b["type"] == "primary"]) > 1:
        lead = "I'd like to split my account between more than one beneficiary."
    else:
        lead = "I need to update the beneficiaries on my account."
    return (f"{lead} Here is what I want on file — {format_designation(new)}. "
            f"Please tell me what you need from me to make this official.")


def weighted_choice(weights_dict):
    keys = list(weights_dict.keys())
    weights = list(weights_dict.values())
    return random.choices(keys, weights=weights, k=1)[0]

def generate_issue(client, fund, issue_type, issue_num):
    nav = fund["current_nav"]
    account_value = client["ocrff_account_value"]
    shares = client["ocrff_shares"]
    issue_id = f"ISS-{RUN_STAMP}-{issue_num:03d}"

    issue = {
        "issue_id": issue_id,
        "issue_type": issue_type,
        "account_id": client["account_id"],
        "client_name": client["name"],
        "fa_name": client["fa_name"],
        "firm": client["firm"],
        "territory": client["territory"],
        "current_shares": round(shares, 4),
        "current_account_value": round(account_value, 2),
        "current_nav": nav,
        "generated_at": datetime.now().isoformat(),
        "status": "PENDING",
        "narrative": "",
        "parameters": {},
    }

    if issue_type == "buy":
        amount = random.choice(BUY_AMOUNTS)
        shares_to_buy = round(amount / nav, 4)
        issue["narrative"] = random.choice(BUY_NARRATIVES).format(amount=amount)
        issue["parameters"] = {
            "amount_dollars": amount,
            "shares_equivalent": shares_to_buy,
            "nav": nav,
        }

    elif issue_type == "sell":
        # Never sell more than 90% of the balance (that would be a full close).
        # The old cap of $25,000 made every sell the same size.
        max_sell = account_value * 0.9
        eligible = [a for a in SELL_AMOUNTS if a <= max_sell]
        if eligible:
            amount = random.choice(eligible)
        else:                                    # small account: round down to $1K
            amount = max(1000, int(max_sell // 1000 * 1000))
        shares_to_sell = round(amount / nav, 4)
        issue["narrative"] = random.choice(SELL_NARRATIVES).format(amount=amount)
        issue["parameters"] = {
            "amount_dollars": amount,
            "shares_equivalent": shares_to_sell,
            "nav": nav,
        }

    elif issue_type == "full_close":
        issue["narrative"] = random.choice(CLOSE_NARRATIVES)
        issue["parameters"] = {
            "amount_dollars": round(account_value, 2),
            "shares_equivalent": round(shares, 4),
            "nav": nav,
            "is_full_close": True,
        }

    elif issue_type == "statement":
        issue["narrative"] = random.choice(STATEMENT_NARRATIVES)
        issue["parameters"] = {}

    elif issue_type == "address":
        cur_addr = client.get("address")
        new_addr = random_address(random, client.get("territory"),
                                  cross_territory_prob=0.25, exclude=cur_addr)
        cur_mail = bool(client.get("mail_to_fa", False))
        new_mail = (not cur_mail) if random.random() < 0.25 else cur_mail
        narrative = (f"I've recently moved. Please update my address to: "
                     f"{format_address(new_addr)}.")
        if new_mail != cur_mail:
            narrative += (" And please have my statements sent to my advisor's office from now on."
                          if new_mail else
                          " Also, please send my statements straight to me at the new address, not to my advisor.")
        issue["narrative"] = narrative
        issue["parameters"] = {
            "new_street":  new_addr["street"],
            "new_unit":    new_addr["unit"],
            "new_city":    new_addr["city"],
            "new_state":   new_addr["state"],
            "new_zip":     new_addr["zip"],
            "new_mail_to_fa":     new_mail,
            "current_mail_to_fa": cur_mail,
            "current_address":    cur_addr,
        }

    elif issue_type == "beneficiary":
        cur_d = client.get("beneficiary") or []
        new_d = make_designation(random, client["name"])
        for _ in range(10):
            if not designation_equal(new_d, cur_d):
                break
            new_d = make_designation(random, client["name"])
        issue["narrative"] = beneficiary_narrative(cur_d, new_d)
        issue["parameters"] = {
            "new_designation":     new_d,
            "current_designation": cur_d,
        }

    elif issue_type == "holdings":
        issue["narrative"] = random.choice(HOLDINGS_ASKS)
        issue["parameters"] = {}

    return issue


def main():
    parser = argparse.ArgumentParser(description="Orbit Issue Generator")
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--force-type", choices=list(ISSUE_WEIGHTS.keys()),
                        help="Force all issues to this type (for testing)")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--output", default=OUT_FILE)
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    print("=" * 55)
    print(" 🌀 Orbit Issue Generator")
    print(f"    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 55)

    with open(CLIENTS_FILE) as f:
        client_data = json.load(f)
    with open(FUND_FILE) as f:
        fund = json.load(f)

    if client_data.get("schema_version") != SCHEMA_VERSION:
        raise SystemExit("❌ crm_clients.json is not schema v2 yet.\n"
                         "   Run: python3 migrate_crm_v2.py")

    # Only active accounts with a balance can raise service issues
    pool = [c for c in client_data["clients"]
            if c.get("status", "ACTIVE") == "ACTIVE" and c["ocrff_account_value"] > 0]
    # Weight toward clients with larger positions for realism; no repeats in a run
    selected = []
    for _ in range(min(args.count, len(pool))):
        weights = [c["ocrff_account_value"] for c in pool]
        pick = random.choices(pool, weights=weights, k=1)[0]
        selected.append(pick)
        pool.remove(pick)

    issues = []
    print(f"\n📋 Generating {args.count} issues...\n")

    for i, client in enumerate(selected):
        itype = args.force_type or weighted_choice(ISSUE_WEIGHTS)
        issue = generate_issue(client, fund, itype, i+1)
        issues.append(issue)

        icon = {"buy":"💰","sell":"💸","full_close":"🚪","statement":"📄",
                "address":"📮","beneficiary":"👤","holdings":"📊"}.get(itype,"❓")
        print(f"  {icon} {issue['issue_id']} | {itype.upper():<12} | "
              f"{client['name']:<22} | ${client['ocrff_account_value']:>10,.2f}")
        print(f"     FA: {client['fa_name']} | {client['firm']}")
        print(f"     \"{issue['narrative'][:70]}...\"" if len(issue['narrative'])>70
              else f"     \"{issue['narrative']}\"")
        print()

    output = {
        "generated_at": datetime.now().isoformat(),
        "count": len(issues),
        "nav": fund["current_nav"],
        "issues": issues,
    }

    with open(args.output, "w") as f:
        json.dump(output, f, indent=2)

    print(f"✅ Saved: {args.output}")
    print(f"▶️  Next: python3 orbit.py --issues {args.output}")


if __name__ == "__main__":
    main()
