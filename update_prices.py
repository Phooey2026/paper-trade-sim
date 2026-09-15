#!/usr/bin/env python3
"""
update_prices.py — Neptune Price Updater
Reads the latest research_latest.json (stocks) and mercury_latest.json (ETFs)
and refreshes all position prices in neptune_holdings.json.

Price sources (in priority order):
  1. prices_1y last line in research_latest.json  (most recent close)
  2. "Current Price:" text in technicals/stock_info fields
  3. yfinance direct call for tickers not in pipeline data
     (AAPL, CSCO, GOOG, GOOGL, META, TSLA, V, IAU, VDE, etc.)
  4. Retain previous price if all sources unavailable (offline/cabin mode)

Usage:
    python3 update_prices.py
    python3 update_prices.py --dry-run     # Preview changes without saving
    python3 update_prices.py --verbose     # Show every price update
"""

import json
import os
import re
import sys
import argparse
import urllib.request
from datetime import datetime

SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
HOLDINGS_FILE = os.path.join(SCRIPT_DIR, "neptune_holdings.json")
DATA_DIR      = os.path.join(SCRIPT_DIR, "data")
RESEARCH_FILE = os.path.join(DATA_DIR, "research_latest.json")
MERCURY_FILE  = os.path.join(DATA_DIR, "mercury_latest.json")
WEBMCP_URL    = "http://localhost:8642"

PRICE_RE = re.compile(r'Current Price:\s*\$([0-9,]+\.?\d*)')


# ── File I/O ──────────────────────────────────────────────────────────────

def load_json(path, label):
    if not os.path.exists(path):
        print(f"⚠️  {label} not found at {path}")
        print(f"   Run: bash sync_pipeline_data.sh")
        return None
    with open(path) as f:
        return json.load(f)


# ── Price extraction from pipeline JSON ───────────────────────────────────

def extract_stock_prices(research_data):
    """
    Extract latest close prices from research JSON.
    Structure: list of {ticker, collected_at, data, summary}
    Tries prices_1y last line first, then text fields.
    """
    prices = {}
    if not research_data:
        return prices

    records = research_data if isinstance(research_data, list) else []

    for record in records:
        ticker = record.get("ticker", "").upper()
        if not ticker:
            continue
        data = record.get("data", {})
        if not isinstance(data, dict):
            continue

        price = None

        # Method 1: last date line of prices_1y  e.g. "2026-06-12: $205.19"
        p1y = data.get("prices_1y", "")
        if p1y and isinstance(p1y, str):
            lines = [l.strip() for l in p1y.strip().splitlines() if l.strip()]
            for line in reversed(lines):
                m = re.search(r'\$([0-9,]+\.?\d*)', line)
                if m and ':' in line and len(line) < 30:
                    try:
                        price = float(m.group(1).replace(',', ''))
                        break
                    except ValueError:
                        continue

        # Method 2: "Current Price: $XX" in any text field
        if not price:
            for field in ("technicals", "stock_info", "analyst_ratings", "fundamentals"):
                text = data.get(field, "")
                if not isinstance(text, str):
                    continue
                m = PRICE_RE.search(text)
                if m:
                    try:
                        price = float(m.group(1).replace(',', ''))
                        break
                    except ValueError:
                        continue

        # Method 3: last line of prices_6mo as final fallback
        if not price:
            p6mo = data.get("prices_6mo", "")
            if p6mo and isinstance(p6mo, str):
                lines = [l.strip() for l in p6mo.strip().splitlines() if l.strip()]
                for line in reversed(lines):
                    m = re.search(r'\$([0-9,]+\.?\d*)', line)
                    if m and ':' in line and len(line) < 30:
                        try:
                            price = float(m.group(1).replace(',', ''))
                            break
                        except ValueError:
                            continue

        if price and price > 0:
            prices[ticker] = price

    return prices


