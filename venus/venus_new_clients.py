#!/usr/bin/env python3
"""
venus_new_clients.py — turn a Venus close into new (fictional) OCRFF client accounts.

When an FA closes, the deal money has to come from somewhere: the FA's clients buy OCRFF.
So a close of $D creates a few new client accounts that add up to exactly $D, each issued
shares AT THE CURRENT NAV (shares = dollars / NAV).  Buying at NAV adds dollars and shares in
the same proportion, so the NAV does not move.  (Adding cash without issuing shares is what
inflated the NAV by 0.34% on 2026-10-06.)

Order of operations in apply_close():
    1. read the NAV  (before any cash goes in)
    2. build the accounts and add them to venus/crm_clients.json
    3. add the cash to neptune_holdings.json (VMRXX + inflow_log)
    4. nav_engine.refresh() once, to re-value every account
If any step fails, both files are put back exactly as they were.

Lives in venus/ ; nav_engine.py lives in paper_trade/ (its parent).
"""

import json
import os
import random
import re
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(SCRIPT_DIR)
for _p in (PARENT_DIR, SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import nav_engine  # noqa: E402  (paper_trade/nav_engine.py)

CLIENTS_FILE = nav_engine.CLIENTS_FILE
HOLDINGS_FILE = nav_engine.HOLDINGS_FILE
MIN_ACCOUNT = 25_000.0        # no account smaller than this
FIRST_NEW_ID = 1001           # IDs continue after OC-CLIENT-1000

_FIRST = ["James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael", "Linda", "David", "Susan",
          "William", "Karen", "Richard", "Nancy", "Thomas", "Lisa", "Charles", "Betty", "Daniel", "Sandra",
          "Matthew", "Donna", "Anthony", "Carol", "Mark", "Ruth", "Steven", "Sharon", "Paul", "Laura",
          "Kevin", "Cynthia", "Brian", "Kathleen", "George", "Amy", "Edward", "Angela", "Ronald", "Helen"]
_LAST = ["Anderson", "Bennett", "Brooks", "Campbell", "Carter", "Coleman", "Diaz", "Edwards", "Fisher", "Foster",
         "Gray", "Griffin", "Hayes", "Howard", "Hughes", "Jenkins", "Kelly", "Long", "Morgan", "Murphy",
         "Nguyen", "Ortiz", "Parker", "Perry", "Powell", "Reed", "Russell", "Sanders", "Simmons", "Stevens",
         "Sullivan", "Torres", "Wallace", "Watson", "West", "Wood", "Wright", "Patel", "Kim", "Reyes"]
_STREETS = ["Oak St", "Maple Ave", "Sunset Blvd", "Harbor Dr", "Mission Rd", "Hillcrest Dr", "Lakeview Ln",
            "Park Ave", "Valley Rd", "Cedar Ct", "Ocean Ave", "Highland Dr", "Willow Way", "Main St"]
_REL = ["Spouse", "Child", "Sibling", "Friend", "Parent"]
_AREA = {"San Diego": "619", "Los Angeles": "310", "Orange County": "949", "San Francisco": "415",
         "Sacramento": "916", "San Jose": "408", "Oakland": "510", "Fresno": "559", "Riverside": "951"}


def _load(path):
    with open(path) as f:
        return json.load(f)


def _next_id_number(clients):
    top = FIRST_NEW_ID - 1
    for c in clients:
        m = re.search(r"(\d+)$", str(c.get("account_id", "")))
        if m:
            top = max(top, int(m.group(1)))
    return top + 1


def split_amounts(deal_value, rng, n=None):
    """Split deal_value into 2-4 varied dollar amounts (cents) that add up EXACTLY to it."""
    cents = int(round(deal_value * 100))
    if n is None:
        n = rng.choice([2, 3, 3, 4])
    n = max(1, min(n, int(deal_value // MIN_ACCOUNT) or 1))
    if n == 1:
        return [cents / 100.0]
    weights = [rng.uniform(0.5, 2.0) for _ in range(n)]
    tot = sum(weights)
    parts = [int(cents * w / tot) for w in weights]
    parts[-1] += cents - sum(parts)          # remainder lands on the last account → exact total
    return sorted((p / 100.0 for p in parts), reverse=True)


def build_accounts(fa, deal_value, nav, existing_clients, rng=None, today=None):
    """Return the new client records for one close. Does not write anything."""
    if nav <= 0:
        raise ValueError("NAV must be positive")
    rng = rng or random.Random()
    today = today or datetime.now().strftime("%Y-%m-%d")
    next_id = _next_id_number(existing_clients)
    used_names = {c.get("name") for c in existing_clients}
    # steal real-looking cities/zips from existing clients in the same territory
    same_terr = [c for c in existing_clients
                 if c.get("territory") == fa.get("territory") and c.get("address", {}).get("zip")]
    area = _AREA.get(fa.get("territory", ""), "555")

    out = []
    for amt in split_amounts(deal_value, rng):
        for _ in range(50):
            first, last = rng.choice(_FIRST), rng.choice(_LAST)
            if f"{first} {last}" not in used_names:
                break
        name = f"{first} {last}"
        used_names.add(name)
        base = rng.choice(same_terr)["address"] if same_terr else {}
        shares = round(amt / nav, 4)
        bfirst = rng.choice(_FIRST)
        out.append({
            "account_id": f"OC-CLIENT-{next_id:04d}",
            "name": name,
            "fa_id": fa["fa_id"],
            "fa_name": fa["name"],
            "firm": fa.get("firm", ""),
            "territory": fa.get("territory", ""),
            "email": f"{first.lower()}.{last.lower()}{rng.randint(10, 99)}@email.com",
            "phone": f"({area}) {rng.randint(200, 999)}-{rng.randint(0, 9999):04d}",
            "ocrff_shares": shares,
            "ocrff_account_value": round(shares * nav, 2),
            "inception_date": today,
            "beneficiary": [{"name": f"{bfirst} {last}", "relationship": rng.choice(_REL),
                             "type": "primary", "percent": 100}],
            "mail_to_fa": rng.random() < 0.4,
            "notes": f"Opened {today} via Venus close ({fa['name']}). Initial purchase ${amt:,.2f} at NAV ${nav:,.4f}.",
            "status": "ACTIVE",
            "address": {"street": f"{rng.randint(100, 9999)} {rng.choice(_STREETS)}", "unit": "",
                        "city": base.get("city", fa.get("territory", "")), "state": "CA",
                        "zip": base.get("zip", "")},
            "change_history": [],
            "_purchase_amount": amt,       # stripped before saving
        })
        next_id += 1
    return out


def _inject_cash(holdings, deal_value, fa_name, memo):
    """Add cash + inflow_log entry to an in-memory holdings dict and refresh its summary totals."""
    positions, summary = holdings.get("positions", {}), holdings.setdefault("summary", {})
    new_cash = round(summary.get("cash_value", 0) + deal_value, 2)
    summary.setdefault("inflow_log", []).append({
        "date": datetime.now().strftime("%Y-%m-%d"), "amount": round(deal_value, 2),
        "source": f"New client(s): {fa_name}", "memo": memo[:100]})
    eq = sum(p["market_value"] for p in positions.values() if p.get("asset_type") == "equity")
    etf = sum(p["market_value"] for p in positions.values() if p.get("asset_type") == "etf")
    inv = round(eq + etf, 2)
    port = round(inv + new_cash, 2)
    cb = round(sum(p.get("cost_basis", 0) for p in positions.values()), 2)
    gl = round(sum(p.get("gain_loss", 0) for p in positions.values()), 2)
    summary.update({
        "cash_value": new_cash, "total_portfolio_value": port, "total_invested": inv,
        "total_equity_value": round(eq, 2), "total_etf_value": round(etf, 2),
        "cash_pct": round(new_cash / port * 100, 4) if port else 0,
        "total_cost_basis": cb, "total_gain_loss": gl,
        "total_gain_loss_pct": round(gl / cb * 100, 2) if cb else 0.0})
    holdings["last_updated"] = datetime.now().strftime("%Y-%m-%d")


def apply_close(fa, deal_value, memo="", rng=None, refresh_nav=True):
    """
    Create the accounts, inject the cash, refresh the NAV — all or nothing.
    Returns {"accounts": [...], "nav_before": x, "nav_after": y, "shares_issued": s} or raises.
    """
    snapshots = {}
    for path in (CLIENTS_FILE, HOLDINGS_FILE):
        with open(path) as f:
            snapshots[path] = f.read()
    try:
        crm = json.loads(snapshots[CLIENTS_FILE])
        holdings = json.loads(snapshots[HOLDINGS_FILE])
        nav = nav_engine.compute_nav(holdings, crm)["nav"]          # 1. NAV before the cash goes in
        accounts = build_accounts(fa, deal_value, nav, crm["clients"], rng)
        issued = round(sum(a["ocrff_shares"] for a in accounts), 4)
        for a in accounts:
            a.pop("_purchase_amount", None)
        crm["clients"].extend(accounts)                              # 2. accounts
        crm["total"] = len(crm["clients"])
        nav_engine._save(CLIENTS_FILE, crm)
        _inject_cash(holdings, deal_value, fa["name"], memo)         # 3. cash
        nav_engine._save(HOLDINGS_FILE, holdings)
        after = nav_engine.refresh(apply=refresh_nav, source="venus_close:" + fa["fa_id"], quiet=True)  # 4. NAV
        return {"accounts": accounts, "nav_before": nav, "nav_after": after["nav"], "shares_issued": issued}
    except Exception:
        for path, text in snapshots.items():                         # roll back both files
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                f.write(text)
            os.replace(tmp, path)
        raise
