#!/usr/bin/env python3
"""
repair_venus_nav.py — one-time fix for Venus closes booked BEFORE new-client accounts existed.

Old behaviour: a close added cash to VMRXX but issued no shares, so NAV jumped (+0.34% on 2026-10-06).
This script finds those inflow_log entries (source "New client: <FA name>", not yet repaired), opens
the new client accounts that money should have bought, issued at the NAV from just BEFORE the cash
came in, then refreshes the NAV.  Result: NAV returns to where it was before the closes, and the
cash is backed by real client shares.

    pre-injection NAV = (portfolio value - unissued deal money) / client shares

That formula is right only if prices have not moved since the closes were booked (true when you
repair the same day).  It prints the NAV currently on file next to it as a cross-check; use
--nav X to force a specific NAV.

Usage (from paper_trade/):
    python3 venus/repair_venus_nav.py            # preview — writes nothing
    python3 venus/repair_venus_nav.py --apply    # backs up files, then fixes
Safe to run twice: repaired inflow entries are flagged and skipped.
"""
import argparse, json, os, random, shutil, sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import nav_engine
import venus_new_clients as nc

ADVISORS = os.path.join(nc.SCRIPT_DIR, "crm_advisors.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--nav", type=float, help="issue shares at this NAV instead of the computed pre-injection NAV")
    a = ap.parse_args()

    holdings = nav_engine._load(nc.HOLDINGS_FILE)
    crm = nav_engine._load(nc.CLIENTS_FILE)
    advisors = nav_engine._load(ADVISORS)
    fa_by_name = {f["name"]: f for f in advisors["advisors"]}

    pending = [e for e in holdings["summary"].get("inflow_log", [])
               if str(e.get("source", "")).startswith("New client:") and not e.get("repaired")]
    if not pending:
        print("Nothing to repair — no unrepaired 'New client:' inflows."); return
    D = round(sum(e["amount"] for e in pending), 2)
    cur = nav_engine.compute_nav(holdings, crm)
    nav = a.nav or round((cur["portfolio_value"] - D) / cur["shares"], 6)
    print(f"portfolio ${cur['portfolio_value']:,.2f} | client shares {cur['shares']:,.4f} | NAV now ${cur['nav']:,.4f}")
    print(f"unissued deals: {len(pending)} = ${D:,.2f}  →  pre-injection NAV ${nav:,.4f}")
    try:
        on_file = nav_engine._load(nav_engine.FUND_FILE).get("current_nav")
        print(f"NAV on file in ocrff_fund.json: ${on_file:,.4f}")
    except Exception:
        pass

    rng = random.Random(int(D * 100))   # same split in preview and apply
    new_accounts, total_shares = [], 0.0
    for e in pending:
        name = e["source"].split(":", 1)[1].strip()
        fa = fa_by_name.get(name)
        if not fa:
            sys.exit(f"❌ no FA named '{name}' in crm_advisors.json — fix the inflow source or add --nav and handle by hand")
        accts = nc.build_accounts(fa, e["amount"], nav, crm["clients"] + new_accounts, rng, today=e.get("date"))
        for x in accts:
            x.pop("_purchase_amount", None)
        new_accounts += accts
        e["_accts"] = [x["account_id"] for x in accts]
        print(f"  {name}: ${e['amount']:,.2f} → {len(accts)} accounts " + ", ".join(e["_accts"]))
    total_shares = sum(x["ocrff_shares"] for x in new_accounts)
    nav_after = (cur["portfolio_value"]) / (cur["shares"] + total_shares)
    print(f"issuing {total_shares:,.4f} shares → NAV after ${nav_after:,.4f}")

    if not a.apply:
        print("\n(preview only — add --apply)"); return

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for p in (nc.CLIENTS_FILE, nc.HOLDINGS_FILE, ADVISORS):
        root, ext = os.path.splitext(p); shutil.copy2(p, f"{root}_BEFORE_REPAIR_{stamp}{ext}")
    crm["clients"].extend(new_accounts); crm["total"] = len(crm["clients"])
    for e in pending:
        e["repaired"] = True; e["account_ids"] = e.pop("_accts")
    for e in pending:   # additive FA bookkeeping
        fa = fa_by_name[e["source"].split(":", 1)[1].strip()]
        fa["oc_aum"] = round(e["amount"], 2) if not fa.get("oc_aum") else fa["oc_aum"]
        fa["oc_clients"] = max(fa.get("oc_clients") or 0, len(e["account_ids"]))
        fa["oc_account_ids"] = sorted(set(fa.get("oc_account_ids", [])) | set(e["account_ids"]))
    nav_engine._save(nc.CLIENTS_FILE, crm)
    nav_engine._save(nc.HOLDINGS_FILE, holdings)
    nav_engine._save(ADVISORS, advisors)
    nav_engine.refresh(apply=True, source="repair_venus_nav")
    print("✅ repaired. Run: python3 nav_engine.py")

if __name__ == "__main__":
    main()
