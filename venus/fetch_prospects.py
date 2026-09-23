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
FUND_FILE    = os.path.join(SCRIPT_DIR, "ocrff_fund.json")
OUT_FILE     = os.path.join(SCRIPT_DIR, "venus_prospects_latest.json")

TODAY = date.today().isoformat()

# Per-league config: schedule file, the CRM field holding the FA's team code
# for that league, and the interest tag crm_advisors.json uses for fans of it.
LEAGUES = {
    "mlb": {"schedule_file": "baseball_schedules.json", "team_field": "mlb_team", "interest_tag": "Baseball"},
    "nba": {"schedule_file": "nba_schedules.json",       "team_field": "nba_team", "interest_tag": "Basketball"},
    "nfl": {"schedule_file": "nfl_schedules.json",       "team_field": "nfl_team", "interest_tag": "Football"},
}
SPORT_INTERESTS = tuple(cfg["interest_tag"] for cfg in LEAGUES.values())
SPORT_EMOJI = {"Baseball": "⚾", "Basketball": "🏀", "Football": "🏈"}


def load_json(path, label=""):
    if not os.path.exists(path):
        print(f"❌ {label or path} not found")
        raise SystemExit(1)
    with open(path) as f:
        return json.load(f)


def load_league_schedules():
    """
    Load whichever league schedule files are present.
    Baseball, NBA and NFL are each independent — a missing file just means
    that league's hooks are unavailable this run, it shouldn't block the
    other two. At least one schedule file must exist.
    """
    leagues = {}
    for league, cfg in LEAGUES.items():
        path = os.path.join(SCRIPT_DIR, cfg["schedule_file"])
        if not os.path.exists(path):
            print(f"  ⚠️  {cfg['schedule_file']} not found — {league.upper()} hooks unavailable this run")
            continue
        with open(path) as f:
            leagues[league] = json.load(f)
    if not leagues:
        print("❌ No schedule files found (checked: "
              f"{', '.join(cfg['schedule_file'] for cfg in LEAGUES.values())})")
        raise SystemExit(1)
    return leagues


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

    # Sports fan — Venus can personalize heavily via a game-day hook.
    # Bonus scales with how many teams they follow (more hooks = more angles,
    # and gives Venus room to offer a choice rather than a single pitch).
    sport_count = sum(1 for tag in SPORT_INTERESTS if tag in fa.get("interests", []))
    if sport_count == 1:
        score += 20
    elif sport_count >= 2:
        score += 20 + 5 * (sport_count - 1)

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
        # FAs who are baseball fans (kept for backward compatibility)
        candidates = [a for a in prospects if "Baseball" in a.get("interests", [])]
    elif strategy == "sports":
        # Any sports fan — MLB, NBA, or NFL
        candidates = [a for a in prospects if any(s in a.get("interests", []) for s in SPORT_INTERESTS)]
    else:
        # Mixed: score and pick top + random
        scored = sorted(prospects, key=score_prospect, reverse=True)
        top    = scored[:max(count*2, 20)]
        candidates = top

    if len(candidates) < count:
        candidates = prospects

    return random.sample(candidates, min(count, len(candidates)))


def enrich_prospect(fa, leagues, fund):
    """
    Add sports schedule hooks (MLB/NBA/NFL — whichever the FA actually
    follows), fund data, and strategy notes to a prospect.
    """
    score = score_prospect(fa)

    # Gather one hook per league the FA follows (mlb_team/nba_team/nfl_team
    # are each independently optional — a SoCal FA can be a fan of all
    # three, one, or none, and may follow a team without a code on file if
    # they haven't told us which team yet).
    sports_hooks = []
    for league, cfg in LEAGUES.items():
        team_code = fa.get(cfg["team_field"])
        if not team_code or league not in leagues:
            continue
        league_data = leagues[league]
        team_info   = league_data.get("teams", {}).get(team_code, {})
        sched       = get_upcoming_games(team_code, league_data.get("schedules", {}))
        sports_hooks.append({
            "league":        league,
            "interest_tag":  cfg["interest_tag"],
            "team_code":     team_code,
            "team_name":     team_info.get("name", team_code),
            "upcoming":      sched,
        })

    # Strategy notes for Venus
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
    for hook in sports_hooks:
        next_home = hook["upcoming"].get("next_home")
        if next_home:
            strategy_notes.append(
                f"{fa['first_name']} is a {hook['team_name']} fan ({hook['interest_tag']}) — "
                f"upcoming hook: {next_home['label']}."
            )
        else:
            strategy_notes.append(
                f"{fa['first_name']} is a {hook['team_name']} fan ({hook['interest_tag']}) — "
                "no home game in the next 30 days, but still worth referencing."
            )
    if len(sports_hooks) > 1:
        strategy_notes.append(
            f"{fa['first_name']} follows {len(sports_hooks)} teams "
            f"({', '.join(h['team_name'] for h in sports_hooks)}) — multiple hooks available. "
            "Offer more than one and let them pick rather than leading with just one."
        )
    if fa.get("contact_via_assistant") and fa.get("assistant_name"):
        strategy_notes.append(
            f"Route through assistant {fa['assistant_name']} ({fa['assistant_email']}). "
            "Don't email the FA directly."
        )

    # Backward-compat "baseball" block: venus.py / venus_week2.py still read
    # this key directly. Kept in sync with the MLB entry (if any) so the
    # existing pipeline keeps working unmodified until those files are
    # updated to consume sports_hooks natively.
    mlb_hook = next((h for h in sports_hooks if h["league"] == "mlb"), None)
    baseball_block = {
        "team_name": mlb_hook["team_name"] if mlb_hook else "",
        "upcoming":  mlb_hook["upcoming"] if mlb_hook else
                     {"team": None, "home_games": [], "away_games": [], "next_home": None},
    }

    return {
        "fa_id":          fa["fa_id"],
        "prospect_score": score,
        "profile": fa,
        "baseball": baseball_block,
        "sports_hooks": sports_hooks,
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
    parser.add_argument("--strategy", choices=["mixed","new_fa","warm","baseball","sports"],
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
    leagues   = load_league_schedules()
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
        p = enrich_prospect(fa, leagues, fund)
        enriched.append(p)
        sport_emoji = "".join(SPORT_EMOJI[h["interest_tag"]] for h in p["sports_hooks"]) or "  "
        print(f"  {p['fa_id']} | {fa['name']:<22} | {fa['firm'][:20]:<20} | "
              f"Book: {fa['book_size']:>3} | Score: {p['prospect_score']:>3} | "
              f"{sport_emoji} "
              f"{'🔥' if fa.get('warm_lead') else ''}")
        print(f"           Territory: {fa['territory']}")
        if p["sports_hooks"]:
            for h in p["sports_hooks"]:
                next_home = h["upcoming"].get("next_home")
                next_game = next_home["label"] if next_home else "no upcoming home games"
                print(f"           {h['interest_tag']} ({h['team_name']}): {next_game}")
        else:
            print(f"           No sports hook on file")
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
