#!/usr/bin/env python3
"""
fetch_prospects.py — Venus Prospect Fetcher
Pulls 5 random PROSPECT FAs from the CRM, enriches each with:
  - Upcoming baseball games for their team (next 30 days)
  - OCRFF performance data
  - Warm connection info if applicable
  - Wholesaler strategy notes

Usage:
    python3 fetch_prospects.py
    python3 fetch_prospects.py --count 5 --strategy new_fa
    python3 fetch_prospects.py --fa-id FA-0023 FA-0041  # specific FAs
    python3 fetch_prospects.py --seed 42  # reproducible selection
"""

import json
import os
import random
import argparse
from datetime import date, datetime, timedelta

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
CRM_FILE     = os.path.join(SCRIPT_DIR, "crm_advisors.json")
SCHED_FILE   = os.path.join(SCRIPT_DIR, "baseball_schedules.json")
FUND_FILE    = os.path.join(SCRIPT_DIR, "ocrff_fund.json")
OUT_FILE     = os.path.join(SCRIPT_DIR, "venus_prospects_latest.json")

TODAY = date.today().isoformat()


def load_json(path, label=""):
    if not os.path.exists(path):
        print(f"❌ {label or path} not found")
        raise SystemExit(1)
    with open(path) as f:
        return json.load(f)


def get_upcoming_games(team_code, schedules, days_ahead=30):
    """Return next N home games for a team within the next 30 days."""
    team_sched = schedules.get(team_code, [])
    cutoff = (date.today() + timedelta(days=days_ahead)).isoformat()

    upcoming = [
        g for g in team_sched
        if not g["played"]
        and g["date"] >= TODAY
        and g["date"] <= cutoff
    ]
    home_games   = [g for g in upcoming if g["home"]][:5]
    away_games   = [g for g in upcoming if not g["home"]][:3]

    def fmt(g):
        time_str = "night" if g["time"] == "N" else "afternoon"
        loc      = "home" if g["home"] else f"@ {g['opponent']}"
        d        = date.fromisoformat(g["date"])
        return {
            "date": g["date"],
            "day":  g["day_of_week"],
            "opponent": g["opponent"],
            "home": g["home"],
            "time": time_str,
            "label": f"{g['day_of_week']}, {d.strftime('%b %-d')} vs {g['opponent']} ({loc}, {time_str})"
        }

    return {
        "team":       team_code,
        "home_games": [fmt(g) for g in home_games],
        "away_games": [fmt(g) for g in away_games],
        "next_home":  fmt(home_games[0]) if home_games else None,
    }


def score_prospect(fa):
    """
    Score a prospect for Venus outreach priority.
    Factors: new FA (small book = higher score), warm lead, baseball fan, AUM tier.
    This encodes the wholesaler insight: new FAs building their book
    are more receptive than established FAs who are constantly pitched.
    """
    score = 0

    # New FA bonus — smaller book = more receptive
    if fa["book_size"] < 30:
        score += 40
    elif fa["book_size"] < 75:
        score += 25
    elif fa["book_size"] < 150:
        score += 10

    # Warm lead (connected to existing client FA)
    if fa.get("warm_lead"):
        score += 30

    # Baseball fan — Venus can personalize heavily
    if "Baseball" in fa.get("interests", []):
        score += 20

    # Has assistant — more professional, easier to schedule
    if fa.get("assistant_name"):
        score += 5

    # Tier 2 sweet spot — not so big they ignore you, not so small they can't buy
    if fa["tier"] == 2:
        score += 10
    elif fa["tier"] == 1:
        score += 3

    return score


def select_prospects(advisors, strategy="mixed", count=5, specific_ids=None, seed=None):
    """Select prospect FAs based on strategy."""
    if seed is not None:
        random.seed(seed)

    prospects = [a for a in advisors if a["status"] == "PROSPECT"]

    if specific_ids:
        selected = [a for a in prospects if a["fa_id"] in specific_ids]
        if len(selected) < count:
            print(f"  ⚠️  Only found {len(selected)} of {len(specific_ids)} requested IDs")
        return selected[:count]

    if strategy == "new_fa":
        # Target new FAs building their book
        candidates = [a for a in prospects if a["book_size"] < 50]
    elif strategy == "warm":
        # Warm leads only
        candidates = [a for a in prospects if a.get("warm_lead")]
        if len(candidates) < count:
            candidates = prospects  # fallback
    elif strategy == "baseball":
        # FAs who are baseball fans
        candidates = [a for a in prospects if "Baseball" in a.get("interests", [])]
    else:
        # Mixed: score and pick top + random
        scored = sorted(prospects, key=score_prospect, reverse=True)
        top    = scored[:max(count*2, 20)]
        candidates = top

    if len(candidates) < count:
        candidates = prospects

    return random.sample(candidates, min(count, len(candidates)))


