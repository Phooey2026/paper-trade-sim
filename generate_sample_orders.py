#!/usr/bin/env python3
"""
generate_sample_orders.py — Simulates Jupiter and Mercury order generation.
Reads sector rankings + research JSON to produce BUY/SELL orders based on
ACCUMULATE/AVOID verdicts. Outputs neptune_orders_YYYYMMDD_HHMM.json.

This file is human-editable before Neptune processes it — add, remove, or
tweak any order for testing Neptune's approval/rejection logic.

Usage:
    python3 generate_sample_orders.py
    python3 generate_sample_orders.py --max-orders 10
    python3 generate_sample_orders.py --include-sells
    python3 generate_sample_orders.py --output data/my_test_orders.json
"""

import json
import os
import random
import argparse
from datetime import datetime
from glob import glob

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
HOLDINGS_FILE = os.path.join(SCRIPT_DIR, "neptune_holdings.json")

# ETF proxies Mercury is allowed to trade
MERCURY_ETFS = ["BITQ", "VDE", "IAU"]

# Dollar ranges for order sizing (wider than real limits to test Neptune)
JUPITER_ORDER_RANGE = (25000, 300000)  # Neptune's hard max is $250K — some will get rejected
MERCURY_ORDER_RANGE = (25000, 275000)


def load_json(path, label):
    if not os.path.exists(path):
        print(f"⚠️  {label} not found: {path}")
        return None
    with open(path) as f:
        return json.load(f)


def get_holdings_tickers():
    holdings = load_json(HOLDINGS_FILE, "neptune_holdings.json")
    if not holdings:
        return set()
    return set(holdings.get("positions", {}).keys())


def get_verdict_from_research(research_data, ticker):
    """Pull Jupiter's verdict for a ticker from research JSON."""
    if not research_data:
        return None
    tickers = research_data.get("tickers", research_data)
    if isinstance(tickers, dict) and ticker in tickers:
        data = tickers[ticker]
        if isinstance(data, dict):
            return (
                data.get("verdict")
                or data.get("jupiter_verdict")
                or data.get("recommendation")
            )
    return None


