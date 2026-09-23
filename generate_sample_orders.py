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
import re
import random
import argparse
from datetime import datetime
from glob import glob

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
HOLDINGS_FILE = os.path.join(SCRIPT_DIR, "neptune_holdings.json")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "neptune_config.json")

# ETF proxies Mercury is allowed to trade
MERCURY_ETFS = ["BITQ", "VDE", "IAU"]

# Fallback order range, used only if neptune_config.json can't be read.
# Real limits are loaded from neptune_config.json's order_limits at runtime
# (see load_order_range()) so this generator can never drift out of sync
# with what Neptune will actually accept.
_FALLBACK_ORDER_RANGE = (100000, 250000)


def load_order_range():
    """Read (min_order_dollars, max_order_dollars) from neptune_config.json.
    Falls back to _FALLBACK_ORDER_RANGE if the file is missing or malformed,
    so the generator still runs (e.g. cabin/offline mode) but prints a
    warning since orders may then not match Neptune's real limits."""
    if not os.path.exists(CONFIG_FILE):
        print(f"  ⚠️  neptune_config.json not found — using fallback range "
              f"${_FALLBACK_ORDER_RANGE[0]:,}-${_FALLBACK_ORDER_RANGE[1]:,}")
        return _FALLBACK_ORDER_RANGE
    try:
        with open(CONFIG_FILE) as f:
            config = json.load(f)
        limits = config.get("order_limits", {})
        lo = limits.get("min_order_dollars", _FALLBACK_ORDER_RANGE[0])
        hi = limits.get("max_order_dollars", _FALLBACK_ORDER_RANGE[1])
        return (lo, hi)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  ⚠️  Could not read neptune_config.json ({e}) — using fallback range")
        return _FALLBACK_ORDER_RANGE


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
                             max_orders=8, include_sells=True, order_range=None):
    """
    Generate BUY orders for ACCUMULATE-ranked tickers,
    SELL orders for AVOID-ranked tickers that are currently held.

    order_range: (min_dollars, max_dollars) tuple, normally loaded from
    neptune_config.json via load_order_range(). Falls back to
    _FALLBACK_ORDER_RANGE if not provided.
    """
    order_range = order_range or _FALLBACK_ORDER_RANGE
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
            amount = round(random.uniform(*order_range) / 100) * 100
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
        amount = round(random.uniform(*order_range) / 100) * 100
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
            amount = round(random.uniform(*order_range) / 100) * 100
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


# Which mercury_backdrop section + stance field each ETF proxy maps to,
# and (for tickers that have one) the COT report's asset-line prefix.
_MERCURY_SIGNAL_MAP = {
    "IAU":  {"backdrop_section": "metals",  "stance_field": "gold_stance", "cot_prefix": "Gold"},
    "VDE":  {"backdrop_section": "energy",  "stance_field": "stance",      "cot_prefix": "Wti Financial Crude Oil"},
    "BITQ": {"backdrop_section": "crypto",  "stance_field": "stance",      "cot_prefix": None},
}

_BULLISH_WORDS = ("bullish", "rising", "up", "strong", "squeeze")
_BEARISH_WORDS = ("bearish", "falling", "selling", "down", "weak", "declining")

# Momentum thresholds (1-month %) — deliberately conservative so a real,
# unambiguous move is required before a signal fires.
_MOMENTUM_BULLISH_PCT = 5.0
_MOMENTUM_BEARISH_PCT = -5.0


def _parse_etf_metrics(etf_data_text, ticker):
    """Pull Price/1d/1m/3m/1y/vs-52w-high out of mercury_latest.json's
    data.etf_data text block for one ticker. Returns None if the ticker's
    section isn't found."""
    if not etf_data_text:
        return None
    # Block looks like: "── IAU — iShares Gold Trust (HELD) ──\n  Price/NAV: ...\n\n"
    # (next block starts at the next "── " header, or end of string)
    block_re = re.compile(
        rf"──\s*{re.escape(ticker)}\s*—.*?──(.*?)(?=\n\n──|\Z)", re.DOTALL
    )
    m = block_re.search(etf_data_text)
    if not m:
        return None
    block = m.group(1)

    def pct(label):
        mm = re.search(rf"{label}:\s*([+-]?[\d.]+)%", block)
        return float(mm.group(1)) if mm else None

    return {
        "chg_1d":       pct("1d Change"),
        "chg_1m":       pct("1m Return"),
        "chg_3m":       pct("3m Return"),
        "chg_1y":       pct("1y Return"),
        "vs_52w_high":  pct("vs 52w High"),
    }


def _parse_backdrop_stance(backdrop_text, section, field):
    """Pull e.g. metals.gold_stance or energy.stance out of mercury_latest.json's
    data.mercury_backdrop text. Returns the raw stance string, or None."""
    if not backdrop_text:
        return None
    sec_re = re.compile(rf"^{section}:\s*\n(.*?)(?=\n\w[\w_]*:\s*\n|\Z)",
                         re.DOTALL | re.MULTILINE)
    sm = sec_re.search(backdrop_text)
    if not sm:
        return None
    field_re = re.compile(rf"{field}:\s*(.+)")
    fm = field_re.search(sm.group(1))
    return fm.group(1).strip() if fm else None


def _stance_polarity(stance_text):
    """Classify a stance string as 'bullish', 'bearish', or 'neutral', using
    the parenthetical qualifier when present (e.g. 'Risk-On (Gold Selling)'
    is bearish for gold specifically, even though the outer stance is
    risk-on) since that's the part that actually describes this asset's
    direction rather than the broader market mood."""
    if not stance_text:
        return "neutral"
    paren = re.search(r"\((.*?)\)", stance_text)
    target = paren.group(1) if paren else stance_text
    target_lower = target.lower()
    bullish = any(w in target_lower for w in _BULLISH_WORDS)
    bearish = any(w in target_lower for w in _BEARISH_WORDS)
    if bullish and not bearish:
        return "bullish"
    if bearish and not bullish:
        return "bearish"
    return "neutral"


