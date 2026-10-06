#!/usr/bin/env python3
"""
nav_engine.py — the one place OCRFF's NAV is calculated.

    NAV per share  =  portfolio value  /  shares owned by clients

  portfolio value : positions at their last price + VMRXX cash   (neptune_holdings.json)
  shares owned    : sum of ocrff_shares over every client        (venus/crm_clients.json)

Client buys and sells happen AT the NAV, so they add or remove shares and dollars in the same
proportion and leave NAV unchanged. Only price moves (update_prices.py) change NAV.

What refresh() writes:
  venus/ocrff_fund.json   current_nav, nav_date, shares_outstanding, current_aum   (all other keys are left alone)
  venus/crm_clients.json  every client's ocrff_account_value = ocrff_shares x NAV
  venus/nav_history.json  one line per refresh: date, NAV, portfolio value, shares

Usage:
  python3 nav_engine.py                      show portfolio value, shares, NAV (writes nothing)
  python3 nav_engine.py --apply              recalculate NAV and client values and save them
  python3 nav_engine.py --restore-holdings neptune_holdings_ORIGINAL.json
                                             put the holdings back to that snapshot first
                                             (current file is backed up), then use --apply
"""

import argparse
import json
import os
import shutil
import sys
from datetime import date, datetime

PAPER_DIR = os.path.dirname(os.path.abspath(__file__))
VENUS_DIR = os.path.join(PAPER_DIR, "venus")
HOLDINGS_FILE = os.path.join(PAPER_DIR, "neptune_holdings.json")
FUND_FILE = os.path.join(VENUS_DIR, "ocrff_fund.json")
CLIENTS_FILE = os.path.join(VENUS_DIR, "crm_clients.json")
HISTORY_FILE = os.path.join(VENUS_DIR, "nav_history.json")


def _load(path):
    with open(path) as f:
        return json.load(f)


def _save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _backup_once(path, tag):
    """Keep a copy of a file as it was before floating NAV touched it (only the first time)."""
    root, ext = os.path.splitext(path)
    bak = "{}_{}{}".format(root, tag, ext)
    if os.path.exists(path) and not os.path.exists(bak):
        shutil.copy2(path, bak)
    return bak


# ── the calculation ─────────────────────────────────────────────────────────

def portfolio_value(holdings):
    """Positions at last price + cash. Recomputed from the positions, not read from the summary."""
    invested = sum(p.get("market_value", 0) for p in holdings.get("positions", {}).values())
    return round(invested + holdings["summary"]["cash_value"], 2)


def shares_outstanding(clients):
    return round(sum(c.get("ocrff_shares", 0) or 0 for c in clients), 6)


def compute_nav(holdings=None, crm=None):
    holdings = holdings or _load(HOLDINGS_FILE)
    crm = crm or _load(CLIENTS_FILE)
    v = portfolio_value(holdings)
    s = shares_outstanding(crm["clients"])
    if s <= 0:
        raise ValueError("no client shares outstanding — cannot calculate NAV")
    return {"nav": round(v / s, 6), "portfolio_value": v, "shares": s,
            "cash": holdings["summary"]["cash_value"]}


def refresh(apply=True, source="refresh", reprice_clients=True, quiet=False):
    """Recalculate NAV; with apply=True save it and re-value every client account at it."""
    holdings = _load(HOLDINGS_FILE)
    crm = _load(CLIENTS_FILE)
    r = compute_nav(holdings, crm)
    fund = _load(FUND_FILE)
    r["previous_nav"] = fund.get("current_nav")
    r["clients_repriced"] = 0
    if not apply:
        return r

    _backup_once(FUND_FILE, "BEFORE_FLOATING_NAV")
    fund["current_nav"] = r["nav"]
    fund["shares_outstanding"] = r["shares"]                       # client shares, so the one-pager cannot drift
    fund["current_aum"] = round(r["nav"] * r["shares"], 2)         # = portfolio value
    # The NAV is as of the date the holdings were last priced, not the day this script ran.
    fund["nav_date"] = str(holdings.get("last_updated") or date.today().isoformat())[:10]
    _save(FUND_FILE, fund)

    if reprice_clients:
        _backup_once(CLIENTS_FILE, "BEFORE_FLOATING_NAV")
        n = 0
        for c in crm["clients"]:
            new = round((c.get("ocrff_shares", 0) or 0) * r["nav"], 2)
            if c.get("ocrff_account_value") != new:
                c["ocrff_account_value"] = new
                n += 1
        _save(CLIENTS_FILE, crm)
        r["clients_repriced"] = n

    hist = _load(HISTORY_FILE) if os.path.exists(HISTORY_FILE) else {"history": []}
    hist["history"].append({"at": datetime.now().isoformat(timespec="seconds"), "source": source,
                            "nav": r["nav"], "portfolio_value": r["portfolio_value"],
                            "shares_outstanding": r["shares"], "cash": r["cash"]})
    _save(HISTORY_FILE, hist)
    if not quiet:
        print("  NAV ${:,.6f}  =  portfolio ${:,.2f}  /  {:,.4f} client shares   (was {})".format(
            r["nav"], r["portfolio_value"], r["shares"],
            "${:,.4f}".format(r["previous_nav"]) if r["previous_nav"] is not None else "n/a"))
    return r


# ── restore a holdings snapshot ────────────────────────────────────────────

def restore_holdings(snapshot_path):
    snap = _load(snapshot_path)
    if "positions" not in snap or "summary" not in snap:
        sys.exit("❌ {} does not look like a neptune_holdings file".format(snapshot_path))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if os.path.exists(HOLDINGS_FILE):
        root, ext = os.path.splitext(HOLDINGS_FILE)
        shutil.copy2(HOLDINGS_FILE, "{}_BEFORE_RESTORE_{}{}".format(root, stamp, ext))
    _save(HOLDINGS_FILE, snap)
    print("  holdings restored from {}  (value ${:,.2f}, as of {})".format(
        os.path.basename(snapshot_path), portfolio_value(snap), snap.get("last_updated")))


# ── command line ────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="OCRFF floating NAV = portfolio value / client shares")
    ap.add_argument("--apply", action="store_true", help="save the NAV and re-value all client accounts")
    ap.add_argument("--restore-holdings", metavar="FILE", help="reset neptune_holdings.json to this snapshot first")
    ap.add_argument("--no-reprice-clients", action="store_true", help="with --apply: leave client account values alone")
    a = ap.parse_args()

    print("=" * 60)
    print(" OCRFF NAV = portfolio value / client shares")
    print("=" * 60)
    if a.restore_holdings:
        restore_holdings(a.restore_holdings)
    r = refresh(apply=a.apply, source="cli", reprice_clients=not a.no_reprice_clients, quiet=True)
    crm = _load(CLIENTS_FILE)
    active = sum(1 for c in crm["clients"] if c.get("ocrff_shares", 0) > 0)
    print("  portfolio value : ${:>16,.2f}   (cash ${:,.2f})".format(r["portfolio_value"], r["cash"]))
    print("  client shares   : {:>17,.4f}   ({} accounts holding shares)".format(r["shares"], active))
    print("  NAV per share   : ${:>16,.4f}".format(r["nav"]))
    print("  NAV on file     : {}".format("${:,.4f}".format(r["previous_nav"]) if r["previous_nav"] is not None else "n/a"))
    if a.apply:
        print("\n  ✅ saved: ocrff_fund.json, {} client account values, nav_history.json".format(r["clients_repriced"]))
    else:
        print("\n  (nothing saved — add --apply to write it)")


if __name__ == "__main__":
    main()