def generate_jupiter_orders(rankings_data, research_data, holdings_tickers,
                             max_orders=8, include_sells=True):
    """
    Generate BUY orders for ACCUMULATE-ranked tickers,
    SELL orders for AVOID-ranked tickers that are currently held.
    """
    orders = []

    if not rankings_data:
        print("  ⚠️  No rankings data — generating fallback Jupiter orders")
        # Fallback: a few hardcoded test orders for cabin use
        fallback = [
            ("NVDA", "BUY",  "ACCUMULATE ranked in Semiconductors sector. Strong momentum."),
            ("AAPL", "BUY",  "ACCUMULATE ranked in Technology sector. Consistent earnings."),
            ("TSLA", "SELL", "AVOID ranked in Consumer Discretionary. Margin pressure."),
            ("INTC", "SELL", "AVOID ranked in Semiconductors. Market share loss ongoing."),
            ("COST", "BUY",  "ACCUMULATE ranked in Consumer Staples. Membership growth."),
        ]
        for ticker, action, rationale in fallback:
            if action == "SELL" and ticker not in holdings_tickers:
                continue
            amount = round(random.uniform(*JUPITER_ORDER_RANGE) / 100) * 100
            orders.append({
                "order_id": f"JUP-{ticker}-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "agent": "jupiter",
                "ticker": ticker,
                "action": action,
                "dollar_amount": amount,
                "rationale": rationale,
                "source": "fallback_test",
                "verdict": action,
                "generated_at": datetime.now().isoformat()
            })
        return orders

    # Parse rankings JSON — expected structure from sector_ranking.py
    # rankings_data may be a list of sectors or a dict keyed by sector name
    sectors = {}
    if isinstance(rankings_data, dict):
        sectors = rankings_data.get("sectors", rankings_data)
    elif isinstance(rankings_data, list):
        for item in rankings_data:
            if isinstance(item, dict) and "sector" in item:
                sectors[item["sector"]] = item

    accumulate_candidates = []
    avoid_candidates = []

    for sector_name, sector_data in sectors.items():
        if not isinstance(sector_data, dict):
            continue
        # rankings_latest.json: {sector, ranking_rationale, stocks: [...]}
        # Each stock: {ticker, rank, verdict, score, one_liner, strengths, risks}
        tickers_in_sector = (
            sector_data.get("stocks") or
            sector_data.get("tickers") or
            sector_data.get("rankings") or
            []
        )
        if isinstance(tickers_in_sector, dict):
            tickers_in_sector = list(tickers_in_sector.values())

        for entry in tickers_in_sector:
            if not isinstance(entry, dict):
                continue
            ticker  = entry.get("ticker", "")
            verdict = str(entry.get("verdict", "")).upper()

            # Rich rationale from one_liner + strengths/risks
            one_liner = entry.get("one_liner", "")
            strengths = entry.get("strengths", [])
            risks     = entry.get("risks", [])

            if verdict == "ACCUMULATE":
                rationale = (f"ACCUMULATE (rank {entry.get('rank','?')}, "
                             f"score {entry.get('score','?')}): {one_liner}")
                if strengths:
                    rationale += f" Key strength: {strengths[0]}"
                accumulate_candidates.append({
                    "ticker": ticker,
                    "sector": sector_name,
                    "rationale": rationale
                })
            elif verdict == "AVOID" and ticker in holdings_tickers:
                rationale = (f"AVOID (rank {entry.get('rank','?')}, "
                             f"score {entry.get('score','?')}): {one_liner}")
                if risks:
                    rationale += f" Key risk: {risks[0]}"
                avoid_candidates.append({
                    "ticker": ticker,
                    "sector": sector_name,
                    "rationale": rationale
                })

    # Shuffle and cap
    random.shuffle(accumulate_candidates)
    random.shuffle(avoid_candidates)

    buy_count  = min(len(accumulate_candidates), max(2, max_orders // 2))
    sell_count = min(len(avoid_candidates), max_orders - buy_count) if include_sells else 0

    ts = datetime.now().strftime('%Y%m%d%H%M%S')
    for i, cand in enumerate(accumulate_candidates[:buy_count]):
        amount = round(random.uniform(*JUPITER_ORDER_RANGE) / 100) * 100
        orders.append({
            "order_id": f"JUP-{cand['ticker']}-{ts}-{i:02d}",
            "agent": "jupiter",
            "ticker": cand["ticker"],
            "action": "BUY",
            "dollar_amount": amount,
            "rationale": cand["rationale"],
            "source": "sector_rankings",
            "verdict": "ACCUMULATE",
            "sector": cand["sector"],
            "generated_at": datetime.now().isoformat()
        })

    if include_sells:
        for i, cand in enumerate(avoid_candidates[:sell_count]):
            amount = round(random.uniform(*JUPITER_ORDER_RANGE) / 100) * 100
            orders.append({
                "order_id": f"JUP-{cand['ticker']}-{ts}-S{i:02d}",
                "agent": "jupiter",
                "ticker": cand["ticker"],
                "action": "SELL",
                "dollar_amount": amount,
                "rationale": cand["rationale"],
                "source": "sector_rankings",
                "verdict": "AVOID",
                "sector": cand["sector"],
                "generated_at": datetime.now().isoformat()
            })

    return orders


def generate_mercury_orders(mercury_data, holdings_tickers):
    """
    Generate BUY/SELL orders for IAU, VDE, BITQ based on Mercury's
    CCC outlook. Uses simple signal words in Mercury's summary text.
    """
    orders = []
    ts = datetime.now().strftime('%Y%m%d%H%M%S')

    # Signal map: look for bullish/bearish language in Mercury's sections
    etf_signals = {
        "IAU":  {"buy_keywords":  ["gold bullish", "gold rising", "safe haven demand",
                                    "metals positive", "gold upside"],
                  "sell_keywords": ["gold bearish", "gold falling", "metals declining"],
                  "rationale_buy":  "Mercury: Gold outlook bullish — safe haven demand elevated.",
                  "rationale_sell": "Mercury: Gold outlook bearish — USD strength headwind."},
        "VDE":  {"buy_keywords":  ["energy bullish", "oil rising", "wti upside",
                                    "crude positive", "energy demand"],
                  "sell_keywords": ["energy bearish", "oil falling", "crude declining",
                                    "demand destruction"],
                  "rationale_buy":  "Mercury: Energy outlook positive — WTI trend supportive.",
                  "rationale_sell": "Mercury: Energy outlook negative — demand concerns."},
        "BITQ": {"buy_keywords":  ["crypto bullish", "bitcoin rising", "btc upside",
                                    "crypto positive", "risk-on"],
                  "sell_keywords": ["crypto bearish", "bitcoin falling", "btc declining",
                                    "risk-off", "crypto headwinds"],
                  "rationale_buy":  "Mercury: Crypto outlook positive — BTC momentum intact.",
                  "rationale_sell": "Mercury: Crypto outlook cautious — risk-off environment."},
    }

    # Get Mercury summary text for signal detection
    mercury_text = ""
    if mercury_data and isinstance(mercury_data, dict):
        mercury_text = (
            mercury_data.get("mercury_summary", "")
            or mercury_data.get("summary", "")
            or mercury_data.get("ccc_summary", "")
            or json.dumps(mercury_data)
        ).lower()

    for ticker, signals in etf_signals.items():
        action = None
        rationale = ""

        buy_hit  = any(kw in mercury_text for kw in signals["buy_keywords"])
        sell_hit = any(kw in mercury_text for kw in signals["sell_keywords"])

        if buy_hit and not sell_hit:
            action = "BUY"
            rationale = signals["rationale_buy"]
        elif sell_hit and not buy_hit:
            if ticker in holdings_tickers:
                action = "SELL"
                rationale = signals["rationale_sell"]
        else:
            # No clear signal — randomly assign for testing (50/50, bias toward BUY)
            if random.random() > 0.4:
                action = "BUY"
                rationale = signals["rationale_buy"] + " [TEST: no clear signal — defaulting BUY]"
            elif ticker in holdings_tickers:
                action = "SELL"
                rationale = signals["rationale_sell"] + " [TEST: no clear signal — defaulting SELL]"

        if action:
            amount = round(random.uniform(*MERCURY_ORDER_RANGE) / 100) * 100
            orders.append({
                "order_id": f"MER-{ticker}-{ts}",
                "agent": "mercury",
                "ticker": ticker,
                "action": action,
                "dollar_amount": amount,
                "rationale": rationale,
                "source": "mercury_ccc_outlook",
                "generated_at": datetime.now().isoformat()
            })

    return orders


def main():
    parser = argparse.ArgumentParser(description="Generate sample Neptune orders")
    parser.add_argument("--max-orders", type=int, default=8,
                        help="Max Jupiter orders to generate (default: 8)")
    parser.add_argument("--include-sells", action="store_true", default=True,
                        help="Include SELL orders for AVOID-rated held positions")
    parser.add_argument("--no-sells", action="store_true",
                        help="Suppress SELL order generation")
    parser.add_argument("--output", type=str,
                        help="Override output file path")
    args = parser.parse_args()

    include_sells = args.include_sells and not args.no_sells

    print("=" * 60)
    print(" Neptune Order Generator")
    print(f" {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    rankings_data = load_json(os.path.join(DATA_DIR, "rankings_latest.json"), "rankings_latest.json")
    research_data = load_json(os.path.join(DATA_DIR, "research_latest.json"), "research_latest.json")
    mercury_data  = load_json(os.path.join(DATA_DIR, "mercury_latest.json"),  "mercury_latest.json")
    holdings_tickers = get_holdings_tickers()

    print(f"\n📋 Holdings positions loaded: {len(holdings_tickers)}")

    print(f"\n🪐 Generating Jupiter orders (equity)...")
    jupiter_orders = generate_jupiter_orders(
        rankings_data, research_data, holdings_tickers,
        max_orders=args.max_orders, include_sells=include_sells
    )
    print(f"   Generated: {len(jupiter_orders)} orders")

    print(f"\n⚡ Generating Mercury orders (ETF proxies)...")
    mercury_orders = generate_mercury_orders(mercury_data, holdings_tickers)
    print(f"   Generated: {len(mercury_orders)} orders")

    all_orders = jupiter_orders + mercury_orders

    # Build order file
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    output_path = args.output or os.path.join(DATA_DIR, f"neptune_orders_{ts}.json")

    order_file = {
        "generated_at": datetime.now().isoformat(),
        "generated_by": "generate_sample_orders.py",
        "account_id": "OC-CLIENT-001",
        "status": "pending_neptune_review",
        "order_count": len(all_orders),
        "_edit_note": (
            "This file is human-editable. Add, remove, or modify orders before "
            "running neptune.py. Neptune will approve or reject each order."
        ),
        "orders": all_orders
    }

    with open(output_path, "w") as f:
        json.dump(order_file, f, indent=2)

    print(f"\n{'─'*60}")
    print(f"  Orders generated : {len(all_orders)}")
    print(f"    Jupiter (equity) : {len(jupiter_orders)}")
    print(f"    Mercury (ETFs)   : {len(mercury_orders)}")
    print(f"  Output file      : {output_path}")
    print(f"{'─'*60}")
    print(f"\n✏️  You may edit {os.path.basename(output_path)} before running neptune.py")
    print(f"▶️  Next: python3 neptune.py --orders {output_path}")


if __name__ == "__main__":
    main()
