#!/usr/bin/env python3
"""
generate_issues.py — Orbit Issue Generator
Pulls 5 random clients from crm_clients.json and assigns realistic
customer service issues. Outputs orbit_issues_YYYYMMDD_HHMM.json.

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

import json, os, random, argparse
from datetime import datetime, date

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
PAPER_DIR   = os.path.dirname(SCRIPT_DIR)
VENUS_DIR   = os.path.join(PAPER_DIR, "venus")
FUND_FILE   = os.path.join(VENUS_DIR, "ocrff_fund.json")
CLIENTS_FILE= os.path.join(VENUS_DIR, "crm_clients.json")
OUT_FILE    = os.path.join(SCRIPT_DIR, "orbit_issues_latest.json")

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

ADDRESSES = [
    ("1234 Ocean Ave", "Santa Monica", "CA", "90402"),
    ("567 Elm Street", "La Jolla", "CA", "92037"),
    ("890 Pacific Coast Hwy", "Malibu", "CA", "90265"),
    ("321 Sunset Blvd", "Beverly Hills", "CA", "90210"),
    ("456 Harbor Drive", "Coronado", "CA", "92118"),
    ("789 Market Street", "San Francisco", "CA", "94105"),
    ("234 Wilshire Blvd", "Los Angeles", "CA", "90036"),
    ("901 Mission Street", "San Diego", "CA", "92103"),
]

BENEFICIARY_NAMES = [
    "Sarah {last}", "Michael {last}", "Emma {last}", "James {last}",
    "Jennifer {last} Trust", "Robert {last} Family Trust",
    "The {last} Family", "David {last}",
]

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

def weighted_choice(weights_dict):
    keys = list(weights_dict.keys())
    weights = list(weights_dict.values())
    return random.choices(keys, weights=weights, k=1)[0]

def generate_issue(client, fund, issue_type, issue_num):
    nav = fund["current_nav"]
    last = client["name"].split()[-1]
    account_value = client["ocrff_account_value"]
    shares = client["ocrff_shares"]
    issue_id = f"ISS-{datetime.now().strftime('%Y%m%d')}-{issue_num:03d}"

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
        # Don't generate a sell larger than account value
        max_sell = min(account_value * 0.9, 25000)
        eligible = [a for a in SELL_AMOUNTS if a <= max_sell]
        if not eligible:
            eligible = [min(SELL_AMOUNTS)]
        amount = random.choice(eligible)
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
        new_addr = random.choice(ADDRESSES)
        issue["narrative"] = (
            f"I've recently moved. Please update my address to: "
            f"{new_addr[0]}, {new_addr[1]}, {new_addr[2]} {new_addr[3]}."
        )
        issue["parameters"] = {
            "new_street":  new_addr[0],
            "new_city":    new_addr[1],
            "new_state":   new_addr[2],
            "new_zip":     new_addr[3],
        }

    elif issue_type == "beneficiary":
        template = random.choice(BENEFICIARY_NAMES)
        new_bene = template.format(last=last)
        issue["narrative"] = (
            f"I need to update my beneficiary on this account to {new_bene}. "
            f"Please make this change as soon as possible."
        )
        issue["parameters"] = {
            "new_beneficiary": new_bene,
            "current_beneficiary": client.get("beneficiary", ""),
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

    if args.seed:
        random.seed(args.seed)

    print("=" * 55)
    print(" 🌀 Orbit Issue Generator")
    print(f"    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 55)

    with open(CLIENTS_FILE) as f:
        client_data = json.load(f)
    with open(FUND_FILE) as f:
        fund = json.load(f)

    clients = client_data["clients"]
    # Weight toward clients with larger positions for realism
    weights = [max(c["ocrff_account_value"], 1) for c in clients]
    selected = random.choices(clients, weights=weights, k=args.count)

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
