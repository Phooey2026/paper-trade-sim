#!/usr/bin/env python3
"""
trade_engine.py — Shared Trade Execution Engine
Used by both Neptune (agent orders) and Orbit (client orders).
Handles buy/sell execution against neptune_holdings.json with
file locking, cash floor checking, and escalation detection.
"""

import json, os, fcntl, math
from datetime import datetime, date
from copy import deepcopy

PAPER_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOLDINGS_FILE = os.path.join(PAPER_DIR, "neptune_holdings.json")
VENUS_DIR    = os.path.join(PAPER_DIR, "venus")
DATA_DIR     = os.path.join(PAPER_DIR, "data")

CASH_FLOOR_PCT   = 5.0   # VMRXX must stay above 5% of portfolio
RANKINGS_FILE    = os.path.join(DATA_DIR, "rankings_latest.json")
ESCALATION_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "neptune_escalations.json")


def load_holdings():
    with open(HOLDINGS_FILE) as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        data = json.load(f)
        fcntl.flock(f, fcntl.LOCK_UN)
    return data


def save_holdings(holdings):
    tmp = HOLDINGS_FILE + ".tmp"
    with open(tmp, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump(holdings, f, indent=2)
        fcntl.flock(f, fcntl.LOCK_UN)
    os.replace(tmp, HOLDINGS_FILE)


def recompute_summary(holdings):
    positions = holdings["positions"]
    s = holdings["summary"]
    eq  = sum(p["market_value"] for p in positions.values() if p.get("asset_type")=="equity")
    etf = sum(p["market_value"] for p in positions.values() if p.get("asset_type")=="etf")
    invested = round(eq + etf, 2)
    total    = round(invested + s["cash_value"], 2)
    cb       = round(sum(p["cost_basis"] for p in positions.values()), 2)
    gl       = round(sum(p["gain_loss"]  for p in positions.values()), 2)
    s.update({
        "total_equity_value":    round(eq, 2),
        "total_etf_value":       round(etf, 2),
        "total_invested":        invested,
        "total_portfolio_value": total,
        "total_cost_basis":      cb,
        "total_gain_loss":       gl,
        "total_gain_loss_pct":   round((gl/cb)*100, 2) if cb else 0.0,
        "cash_pct":              round((s["cash_value"]/total)*100, 4) if total else 0.0,
    })
    holdings["last_updated"] = date.today().isoformat()
    return holdings


def execute_ocrff_buy(amount_dollars, nav, source="orbit", issue_id=""):
    """
    Client buys OCRFF shares. Money appears in VMRXX (simulated wire),
    then is deployed into the fund position.
    Returns: (transaction_record, error_string)
    """
    holdings = load_holdings()
    s = holdings["summary"]
    shares = round(amount_dollars / nav, 6)

    # Add cash first (simulated external wire)
    s["cash_value"] = round(s["cash_value"] + amount_dollars, 2)
    # Then deduct for fund purchase
    s["cash_value"] = round(s["cash_value"] - amount_dollars, 2)

    txn = {
        "transaction_id": f"TXN-{source.upper()}-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "issue_id": issue_id,
        "date": date.today().isoformat(),
        "type": "BUY",
        "amount_dollars": round(amount_dollars, 2),
        "shares": shares,
        "nav": nav,
        "processed_by": source,
        "cash_after": round(s["cash_value"], 2),
        "escalated": False,
    }

    holdings = recompute_summary(holdings)
    save_holdings(holdings)
    return txn, None


def execute_ocrff_sell(amount_dollars, nav, source="orbit", issue_id="",
                       is_full_close=False, actual_shares=None):
    """
    Client redeems OCRFF shares. Money leaves the firm entirely.
    Fund pays from VMRXX first. If VMRXX is insufficient, Neptune
    must liquidate stock positions to cover the shortfall.

    Returns: (transaction_record, escalation_needed, shortfall, error_string)
    """
    holdings = load_holdings()
    s = holdings["summary"]

    # Cap sell at account value if full close
    if is_full_close and actual_shares:
        shares = round(actual_shares, 6)
        amount_dollars = round(shares * nav, 2)
    else:
        shares = round(amount_dollars / nav, 6)

    cash_available    = s["cash_value"]
    escalation_needed = amount_dollars > cash_available
    shortfall         = round(amount_dollars - cash_available, 2) if escalation_needed else 0.0

    # VMRXX pays what it can; Neptune covers the rest
    cash_paid         = min(amount_dollars, cash_available)
    s["cash_value"]   = round(cash_available - cash_paid, 2)

    txn = {
        "transaction_id": f"TXN-{source.upper()}-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "issue_id": issue_id,
        "date": date.today().isoformat(),
        "type": "SELL",
        "amount_dollars": round(amount_dollars, 2),
        "shares": shares,
        "nav": nav,
        "processed_by": source,
        "cash_paid_from_vmrxx": round(cash_paid, 2),
        "cash_after": round(s["cash_value"], 2),
        "is_full_close": is_full_close,
        "escalated": escalation_needed,
        "shortfall": shortfall,
    }

    if escalation_needed:
        txn["cash_floor_note"] = (
            f"VMRXX insufficient: had ${cash_available:,.2f}, "
            f"needed ${amount_dollars:,.2f}. "
            f"Shortfall ${shortfall:,.2f} escalated to Neptune."
        )

    holdings = recompute_summary(holdings)
    save_holdings(holdings)
    return txn, escalation_needed, shortfall, None


def neptune_rebalance_for_cash(shortfall_dollars, issue_id=""):
    """
    Neptune auto-sells AVOID-rated positions to restore VMRXX above floor.
    Pure Python — no LLM call.
    Returns escalation_record with actions taken.
    """
    holdings  = load_holdings()
    s         = holdings["summary"]
    positions = holdings["positions"]

    # Load rankings to find AVOID-rated positions
    avoid_positions = []
    if os.path.exists(RANKINGS_FILE):
        with open(RANKINGS_FILE) as f:
            rankings = json.load(f)
        avoid_tickers = set()
        sectors = rankings if isinstance(rankings, list) else []
        for sector in sectors:
            for stock in sector.get("stocks", []):
                if stock.get("verdict", "").upper() == "AVOID":
                    avoid_tickers.add(stock["ticker"])

        for ticker, pos in positions.items():
            if ticker in avoid_tickers and pos["market_value"] > 0:
                avoid_positions.append((ticker, pos["market_value"]))

    # Sort by smallest value first (least disruptive)
    avoid_positions.sort(key=lambda x: x[1])

    actions = []
    total_raised = 0.0
    needed = shortfall_dollars * 1.1  # 10% buffer above floor

    for ticker, market_value in avoid_positions:
        if total_raised >= needed:
            break
        sell_amount = min(market_value, needed - total_raised)
        pos = positions[ticker]
        price = pos["last_price"]
        shares_sold = sell_amount / price
        cb_reduction = pos["cost_basis"] * (shares_sold / pos["shares"]) if pos["shares"] else 0

        pos["shares"]       = round(pos["shares"] - shares_sold, 6)
        pos["cost_basis"]   = round(pos["cost_basis"] - cb_reduction, 2)
        pos["market_value"] = round(pos["shares"] * price, 2)
        pos["gain_loss"]    = round(pos["market_value"] - pos["cost_basis"], 2)
        pos["gain_loss_pct"]= round((pos["gain_loss"]/pos["cost_basis"])*100,2) if pos["cost_basis"] else 0

        if pos["shares"] < 0.0001:
            del positions[ticker]

        s["cash_value"] = round(s["cash_value"] + sell_amount, 2)
        total_raised += sell_amount

        actions.append({
            "ticker": ticker,
            "sold_dollars": round(sell_amount, 2),
            "shares_sold": round(shares_sold, 6),
            "price": price,
            "verdict": "AVOID",
        })

    holdings = recompute_summary(holdings)
    save_holdings(holdings)

    # NOW deduct the shortfall from VMRXX — this is the client payment leaving the firm.
    # neptune raised cash into VMRXX; we immediately pay it out to the client.
    # Only deduct what was actually raised (can't pay more than we have).
    actual_payment = min(total_raised, shortfall_dollars)
    holdings = load_holdings()
    holdings["summary"]["cash_value"] = round(
        holdings["summary"]["cash_value"] - actual_payment, 2
    )
    holdings = recompute_summary(holdings)
    save_holdings(holdings)

    escalation_record = {
        "escalation_id": f"ESC-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "issue_id": issue_id,
        "triggered_at": datetime.now().isoformat(),
        "resolved_at": datetime.now().isoformat(),
        "shortfall_dollars": round(shortfall_dollars, 2),
        "total_raised": round(total_raised, 2),
        "actual_payment": round(actual_payment, 2),
        "actions": actions,
        "cash_after": round(holdings["summary"]["cash_value"], 2),
        "status": "RESOLVED" if total_raised >= shortfall_dollars * 0.95 else "PARTIAL",
    }

    # Save escalation log
    esc_data = {"escalations": []}
    if os.path.exists(ESCALATION_FILE):
        with open(ESCALATION_FILE) as f:
            esc_data = json.load(f)
    esc_data["escalations"].append(escalation_record)
    with open(ESCALATION_FILE, "w") as f:
        json.dump(esc_data, f, indent=2)

    return escalation_record
