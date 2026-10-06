#!/usr/bin/env python3
"""
ytd_check.py — where does the fund's YTD return come from?

Reads the holdings snapshot and the cached prices (prices.csv from backtest_fund_returns.py) and prints,
for every position, its YTD price return and its contribution, then the fund's YTD return under
four different assumptions so you can see which one gives which number.

  A  PORTFOLIO WEIGHTING   today's (snapshot) weights x each holding's YTD return, cash earning the cash rate
  A2 same, positions only  cash left out, weights re-scaled to 100%
  B  FIXED SHARES          hold the snapshot share counts all year (start-of-year weights drift from A);
                           every position that has prices on the start date is included
  C  BACKTEST STYLE        like B, but positions that listed after --late-cutoff are dropped,
                           which is what `backtest_fund_returns.py --late-start drop` does to every period

Usage:
  python3 ytd_check.py --holdings neptune_holdings_ORIGINAL.json --prices-csv prices.csv
  python3 ytd_check.py --holdings neptune_holdings_ORIGINAL.json --prices-csv prices.csv --end 2026-06-19
"""

import argparse
import json
import sys

import pandas as pd


def main():
    ap = argparse.ArgumentParser(description="YTD return of the fund under different weighting assumptions")
    ap.add_argument("--holdings", required=True)
    ap.add_argument("--prices-csv", required=True)
    ap.add_argument("--end", default=None, help="YYYY-MM-DD (default: last date in the prices file)")
    ap.add_argument("--sp-ticker", default="SPY")
    ap.add_argument("--cash-rate", type=float, default=0.03, help="annual yield on cash (default 0.03)")
    ap.add_argument("--late-cutoff", default="2020-01-09",
                    help="C drops positions whose first price is after this date (default: backtest's rule)")
    a = ap.parse_args()

    h = json.load(open(a.holdings))
    pos = h["positions"]
    cash = float(h["summary"]["cash_value"])
    px = pd.read_csv(a.prices_csv, index_col=0, parse_dates=True).sort_index()

    end = pd.Timestamp(a.end) if a.end else px.index[-1]
    end_ts = px.index[px.index <= end][-1]
    start_ts = px.index[px.index <= pd.Timestamp("{}-12-31".format(end_ts.year - 1))][-1]   # last close of prior year
    frac = (end_ts - start_ts).days / 365.0

    mv_total = sum(p["market_value"] for p in pos.values()) + cash
    rows, missing = [], []
    for t, p in pos.items():
        if t not in px.columns or pd.isna(px.loc[:start_ts, t].dropna().tail(1)).all():
            missing.append(t)
            continue
        s = px[t].loc[:start_ts].dropna()
        e = px[t].loc[:end_ts].dropna()
        if s.empty or e.empty:
            missing.append(t)
            continue
        p0, p1 = float(s.iloc[-1]), float(e.iloc[-1])
        first = px[t].first_valid_index()
        rows.append({"t": t, "sh": p["shares"], "w": p["market_value"] / mv_total, "p0": p0, "p1": p1,
                     "r": p1 / p0 - 1, "snap": p.get("last_price"), "first": first})
    df = pd.DataFrame(rows).set_index("t")
    df["contrib"] = df["w"] * df["r"]
    w_cash = cash / mv_total
    cash_r = a.cash_rate * frac

    # A: snapshot weights x YTD returns
    A = df["contrib"].sum() + w_cash * cash_r
    A2 = (df["contrib"].sum() / df["w"].sum())

    # B: fixed share counts all year
    v0 = df["sh"] * df["p0"]
    v1 = df["sh"] * df["p1"]
    cash_w = w_cash                                    # cash held at the same share of the fund
    B_pos = v1.sum() / v0.sum() - 1
    B = (1 - cash_w) * B_pos + cash_w * cash_r

    # C: backtest style — late listings dropped
    keep = df["first"] <= pd.Timestamp(a.late_cutoff)
    C_pos = v1[keep].sum() / v0[keep].sum() - 1
    C = (1 - cash_w) * C_pos + cash_w * cash_r

    sp = None
    if a.sp_ticker in px.columns:
        s0 = float(px[a.sp_ticker].loc[:start_ts].dropna().iloc[-1])
        s1 = float(px[a.sp_ticker].loc[:end_ts].dropna().iloc[-1])
        sp = s1 / s0 - 1

    print("YTD window: {:%Y-%m-%d} close -> {:%Y-%m-%d} close   ({} positions with prices, cash {:.1f}% of fund)".format(
        start_ts, end_ts, len(df), w_cash * 100))
    if missing:
        print("no price data for:", ", ".join(missing))
    print()
    print("{:<7}{:>8}{:>10}{:>10}{:>9}{:>9}{:>11}   {}".format(
        "ticker", "weight", "start px", "end px", "YTD", "contrib", "snapshot px", "note"))
    for t, r in df.sort_values("contrib", ascending=False).iterrows():
        note = []
        if r["first"] > pd.Timestamp(a.late_cutoff):
            note.append("late listing (C drops it)")
        if r["snap"] and abs(r["p1"] / r["snap"] - 1) > 0.5:
            note.append("end px vs snapshot px {:+.0f}%".format((r["p1"] / r["snap"] - 1) * 100))
        print("{:<7}{:>7.1f}%{:>10.2f}{:>10.2f}{:>8.1f}%{:>8.2f}%{:>11}   {}".format(
            t, r["w"] * 100, r["p0"], r["p1"], r["r"] * 100, r["contrib"] * 100,
            "{:.2f}".format(r["snap"]) if r["snap"] else "-", "; ".join(note)))
    print("\n{:<44}{:>8}".format("A   portfolio weighting (snapshot weights)", "{:+.1f}%".format(A * 100)))
    print("{:<44}{:>8}".format("A2  same, positions only (cash left out)", "{:+.1f}%".format(A2 * 100)))
    print("{:<44}{:>8}".format("B   fixed shares, all positions with prices", "{:+.1f}%".format(B * 100)))
    print("{:<44}{:>8}".format("C   backtest style (late listings dropped)", "{:+.1f}%".format(C * 100)))
    if sp is not None:
        print("{:<44}{:>8}".format("    {} for reference".format(a.sp_ticker), "{:+.1f}%".format(sp * 100)))
    print("\nA vs B differ because A puts today's big weights on this year's movers; B starts the year "
          "with the weights those share counts had then.")


if __name__ == "__main__":
    main()