def enrich_prospect(fa, schedules_data, fund):
    """Add baseball schedule, fund data, and strategy notes to a prospect."""
    team_code = fa.get("team", "LAD")
    sched     = get_upcoming_games(team_code, schedules_data.get("schedules", {}))
    team_info = schedules_data.get("teams", {}).get(team_code, {})
    score     = score_prospect(fa)

    # Strategy note for Venus
    strategy_notes = []
    if fa["book_size"] < 30:
        strategy_notes.append(
            f"{fa['first_name']} is building a new book ({fa['book_size']} clients). "
            "New FAs are more open to exploring new products — less gatekeeping, more curiosity."
        )
    if fa.get("warm_lead") and fa.get("connections"):
        strategy_notes.append(
            f"Warm lead: {fa['first_name']} is connected to {', '.join(fa['connections'])} "
            "who is already a client. Drop the name."
        )
    if "Baseball" in fa.get("interests", []):
        strategy_notes.append(
            f"{fa['first_name']} is a baseball fan ({team_info.get('name','')}) — "
            "use upcoming home games as the hook."
        )
    if fa.get("contact_via_assistant") and fa.get("assistant_name"):
        strategy_notes.append(
            f"Route through assistant {fa['assistant_name']} ({fa['assistant_email']}). "
            "Don't email the FA directly."
        )

    return {
        "fa_id":          fa["fa_id"],
        "prospect_score": score,
        "profile": fa,
        "baseball": {
            "team_name": team_info.get("name", ""),
            "upcoming":  sched,
        },
        "ocrff_performance": fund["performance"],
        "fund_name":    fund["fund_name"],
        "fund_nav":     fund["current_nav"],
        "strategy_notes": strategy_notes,
        "contact_name": fa.get("assistant_name") if fa.get("contact_via_assistant") else fa["name"],
        "contact_email": fa.get("assistant_email") if fa.get("contact_via_assistant") else fa["email"],
        "fetched_at":   datetime.now().isoformat(),
    }


def main():
    parser = argparse.ArgumentParser(description="Venus Prospect Fetcher")
    parser.add_argument("--count", type=int, default=5,
                        help="Number of prospects to fetch (default: 5)")
    parser.add_argument("--strategy", choices=["mixed","new_fa","warm","baseball"],
                        default="mixed", help="Selection strategy")
    parser.add_argument("--fa-id", nargs="+", help="Specific FA IDs to fetch")
    parser.add_argument("--seed", type=int, help="Random seed for reproducible selection")
    parser.add_argument("--output", type=str, default=OUT_FILE)
    args = parser.parse_args()

    print("=" * 55)
    print(" 💫 Venus Prospect Fetcher")
    print(f"    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 55)

    crm       = load_json(CRM_FILE, "CRM advisors")
    schedules = load_json(SCHED_FILE, "Baseball schedules")
    fund      = load_json(FUND_FILE, "OCRFF fund data")
    advisors  = crm["advisors"]

    prospects_raw = select_prospects(
        advisors,
        strategy=args.strategy,
        count=args.count,
        specific_ids=args.fa_id,
        seed=args.seed
    )

    print(f"\n📋 Strategy: {args.strategy} | Fetching {len(prospects_raw)} prospects\n")

    enriched = []
    for fa in prospects_raw:
        p = enrich_prospect(fa, schedules, fund)
        enriched.append(p)
        team  = p["baseball"]["team_name"]
        next_home = p["baseball"]["upcoming"].get("next_home")
        next_game = next_home["label"] if next_home else "no upcoming home games"
        print(f"  {p['fa_id']} | {fa['name']:<22} | {fa['firm'][:20]:<20} | "
              f"Book: {fa['book_size']:>3} | Score: {p['prospect_score']:>3} | "
              f"{'⚾' if 'Baseball' in fa.get('interests',[]) else '  '} "
              f"{'🔥' if fa.get('warm_lead') else ''}")
        print(f"           Territory: {fa['territory']} | Team: {team}")
        print(f"           Next home game: {next_game}")
        if p["strategy_notes"]:
            print(f"           💡 {p['strategy_notes'][0][:80]}")
        print()

    output = {
        "fetched_at":  datetime.now().isoformat(),
        "strategy":    args.strategy,
        "count":       len(enriched),
        "ocrff_ytd":   fund["performance"]["ytd"]["ocrff"],
        "sp500_ytd":   fund["performance"]["ytd"]["sp500"],
        "prospects":   enriched,
    }

    with open(args.output, "w") as f:
        json.dump(output, f, indent=2)

    print(f"✅ Saved: {args.output}")
    print(f"▶️  Next: python3 venus.py --prospects {args.output}")


if __name__ == "__main__":
    main()
