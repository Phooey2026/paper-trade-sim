#!/usr/bin/env python3
"""
backtest_fund_returns.py — Rebuild OCRFF's performance table from real price history.

Assumption (Jay's): the fund has held the share quantities in neptune_holdings(_ORIGINAL).json
since the inception date (2020-01-02), buy-and-hold, cost basis ignored. Fund value on any day is
    sum(shares x price) + cash
and returns are measured from that value series. The S&P 500 is measured over the same dates.

Total return: prices are dividend-adjusted closes (yfinance auto_adjust), for the fund AND the S&P
proxy (default SPY), so the two are comparable. Use --sp-ticker ^GSPC for price-only.

Data limits (all reported when you run it):
  * Tickers that did not exist at the start (GEV from 2024, BITQ from Nov 2021) are held FLAT at their
    first available price before they existed (--late-start flat, the default), or dropped (--late-start drop).
  * Cash (VMRXX): --cash-mode weight (default) keeps cash at the holdings file's cash_pct of the fund,
    earning --cash-rate (default 3%); exclude = positions only; fixed = dollar amount held flat.

Usage
    python3 backtest_fund_returns.py --holdings venus/../neptune_holdings_ORIGINAL.json \
            --fund venus/ocrff_fund.json                       # preview only (nothing written)
    python3 backtest_fund_returns.py ... --write               # backup + update performance/annual_returns
    python3 backtest_fund_returns.py ... --write --update-risk --rebase-inception-nav
    python3 backtest_fund_returns.py ... --save-prices prices.csv   # cache the download
    python3 backtest_fund_returns.py ... --prices-csv prices.csv    # reuse the cache (no network)
"""
import argparse
import json
import math
import os
import shutil
import sys
from datetime import datetime

import pandas as pd

START_DEFAULT = "2020-01-02"


# ── Data ───────────────────────────────────────────────────────────────────

def download_prices(tickers, start, end=None):
    try:
        import yfinance as yf
    except ImportError:
        sys.exit("❌ yfinance not installed (pip install yfinance) — or use --prices-csv.")
    df = yf.download(sorted(set(tickers)), start=start, end=end, auto_adjust=True,
                     progress=False, group_by="column")
    px = df["Close"] if "Close" in df else df
    if isinstance(px, pd.Series):
        px = px.to_frame(tickers[0])
    px = px.dropna(how="all")
    px.index = pd.to_datetime(px.index).tz_localize(None)
    return px


# ── Portfolio value series ─────────────────────────────────────────────────

def fund_series(px, shares, cash, start, late_start="flat", cash_rate=0.0,
                cash_mode="weight", cash_weight=0.0):
    """Returns (value Series, notes list)."""
    notes = []
    px = px[px.index >= pd.Timestamp(start)]
    cols = [t for t in shares if t in px.columns]
    missing = [t for t in shares if t not in px.columns]
    if missing:
        notes.append("no price data at all for: {} (dropped)".format(", ".join(missing)))
    val = pd.Series(0.0, index=px.index)
    for t in cols:
        s = px[t]
        first = s.first_valid_index()
        if first is None:
            notes.append("{}: no prices (dropped)".format(t)); continue
        if first > px.index[0] + pd.Timedelta(days=7):
            if late_start == "drop":
                notes.append("{}: first price {:%Y-%m-%d} — dropped (late start)".format(t, first))
                continue
            notes.append("{}: first price {:%Y-%m-%d} — held flat at that price before then".format(t, first))
        s = s.bfill().ffill()          # flat before listing / over gaps
        val = val + s * shares[t]
    if cash_mode == "exclude":
        notes.append("cash excluded (equity + ETF positions only)")
    elif cash_mode == "fixed":
        days = (px.index - px.index[0]).days.values / 365.25
        val = val + cash * (1 + cash_rate) ** days
        notes.append("cash held as a FIXED ${:,.0f} (its 2020 weight is inflated — see --cash-mode weight)".format(cash))
    else:  # weight: cash is a constant share of the fund, rebalanced daily, earning cash_rate
        inv = val
        dt = pd.Series(inv.index, index=inv.index).diff().dt.days.fillna(0) / 365.25
        r = (1 - cash_weight) * inv.pct_change().fillna(0) + cash_weight * ((1 + cash_rate) ** dt - 1)
        val = (1 + r).cumprod() * inv.iloc[0] / (1 - cash_weight)
        notes.append("cash = constant {:.1%} of the fund (rebalanced daily), earning {:.1%}/yr".format(cash_weight, cash_rate))
    return val, notes