def _parse_cot_extreme(cot_text, asset_prefix):
    """Look up an asset's line in the COT report text and report whether
    it's flagged EXTREME, and in which direction. Returns None if the
    asset has no COT line (e.g. crypto)."""
    if not cot_text or not asset_prefix:
        return None
    line_re = re.compile(
        rf"^{re.escape(asset_prefix)}\s+Net:\s*([+-][\d,]+)\s+\(NET (LONG|SHORT)",
        re.MULTILINE,
    )
    m = line_re.search(cot_text)
    if not m:
        return None
    net = int(m.group(1).replace(",", ""))
    direction = m.group(2)
    # Extreme flag is the line immediately following the Long/Short breakdown
    tail = cot_text[m.end():m.end() + 200]
    extreme = "Extreme positioning" in tail
    return {"net": net, "direction": direction, "extreme": extreme}


def generate_mercury_orders(mercury_data, holdings_tickers, order_range=None):
    """
    Generate BUY/SELL orders for IAU, VDE, BITQ from Mercury's actual
    quantitative data in mercury_latest.json (data.etf_data momentum,
    data.mercury_backdrop stance, data.cot_report positioning extremes) —
    no keyword-matching on prose, no random fallback. A ticker with no
    clear signal simply gets no order.
    """
    order_range = order_range or _FALLBACK_ORDER_RANGE
    orders = []
    ts = datetime.now().strftime('%Y%m%d%H%M%S')

    data = (mercury_data or {}).get("data", {})
    etf_data_text = data.get("etf_data", "")
    backdrop_text = data.get("mercury_backdrop", "")
    cot_text      = data.get("cot_report", "")

    if not etf_data_text:
        print("  ⚠️  No etf_data in mercury_latest.json — Mercury generates no orders this run")
        return orders

    for ticker, sig in _MERCURY_SIGNAL_MAP.items():
        metrics = _parse_etf_metrics(etf_data_text, ticker)
        if not metrics or metrics["chg_1m"] is None:
            print(f"  ⚠️  {ticker}: no momentum data found — skipping")
            continue

        stance_text = _parse_backdrop_stance(backdrop_text, sig["backdrop_section"], sig["stance_field"])
        stance      = _stance_polarity(stance_text)
        cot         = _parse_cot_extreme(cot_text, sig["cot_prefix"])

        chg_1m = metrics["chg_1m"]
        bullish_momentum = chg_1m >= _MOMENTUM_BULLISH_PCT
        bearish_momentum = chg_1m <= _MOMENTUM_BEARISH_PCT

        # A crowded (EXTREME) net-long position is a contrarian caution
        # against adding, regardless of momentum — matches Mercury's own
        # real commentary style ("hold, add only after positioning unwinds").
        cot_blocks_buy = bool(cot and cot["extreme"] and cot["direction"] == "LONG")

        action, decision_note = None, ""
        if bullish_momentum and stance != "bearish" and not cot_blocks_buy:
            action = "BUY"
        elif bullish_momentum and cot_blocks_buy:
            decision_note = " No buy signal: crowded EXTREME net-long positioning against the momentum."
        elif bearish_momentum and stance != "bullish" and ticker in holdings_tickers:
            action = "SELL"

        if not action:
            if decision_note:
                print(f"  ⚠️  {ticker}: {chg_1m:+.2f}% 1m — {decision_note.strip()}")
            else:
                print(f"  ⚠️  {ticker}: {chg_1m:+.2f}% 1m, stance {stance} — no clear signal, no order")
            continue

        rationale = (
            f"Mercury: {ticker} {chg_1m:+.2f}% 1m "
            f"({metrics['chg_3m']:+.2f}% 3m, {metrics['vs_52w_high']:+.2f}% vs 52w high). "
            f"Backdrop stance: {stance_text or 'n/a'} ({stance})."
        )
        if cot:
            flag = " [EXTREME]" if cot["extreme"] else ""
            rationale += f" COT: {cot['direction']} {cot['net']:+,}{flag}."
        rationale += decision_note

        amount = round(random.uniform(*order_range) / 100) * 100
        orders.append({
            "order_id": f"MER-{ticker}-{ts}",
            "agent": "mercury",
            "ticker": ticker,
            "action": action,
            "dollar_amount": amount,
            "rationale": rationale,
            "source": "mercury_ccc_data",
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

    order_range = load_order_range()
    print(f"\n💵 Order size range (from neptune_config.json): "
          f"${order_range[0]:,}-${order_range[1]:,}")

    rankings_data = load_json(os.path.join(DATA_DIR, "rankings_latest.json"), "rankings_latest.json")
    research_data = load_json(os.path.join(DATA_DIR, "research_latest.json"), "research_latest.json")
    mercury_data  = load_json(os.path.join(DATA_DIR, "mercury_latest.json"),  "mercury_latest.json")
    holdings_tickers = get_holdings_tickers()

    print(f"\n📋 Holdings positions loaded: {len(holdings_tickers)}")

    print(f"\n🪐 Generating Jupiter orders (equity)...")
    jupiter_orders = generate_jupiter_orders(
        rankings_data, research_data, holdings_tickers,
        max_orders=args.max_orders, include_sells=include_sells,
        order_range=order_range
    )
    print(f"   Generated: {len(jupiter_orders)} orders")

    print(f"\n⚡ Generating Mercury orders (ETF proxies)...")
    mercury_orders = generate_mercury_orders(mercury_data, holdings_tickers, order_range=order_range)
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