def extract_etf_prices(mercury_data):
    """
    Extract ETF prices from mercury JSON.

    Only BITQ is extracted here — it appears in data.crypto_prices with a
    reliable per-share price (e.g. "BITQ    $27.87  1d: +2.50% ...").

    IAU and VDE are intentionally excluded:
      - IAU appears only in the summary as a portfolio MV ("$34.4M"), not a
        per-share price.  The metals section contains spot gold per troy oz
        (~$4,100+) which is ~50x the ETF share price — both are wrong sources.
      - VDE appears only in the summary as a portfolio MV ("$18.9M").
    Both will fall through to the yfinance fallback in main().

    Per-share sanity ceiling: any price extracted here that exceeds MAX_ETF_PRICE
    is rejected and logged — defence in depth against future mercury restructuring.
    """
    # Reasonable upper bound for any single ETF share price we hold.
    # Raise this only if you deliberately buy a high-priced ETF.
    MAX_ETF_PRICE = 500.0

    prices = {}
    if not mercury_data:
        return prices

    # Only BITQ has a reliable per-share price in mercury (crypto_prices section)
    bitq_tickers = {"BITQ"}

    def scan(obj, depth=0):
        if depth > 6:
            return
        if isinstance(obj, dict):
            for v in obj.values():
                scan(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                scan(item, depth + 1)
        elif isinstance(obj, str) and len(obj) > 10:
            for ticker in bitq_tickers:
                if ticker in obj and ticker not in prices:
                    idx = obj.find(ticker)
                    snippet = obj[max(0, idx - 20):idx + 200]
                    m = PRICE_RE.search(snippet)
                    if not m:
                        m = re.search(r'\$([0-9,]+\.?\d{2})', snippet)
                    if m:
                        try:
                            candidate = float(m.group(1).replace(',', ''))
                            if candidate > MAX_ETF_PRICE:
                                print(f"   ⚠️  {ticker}: rejected price ${candidate:,.2f} "
                                      f"(exceeds sanity ceiling ${MAX_ETF_PRICE:,.0f})")
                            else:
                                prices[ticker] = candidate
                        except ValueError:
                            pass

    scan(mercury_data)
    return prices


# ── yfinance fallback for tickers not in pipeline data ────────────────────

def fetch_prices_yfinance(tickers):
    """
    Fetch prices directly via yfinance for any tickers not found in pipeline.
    Covers: AAPL, CSCO, GOOG, GOOGL, META, TSLA, V, IAU, VDE, and any others.
    Fails gracefully when offline (cabin mode).
    """
    if not tickers:
        return {}

    prices = {}
    print(f"\n🌐 yfinance fallback for: {', '.join(sorted(tickers))}")

    # Quick connectivity check — any HTTP response (even 403) means we're online
    import urllib.error, socket
    connected = False
    for test_url in [
        "https://query1.finance.yahoo.com",
        "http://www.google.com",
        "http://1.1.1.1",
    ]:
        try:
            urllib.request.urlopen(test_url, timeout=5)
            connected = True
            break
        except urllib.error.HTTPError:
            # Got an HTTP response — server is reachable, we're online
            connected = True
            break
        except (urllib.error.URLError, socket.timeout, OSError):
            # Genuine network failure — try next
            continue

    if not connected:
        print("   ⚠️  No internet — skipping yfinance fallback (cabin mode)")
        print("   These positions will retain their previous prices.")
        return prices

    try:
        import yfinance as yf
        for ticker in sorted(tickers):
            try:
                info = yf.Ticker(ticker).fast_info
                price = (getattr(info, 'last_price', None) or
                         getattr(info, 'previous_close', None))
                if price and float(price) > 0:
                    prices[ticker] = round(float(price), 4)
                    print(f"   ✅ {ticker:<6} ${prices[ticker]:>10.2f}")
                else:
                    print(f"   ⚠️  {ticker}: no price returned")
            except Exception as e:
                print(f"   ❌ {ticker}: {e}")
    except ImportError:
        print("   ⚠️  yfinance not installed — run: pip install yfinance --break-system-packages")

    return prices


# ── Apply prices to holdings ──────────────────────────────────────────────

def update_holdings(holdings, price_map, verbose=False, dry_run=False):
    """Apply new prices to all positions and recompute market values."""
    positions = holdings.get("positions", {})
    summary   = holdings.get("summary", {})
    updated   = []
    not_found = []
    run_date  = datetime.now().strftime("%Y-%m-%d")

    for ticker, pos in positions.items():
        if ticker not in price_map:
            not_found.append(ticker)
            continue

        old_price = pos.get("last_price", 0)
        new_price = price_map[ticker]
        shares    = pos["shares"]
        cost_basis = pos["cost_basis"]

        new_market_value = round(shares * new_price, 2)
        new_gain_loss    = round(new_market_value - cost_basis, 2)
        new_gl_pct       = round((new_gain_loss / cost_basis) * 100, 2) if cost_basis else 0.0
        price_chg        = round(new_price - old_price, 4)
        price_chg_pct    = round((price_chg / old_price) * 100, 2) if old_price else 0.0

        if verbose:
            direction = "▲" if new_price >= old_price else "▼"
            print(f"  {ticker:<6} ${old_price:>10.2f} → ${new_price:>10.2f}  "
                  f"{direction} {price_chg_pct:+.2f}%   MV: ${new_market_value:>12,.2f}")

        if not dry_run:
            pos["last_price"]    = new_price
            pos["market_value"]  = new_market_value
            pos["gain_loss"]     = new_gain_loss
            pos["gain_loss_pct"] = new_gl_pct
            pos["price_date"]    = run_date
            pos["price_source"]  = "pipeline_json"

        updated.append(ticker)

    # Recompute portfolio summary
    if not dry_run:
        total_equity = sum(p["market_value"] for p in positions.values()
                           if p.get("asset_type") == "equity")
        total_etf    = sum(p["market_value"] for p in positions.values()
                           if p.get("asset_type") == "etf")
        cash         = summary["cash_value"]
        total_invested  = round(total_equity + total_etf, 2)
        total_portfolio = round(total_invested + cash, 2)
        total_cb = round(sum(p["cost_basis"] for p in positions.values()), 2)
        total_gl = round(sum(p["gain_loss"]  for p in positions.values()), 2)
        gl_pct   = round((total_gl / total_cb) * 100, 2) if total_cb else 0.0

        summary.update({
            "total_portfolio_value": total_portfolio,
            "total_invested":        total_invested,
            "total_equity_value":    round(total_equity, 2),
            "total_etf_value":       round(total_etf, 2),
            "cash_pct":              round((cash / total_portfolio) * 100, 4),
            "total_cost_basis":      total_cb,
            "total_gain_loss":       total_gl,
            "total_gain_loss_pct":   gl_pct,
        })
        holdings["last_updated"] = run_date
        holdings["price_source"] = "pipeline_json"

    return updated, not_found


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Neptune Price Updater")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview price changes without saving")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show every price update")
    args = parser.parse_args()

    print("=" * 60)
    print(" Neptune Price Updater")
    print(f" {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if args.dry_run:
        print(" *** DRY RUN — no files will be modified ***")
    print("=" * 60)

    holdings = load_json(HOLDINGS_FILE, "neptune_holdings.json")
    if not holdings:
        sys.exit(1)

    research_data = load_json(RESEARCH_FILE, "research_latest.json")
    mercury_data  = load_json(MERCURY_FILE,  "mercury_latest.json")

    print(f"\n📊 Building price map...")
    stock_prices = extract_stock_prices(research_data)
    etf_prices   = extract_etf_prices(mercury_data)
    price_map    = {**stock_prices, **etf_prices}

    print(f"   Pipeline prices  : {len(price_map)}")

    # yfinance fallback for any held tickers not in pipeline data
    positions = holdings.get("positions", {})
    missing   = set(positions.keys()) - set(price_map.keys())
    if missing:
        yf_prices = fetch_prices_yfinance(missing)
        price_map.update(yf_prices)
        print(f"   yfinance prices  : {len(yf_prices)}")

    print(f"   Total prices     : {len(price_map)}")

    if len(price_map) == 0:
        print("\n❌ No prices found. Check pipeline data files.")
        sys.exit(1)

    if args.verbose:
        print(f"\n{'─'*60}")
        print(f"  {'TICKER':<6}  {'OLD PRICE':>12}  {'NEW PRICE':>12}  {'CHG':>8}  {'MARKET VALUE':>14}")
        print(f"{'─'*60}")

    updated, not_found = update_holdings(
        holdings, price_map, verbose=args.verbose, dry_run=args.dry_run
    )

    print(f"\n✅ Prices updated : {len(updated)}")
    if not_found:
        print(f"⚠️  No price found : {', '.join(sorted(not_found))}")
        print(f"   These positions retain their previous price.")

    if not args.dry_run:
        with open(HOLDINGS_FILE, "w") as f:
            json.dump(holdings, f, indent=2)
        print(f"\n💾 Saved: {HOLDINGS_FILE}")

    s = holdings["summary"]
    print(f"\n{'─'*60}")
    print(f"  Portfolio Total  : ${s['total_portfolio_value']:>14,.2f}")
    print(f"  Equities         : ${s['total_equity_value']:>14,.2f}")
    print(f"  ETFs             : ${s['total_etf_value']:>14,.2f}")
    print(f"  Cash (VMRXX)     : ${s['cash_value']:>14,.2f}  ({s['cash_pct']:.2f}%)")
    print(f"  Total Gain/Loss  : ${s['total_gain_loss']:>14,.2f}  ({s['total_gain_loss_pct']:+.2f}%)")
    print(f"{'─'*60}")

    if args.dry_run:
        print("\n⚠️  Dry run complete — no changes saved.")


if __name__ == "__main__":
    main()