def prepare_prices(px, tickers, start, late_start="flat"):
    """Trim to start, drop/flat-fill late listings. Returns (prices DataFrame, notes)."""
    notes = []
    px = px[px.index >= pd.Timestamp(start)]
    keep = {}
    for t in tickers:
        if t not in px.columns or px[t].first_valid_index() is None:
            notes.append("{}: no price data (dropped)".format(t)); continue
        first = px[t].first_valid_index()
        if first > px.index[0] + pd.Timedelta(days=7):
            if late_start == "drop":
                notes.append("{}: first price {:%Y-%m-%d} — dropped (late start)".format(t, first)); continue
            notes.append("{}: first price {:%Y-%m-%d} — held flat at that price before then".format(t, first))
        keep[t] = px[t].bfill().ffill()
    return pd.DataFrame(keep), notes


def weights_series(prices, weights, rebalance="annual", start_value=None):
    """Fund value if the CURRENT model weights are held from the start and rebalanced on schedule."""
    cols = list(weights)
    w = pd.Series({c: weights[c] for c in cols}); w = w / w.sum()
    P = prices[cols].values
    idx = prices.index
    v = float(start_value or 100.0)
    units = v * w.values / P[0]
    out = [v]
    for i in range(1, len(idx)):
        v = float((units * P[i]).sum())
        out.append(v)
        d, p = idx[i], idx[i - 1]
        reb = (rebalance == "monthly" and d.month != p.month) or \
              (rebalance == "quarterly" and d.month != p.month and d.month in (1, 4, 7, 10)) or \
              (rebalance == "annual" and d.year != p.year)
        if reb:
            units = v * w.values / P[i]
    return pd.Series(out, index=idx)


def contributions(prices, shares, cash_value=0.0):
    """Dollar gain per ticker for fixed shares (first day -> last day)."""
    rows = []
    for t, n in shares.items():
        if t in prices.columns:
            v0, v1 = n * prices[t].iloc[0], n * prices[t].iloc[-1]
            rows.append((t, v0, v1, v1 - v0))
    df = pd.DataFrame(rows, columns=["ticker", "start", "end", "gain"]).set_index("ticker")
    tot0 = df["start"].sum() + cash_value
    df["start_w%"] = df["start"] / tot0 * 100
    df["multiple"] = df["end"] / df["start"]
    df["gain_share%"] = df["gain"] / df["gain"].sum() * 100
    return df.sort_values("gain", ascending=False)


# ── Return maths ───────────────────────────────────────────────────────────

class PeriodFund:
    """
    Fixed-share fund return for ANY window, using every position that has prices at the window's start.

    A position that listed after the window opens (e.g. GEV in 2024, BITQ in 2021) is left out of windows that
    begin before it existed, and included in every window that begins after. Cash is handled exactly as in
    fund_series (constant share of the fund, rebalanced daily, earning cash_rate) — but rebuilt per window.
    """
    def __init__(self, px, shares, cash, cash_rate, cash_mode, cash_weight, fund_index):
        self.px, self.shares, self.cash = px, shares, cash
        self.rate, self.mode, self.cw = cash_rate, cash_mode, cash_weight
        self.index = fund_index
        self.last = {}

    def _on_or_before(self, ts):
        idx = self.index[self.index <= pd.Timestamp(ts)]
        return idx[-1] if len(idx) else None

    def __call__(self, t0, t1):
        d0, d1 = self._on_or_before(t0), self._on_or_before(t1)
        if d0 is None or d1 is None or d1 <= d0:
            return None
        win = self.px.loc[d0:d1]
        val = pd.Series(0.0, index=win.index)
        used, omitted = [], []
        for t, n in self.shares.items():
            if t not in self.px.columns:
                omitted.append(t); continue
            col = self.px[t].dropna()
            if col.empty or col.index[0] > d0 + pd.Timedelta(days=7) or col.index[-1] < d1 - pd.Timedelta(days=7):
                omitted.append(t); continue
            val = val + win[t].bfill().ffill() * n
            used.append(t)
        if not used or val.iloc[0] == 0:
            return None
        if self.mode == "exclude":
            tot = val
        elif self.mode == "fixed":
            days = (win.index - win.index[0]).days.values / 365.25
            tot = val + self.cash * (1 + self.rate) ** days
        else:
            dt = pd.Series(win.index, index=win.index).diff().dt.days.fillna(0) / 365.25
            r = (1 - self.cw) * val.pct_change().fillna(0) + self.cw * ((1 + self.rate) ** dt - 1)
            tot = (1 + r).cumprod()
        self.last = {"used": len(used), "omitted": omitted}
        return {"ret": (tot.iloc[-1] / tot.iloc[0] - 1) * 100, "d0": d0, "d1": d1,
                "years": (d1 - d0).days / 365.25}


