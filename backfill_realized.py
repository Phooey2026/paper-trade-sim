#!/usr/bin/env python3
"""
backfill_realized.py — one-time: fill in realized gain for SELLs already in neptune_holdings.json's
trade_log (the run before realized-gain tracking existed), and start summary.realized_gain_total.

Average-cost method: realized = shares_sold x (sale price - average cost per share).  A pro-rata sale
leaves a position's cost_basis/shares unchanged, so the average cost is read from the position as it
stands now.  (If the same ticker was BOUGHT again after the sale in the same log, the average has
shifted slightly and the figure is approximate; sells of fully closed positions are skipped and listed.)

    python3 backfill_realized.py            # preview
    python3 backfill_realized.py --apply    # backs up neptune_holdings.json first
Idempotent: trades that already carry realized_gain are left alone.
"""
import argparse, json, os, shutil
from datetime import datetime

F = os.path.join(os.path.dirname(os.path.abspath(__file__)), "neptune_holdings.json")

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); a = ap.parse_args()
    h = json.load(open(F)); pos = h["positions"]; total = 0.0; n = 0
    for t in h.get("trade_log", []):
        if t.get("action") != "SELL" or "realized_gain" in t:
            continue
        p = pos.get(t["ticker"])
        if not p or not p.get("shares"):
            print(f"  skip {t['ticker']}: position closed, no average cost on file"); continue
        avg = p["cost_basis"] / p["shares"]
        g = round(t["shares"] * (t["price"] - avg), 2)
        print(f"  {t['trade_date']} SELL {t['ticker']:<5} {t['shares']:>14,.2f} sh @ {t['price']:<9} avg cost {avg:,.4f} → realized {g:+,.2f}")
        t["realized_gain"] = g; t["cost_basis_sold"] = round(t["shares"] * avg, 2)
        total += g; n += 1
    h["summary"]["realized_gain_total"] = round(h["summary"].get("realized_gain_total", 0.0) + total, 2)
    print(f"{n} trades, realized total {h['summary']['realized_gain_total']:+,.2f}")
    if a.apply and n:
        shutil.copy2(F, F.replace(".json", f"_BEFORE_REALIZED_{datetime.now():%Y%m%d_%H%M%S}.json"))
        json.dump(h, open(F + ".tmp", "w"), indent=2); os.replace(F + ".tmp", F); print("saved")
    elif not a.apply:
        print("(preview only — add --apply)")

if __name__ == "__main__":
    main()