def on_or_before(series, ts):
    s = series[series.index <= ts]
    return (s.index[-1], float(s.iloc[-1])) if len(s) else (None, None)


def period_return(series, start_ts, end_ts):
    d0, v0 = on_or_before(series, start_ts)
    d1, v1 = on_or_before(series, end_ts)
    if d0 is None or v0 == 0:
        return None
    return {"ret": (v1 / v0 - 1) * 100, "d0": d0, "d1": d1, "years": (d1 - d0).days / 365.25}


def annualise(total_pct, years):
    if years <= 0:
        return None
    return ((1 + total_pct / 100) ** (1 / years) - 1) * 100


def build_periods(fund, sp, end, pf=None, log=None):
    """fund, sp: value series on the same index. Returns dict for the 'performance' block."""
    y = end.year
    specs = [
        ("ytd", pd.Timestamp(y - 1, 12, 31), False, "YTD"),
        ("six_month", end - pd.DateOffset(months=6), False, "6M"),
        ("one_year", end - pd.DateOffset(years=1), False, "1Y"),
        ("three_year_ann", end - pd.DateOffset(years=3), True, "3Y"),
        ("five_year_ann", end - pd.DateOffset(years=5), True, "5Y"),
    ]
    out = {}
    def fmt(d): return d.strftime("%b %-d, %Y")
    for key, start, ann, _ in specs:
        s = period_return(sp, start, end)
        f = (pf(start, end) if pf else period_return(fund, start, end)) if start >= fund.index[0] else None
        if not f or not s or start < fund.index[0]:
            continue
        if pf and log is not None:
            log[key] = dict(pf.last)
        fr, sr = f["ret"], s["ret"]
        if ann:
            fr, sr = annualise(fr, f["years"]), annualise(sr, s["years"])
        label = "{} – {}".format(fmt(f["d0"]), fmt(f["d1"])) + (" (annualized)" if ann else "")
        out[key] = {"ocrff": round(fr, 1), "sp500": round(sr, 1),
                    "alpha": round(fr - sr, 1), "period": label}
    start = fund.index[0]
    f = (pf(start, end) if pf else None) or period_return(fund, start, end); s = period_return(sp, start, end)
    if pf and log is not None:
        log["since_inception"] = dict(pf.last)
    fa, sa = annualise(f["ret"], f["years"]), annualise(s["ret"], s["years"])
    span = "{} – {}".format(fmt(f["d0"]), fmt(f["d1"]))
    out["since_inception_ann"] = {"ocrff": round(fa, 1), "sp500": round(sa, 1),
                                  "alpha": round(fa - sa, 1), "period": span + " (annualized)"}
    out["since_inception_total"] = {"ocrff": round(f["ret"], 1), "sp500": round(s["ret"], 1),
                                    "alpha": round(f["ret"] - s["ret"], 1), "period": span + " (cumulative)"}
    return out


def build_annual(fund, sp, end, pf=None):
    out = {}
    first = fund.index[0]
    for yr in range(first.year, end.year):            # full calendar years only
        start = first if yr == first.year else pd.Timestamp(yr - 1, 12, 31)
        f = (pf(start, pd.Timestamp(yr, 12, 31)) if pf else None) or period_return(fund, start, pd.Timestamp(yr, 12, 31))
        s = period_return(sp, start, pd.Timestamp(yr, 12, 31))
        out[str(yr)] = {"ocrff": round(f["ret"], 1), "sp500": round(s["ret"], 1)}
    return out


def risk_metrics(fund, sp, rf=0.0):
    fr = fund.pct_change().dropna(); sr = sp.pct_change().dropna()
    j = pd.concat([fr, sr], axis=1, join="inner").dropna(); j.columns = ["f", "s"]
    vol = j["f"].std() * math.sqrt(252)
    ann = (fund.iloc[-1] / fund.iloc[0]) ** (252 / len(j)) - 1
    dd = fund / fund.cummax() - 1
    trough = dd.idxmin(); peak = fund[:trough].idxmax()
    return {
        "sharpe_ratio": float(round((ann - rf) / vol, 2)),
        "max_drawdown_pct": float(round(dd.min() * 100, 1)),
        "max_drawdown_period": "{} – {}".format(peak.strftime("%b %Y"), trough.strftime("%b %Y")),
        "volatility_ann": float(round(vol * 100, 1)),
        "beta_vs_sp500": float(round(j["f"].cov(j["s"]) / j["s"].var(), 2)),
        "correlation_sp500": float(round(j["f"].corr(j["s"]), 2)),
    }


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--holdings", required=True)
    ap.add_argument("--fund", required=True)
    ap.add_argument("--start", default=START_DEFAULT)
    ap.add_argument("--end", default=None, help="YYYY-MM-DD (default: latest date with data)")
    ap.add_argument("--sp-ticker", default="SPY")
    ap.add_argument("--late-start", choices=["flat", "drop"], default="flat")
    ap.add_argument("--allocation", choices=["shares", "weights"], default="shares",
                    help="shares (default): hold today's SHARE COUNTS since inception (2020 weights = shares x 2020 "
                         "price). weights: hold today's DOLLAR WEIGHTS since inception, rebalanced on --rebalance")
    ap.add_argument("--rebalance", choices=["none", "annual", "quarterly", "monthly"], default="annual",
                    help="rebalance schedule for --allocation weights")
    ap.add_argument("--top", type=int, default=10, help="rows in the contribution table")
    ap.add_argument("--cash-mode", choices=["weight", "exclude", "fixed"], default="weight",
                    help="weight (default): cash is a constant share of the fund; exclude: positions only; "
                         "fixed: the file's dollar amount held flat since 2020 (distorts early weights)")
    ap.add_argument("--cash-weight", type=float, default=None,
                    help="cash share for --cash-mode weight (default: cash_pct in the holdings file)")
    ap.add_argument("--cash-rate", type=float, default=0.03,
                    help="annual yield on cash (default 0.03 ≈ 2020-26 money-market average)")
    ap.add_argument("--prices-csv", help="Use cached prices instead of downloading")
    ap.add_argument("--save-prices", help="Save the downloaded prices to this CSV")
    ap.add_argument("--fixed-universe", action="store_true",
                    help="old behaviour: every period uses only the positions that existed at inception "
                         "(--late-start drop then removes BITQ/GEV even from YTD). Default: each period uses every "
                         "position that has prices at that period's start")
    ap.add_argument("--write", action="store_true", help="Backup then write to the fund file")
    ap.add_argument("--update-risk", action="store_true", help="Also overwrite risk_metrics")
    ap.add_argument("--rebase-inception-nav", action="store_true",
                    help="Set inception_nav/inception_aum so NAV growth matches the backtest total return")
    a = ap.parse_args()

    with open(a.holdings) as f:
        h = json.load(f)
    with open(a.fund) as f:
        fund_json = json.load(f)
    shares = {t: float(p["shares"]) for t, p in h["positions"].items()}
    cash = float(h["summary"].get("cash_value", 0.0))

    if a.prices_csv:
        px = pd.read_csv(a.prices_csv, index_col=0, parse_dates=True)
    else:
        print("⬇  downloading {} tickers from {} ...".format(len(shares) + 1, a.start))
        px = download_prices(list(shares) + [a.sp_ticker], a.start, a.end)
        if a.save_prices:
            px.to_csv(a.save_prices); print("💾 prices cached:", a.save_prices)
    if a.sp_ticker not in px.columns:
        sys.exit("❌ no data for {}".format(a.sp_ticker))

    cw = a.cash_weight if a.cash_weight is not None else float(h["summary"].get("cash_pct", 0)) / 100
    contrib = None
    if a.allocation == "weights":
        prices, notes = prepare_prices(px, list(shares), a.start, a.late_start)
        mv = {t: shares[t] * float(h["positions"][t]["last_price"]) for t in prices.columns}
        if a.cash_mode != "exclude":
            days = (prices.index - prices.index[0]).days.values / 365.25
            prices = prices.assign(CASH=(1 + a.cash_rate) ** days)
            mv["CASH"] = cash
            notes.append("cash = {:.1%} weight, earning {:.1%}/yr".format(cash / sum(mv.values()), a.cash_rate))
        else:
            notes.append("cash excluded")
        fund = weights_series(prices, mv, a.rebalance)
        notes.append("weights = today's market-value weights, rebalanced {}".format(a.rebalance))
    else:
        fund, notes = fund_series(px, shares, cash, a.start, a.late_start, a.cash_rate, a.cash_mode, cw)
        prices, _ = prepare_prices(px, list(shares), a.start, a.late_start)
        contrib = contributions(prices, shares, cash if a.cash_mode == "fixed" else 0.0)
    sp = px[a.sp_ticker].dropna()
    idx = fund.index.intersection(sp.index)
    fund, sp = fund.loc[idx], sp.loc[idx]
    if a.end:
        fund, sp = fund[fund.index <= a.end], sp[sp.index <= a.end]
    end = fund.index[-1]

    pf = None
    if a.allocation == "shares" and not a.fixed_universe:
        pf = PeriodFund(px, shares, cash, a.cash_rate, a.cash_mode, cw, fund.index)
    plog = {}
    perf = build_periods(fund, sp, end, pf, plog)
    annual = build_annual(fund, sp, end, pf)
    risk = risk_metrics(fund, sp)

    print("\nBuy-and-hold of the {} positions + ${:,.0f} cash since {:%Y-%m-%d}".format(
        len(shares), cash, fund.index[0]))
    print("Start value ${:,.0f}  ->  end value ${:,.0f} on {:%Y-%m-%d}".format(fund.iloc[0], fund.iloc[-1], end))
    for n in notes:
        print("  note:", n)
    print("\n{:<22}{:>9}{:>9}{:>9}   {}".format("period", "OCRFF", "S&P", "alpha", "dates"))
    for k, v in perf.items():
        print("{:<22}{:>8}%{:>8}%{:>8}%   {}".format(k, v["ocrff"], v["sp500"], v["alpha"], v["period"]))
    if plog:
        print("\npositions in each period (fixed shares; positions without prices at the period start are left out):")
        for k, v in plog.items():
            print("  {:<22}{:>3} of {} positions{}".format(k, v["used"], len(shares),
                  "   left out: " + ", ".join(v["omitted"]) if v["omitted"] else ""))
    print("\nannual:", "  ".join("{} {:+.1f}/{:+.1f}".format(y, v["ocrff"], v["sp500"]) for y, v in annual.items()))
    print("risk:  ", risk)
    if contrib is not None:
        print("\nTop contributors (fixed share counts, {:%Y-%m-%d} -> {:%Y-%m-%d}):".format(prices.index[0], end))
        print("{:<7}{:>13}{:>14}{:>9}{:>10}{:>11}".format("ticker", "start $", "end $", "start wt", "multiple", "% of gain"))
        for t, r in contrib.head(a.top).iterrows():
            print("{:<7}{:>13,.0f}{:>14,.0f}{:>8.1f}%{:>9.1f}x{:>10.1f}%".format(
                t, r["start"], r["end"], r["start_w%"], r["multiple"], r["gain_share%"]))

    nav = fund_json["current_nav"]
    tot = perf["since_inception_total"]["ocrff"]
    implied_inception = nav / (1 + tot / 100)
    inc = fund_json.get("inception_nav")
    if inc:
        print("\nNAV check: file says inception NAV ${:.2f} -> current ${:.2f} ({:+.1f}%). "
              "Backtest total return {:+.1f}% would need inception NAV ${:.2f}.".format(
                  inc, nav, (nav / inc - 1) * 100, tot, implied_inception))
    else:
        print("\nNAV check: no inception_nav in the fund file; a {:+.1f}% total return implies ${:.2f}.".format(
            tot, implied_inception))

    if not a.write:
        print("\n(preview only — add --write to update {})".format(a.fund)); return

    bak = a.fund + ".bak-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(a.fund, bak); print("\n💾 backup:", bak)
    fund_json["performance"] = perf
    if annual:
        fund_json["annual_returns"] = annual
    if a.update_risk:
        fund_json["risk_metrics"] = risk
    if a.rebase_inception_nav:
        fund_json["inception_nav"] = round(implied_inception, 2)
        fund_json["inception_aum"] = round(fund_json["shares_outstanding"] * implied_inception, 2)
    fund_json["performance_asof"] = end.strftime("%Y-%m-%d")
    fund_json["_performance_method"] = (
        "Backtest of the current model portfolio from {} using dividend-adjusted "
        "closes ({} allocation); S&P proxy {}; late-listed tickers {}; cash {}. Generated {} by backtest_fund_returns.py."
        .format(a.start, a.allocation + (" " + a.rebalance if a.allocation == "weights" else ""), a.sp_ticker, "held flat pre-listing" if a.late_start == "flat" else "dropped",
                {"exclude": "excluded", "fixed": "fixed dollars", "weight": "{:.1%} weight at {:.1%}/yr".format(cw, a.cash_rate)}[a.cash_mode],
                datetime.now().strftime("%Y-%m-%d")))
    tmp = a.fund + ".tmp"
    with open(tmp, "w") as f:
        json.dump(fund_json, f, indent=2)
    os.replace(tmp, a.fund)
    print("✅ updated:", a.fund)


if __name__ == "__main__":
    main()
