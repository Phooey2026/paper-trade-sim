# Obsidian Capital — Paper Trading System
## Jetson Orin Nano Super Reference Guide
### Updated October 6, 2026

---

## Overview

The paper trading system simulates a $220M+ investment fund (OCRFF — Obsidian Capital Research Fund) with four AI agents handling trading decisions, client services, and FA outreach. It runs **offline** using gemma4:e2b via llama.cpp on the Jetson's GPU, or against a cloud model (currently Nemotron3 Super via OpenRouter) when internet is available — switch backends with Hermes's own `model` command; nothing in the pipeline scripts needs to change either way.

**Floating NAV (September 29):** OCRFF's NAV is now a real floating NAV — portfolio value ÷ shares
owned by clients — recalculated by `nav_engine.py` every time prices change or a client buys/sells.
See the *Floating NAV* section below.

**October 6 update:** Venus closes now open new client accounts with shares issued at NAV (NAV no
longer jumps on a close); Venus game meetings are pinned to the game's date and time; position limits
are 10% per stock / 15% per ETF (new buys of an over-limit ticker are rejected); Neptune tracks
**realized** gain alongside unrealized. See *Files Updated October 6, 2026*.

**Relationship to stock_dashboard:** The paper trading pipeline reads research data FROM stock_dashboard. Always run `sync_pipeline_data.sh` before a paper trading session.

---

## Hardware & Model

- **Machine:** Jetson Orin Nano Super | 7607 MiB VRAM
- **Model:** `gemma4:e2b Q4_K_M` via llama.cpp Docker container (offline), or a cloud model via OpenRouter (currently Nemotron3 Super) when online — switch with `hermes model`
- **Mode:** Text-only (no `--mmproj` vision encoder — saves ~590MB VRAM)
- **Port:** 8081 (8080 is SearXNG)
- **Context:** 65536 tokens
- **Agent calls:** Hermes `-z prompt --ignore-rules` subprocess

**Start the model:**
```bash
bash ~/paper_trade/start_gemma4_km_8081.sh
```

**Verify model is running:**
```bash
curl -s http://localhost:8081/v1/models | head -3
```

---

## Full Pipeline Run (Quick Reference)

```bash
# 1. Restore clean holdings (if needed) — the ORIGINAL snapshot carries May 21 prices, so
#    ALWAYS follow it with update_prices.py (step 2), which also recalculates the NAV.
#    ⚠️ Restore the clients file too (cp venus/crm_clients_ORIGINAL.json venus/crm_clients.json).
#    Restoring only the holdings drops the cash that new Venus-closed accounts paid in while
#    their shares stay on file, and the NAV comes out too low (and vice versa).
cp ~/paper_trade/neptune_holdings_ORIGINAL.json ~/paper_trade/neptune_holdings.json

# 2. Sync data and update prices (yfinance closes for every holding; also refreshes NAV
#    and every client's account value — see Floating NAV)
bash ~/paper_trade/sync_pipeline_data.sh
python3 ~/paper_trade/update_prices.py

# 3. Neptune trading session
cd ~/paper_trade
python3 generate_sample_orders.py
python3 neptune.py --orders data/neptune_orders_$(date +%Y%m%d)*.json --verbose

# 4. Orbit client services
cd ~/paper_trade/orbit
python3 generate_issues.py
python3 orbit.py --issues orbit_issues_latest.json --verbose
#    Paperwork loop only, no new issues (forms are filled, signed, reviewed and applied):
#    python3 orbit.py --issues ~/empty_issues.json --defect-rate 0 --max-images 10

# 5. Venus sales pipeline
cd ~/paper_trade/venus
mkdir -p venus_outbox/archive
mv venus_outbox/email_*.json venus_outbox/archive/ 2>/dev/null || true
python3 fetch_prospects.py --strategy mixed
python3 venus.py --week 1
python3 fa_agent.py --all --verbose
python3 venus.py --week 2
python3 venus.py --week 3
python3 venus.py --week 4
python3 venus.py --week 5

# 6. Build dashboards
python3 ~/paper_trade/build_neptune_dashboard.py
python3 ~/paper_trade/venus/build_venus_dashboard.py
python3 ~/paper_trade/orbit/build_orbit_dashboard.py
xdg-open ~/paper_trade/neptune_dashboard.html      # use `open` on the Mac
xdg-open ~/paper_trade/venus/venus_dashboard.html
xdg-open ~/paper_trade/orbit/orbit_dashboard.html
```

**Estimated runtime:** under 15 minutes for the full pipeline on cloud LLMs (via Hermes).
Running fully offline on local models is much slower — roughly 60-90 minutes with gemma4:12b on the M4.

## Hermes Agent Profiles

Four text profiles point to llama.cpp on port 8081 (or the cloud model when online); a fifth, `form_agent`, is the image model for Orbit's paperwork loop, and Orbit's form reviewer uses the `orbit` profile on `google/gemma-4-26b-a4b-it`.

| Profile | Role | SOUL.md location |
|---|---|---|
| `neptune` | Paper trading risk manager | `~/.hermes/profiles/neptune/SOUL.md` |
| `venus` | FA outreach & sales agent | `~/.hermes/profiles/venus/SOUL.md` |
| `orbit` | Client services rep | `~/.hermes/profiles/orbit/SOUL.md` |
| `fa` | FA gatekeeper simulator | `~/.hermes/profiles/fa/SOUL.md` |
| `form_agent` | Fills and signs Orbit paperwork forms (image model, Qwen via OpenRouter) | `~/.hermes/profiles/form_agent/` — settings only; the image call goes straight to OpenRouter using this profile's model/key |

**Create profiles (if needed):**
```bash
hermes profile create neptune --clone-from default --description 'Paper trading risk manager for OC-CLIENT-001.'
hermes profile create venus --clone-from default --description 'AI sales assistant for Obsidian Capital.'
hermes profile create orbit --clone-from default --description 'Client services rep for Obsidian Capital.'
hermes profile create fa --clone-from default --description 'Simulates FA gatekeepers. Persona defined per-call.'

# Disable toolsets to prevent tool loading (kills performance)
for profile in neptune venus orbit fa; do
  sed -i 's/^toolsets:.*/toolsets: []/' ~/.hermes/profiles/$profile/config.yaml
done
```

**Write SOUL.md files** (updated September 28 — replace all four):
```bash
# Neptune — evaluate orders as submitted, no scope creep
cat > ~/.hermes/profiles/neptune/SOUL.md << 'EOF'
# Neptune — Paper Trading Risk Manager
## Obsidian Capital | Account OC-CLIENT-001

You are Neptune, the paper trading risk manager for Obsidian Capital.

## Your role
You evaluate trade orders submitted by Jupiter (equities) and Mercury (ETF proxies)
and decide whether to approve or reject them.

## Decision criteria
Approve when: rationale cites specific data, size is within the current order_limits
range from neptune_config.json (shown to you in the prompt's PORTFOLIO GUARDRAILS
section — cite those exact figures, don't assume or recall a number from elsewhere),
no concentration breach, cash stays above the configured floor, ticker not restricted.
Reject when: rationale is vague or contradicts data, order breaches limits, FA admits
uncertainty ("I think", "maybe"), ETF tickers not in mercury_allowed_tickers list.

## Scope rules — critical
You evaluate orders exactly as submitted. Never reject a valid order because you prefer
a larger size. Never modify or second-guess the order amount. Size decisions belong to
Jupiter and Mercury. The only size-based rejections are mechanical: below or above the
live min/max in neptune_config.json's order_limits (currently $100K-$250K, but always
read the actual figures from the prompt, not from memory — this range has changed before).

## Rules
- You NEVER fabricate prices, financials, or portfolio data
- Mercury may only trade: BITQ, VDE, IAU
- All other tickers are Jupiter's domain (equities)

## Response format
DECISION: APPROVE or REJECT
RATIONALE: [2-4 sentences citing specific data from the order]
CONFIDENCE: HIGH, MEDIUM, or LOW
EOF

# Venus — no AI disclosure, Jay attends meetings
cat > ~/.hermes/profiles/venus/SOUL.md << 'EOF'
# Venus — AI Sales Assistant
## Obsidian Capital

You are Venus, the sales coordinator for Obsidian Capital, an AI-driven investment
research firm in California. You support Jay, the founder and portfolio manager.

## Your role
Draft personalized outreach emails to financial advisors and follow up to schedule
face-to-face meetings between Jay and the FA. You do not attend meetings. Jay does.
Never say "I'd love to meet you" — say "Jay would love to meet you."

## Writing style
- Casual but professional — savvy wholesaler, not corporate robot
- Short: 4-6 sentences for outreach, slightly longer for follow-ups
- Personal: reference interests, an upcoming game (baseball, basketball, or football),
  or mutual connections — an FA may follow more than one team; lean on whichever hook
  feels most natural rather than listing all of them in Week 1
- One clear ask per email
- Never say "I have openings on their calendar" — ask what works for THEM
- Sign off as: — Venus | Obsidian Capital

## Identity
Never introduce yourself as an AI. Do not say "I'm Venus, an AI assistant."
Real wholesalers don't open emails announcing what they are. Just write naturally.

## Pipeline awareness
Week 1: initial outreach. Week 2: read reply, propose specific meeting — if the FA
follows multiple teams with games live at the same time, offer all of them and let
the FA choose rather than picking one for them.
Week 3: FA confirms slot (and, if more than one activity was offered, which one).
Week 4: Jay attends — you debrief afterward.
Week 5: send Jay a voice-memo-style debrief email summarizing the cycle.
EOF

# Orbit — client services, top-5 policy, full-close alerts
cat > ~/.hermes/profiles/orbit/SOUL.md << 'EOF'
# Orbit — Client Services Representative
## Obsidian Capital

You are Orbit, client services at Obsidian Capital.
Venus handles FA/sales. You handle existing clients.

FIRM POLICIES:
1. HOLDINGS: Share TOP 5 ONLY by weight. Never the full list.
2. ADDRESS CHANGES: Pending verification. Written auth required. 3-5 days.
3. BENEFICIARY CHANGES: Signed form required. Coordinate through FA.
4. INTERNAL OPS: Never disclose Neptune escalations or which positions were sold.
5. FULL CLOSE: Alert Venus immediately — FA relationship at risk.

Tone: warm and reassuring. Acknowledge the client's concern before explaining policy.
Never say "I can't do that" — say "Here's what I need from you."

Sign as: — Orbit | Obsidian Capital Client Services
EOF

# FA Agent — realistic gatekeeper, Jay attends not Venus
cat > ~/.hermes/profiles/fa/SOUL.md << 'EOF'
# FA Agent — Financial Advisor Gatekeeper Simulator

You follow the instructions given in each prompt exactly. Simulate a financial advisor
or their assistant receiving a pitch from Venus (Obsidian Capital's sales coordinator).

## Important context
Venus coordinates outreach. Jay (portfolio manager) attends the face-to-face meeting.
When confirming a meeting, say "looking forward to meeting Jay" — not Venus.

## Decision rules
YES when: pitch references a mutual connection, personalizes to specific interests
(baseball/basketball/football tickets, golf, sailing), performance data is specific,
FA is new (book < 30).
NO when: pitch is cold/generic, FA has a full book, pitch leads with product features.

## Response format
DECISION: YES or NO
REPLY:
[2-4 sentence reply email, in character as FA or assistant]
EOF
```

**Smoke test:**
```bash
neptune -z "Reply with exactly: NEPTUNE_ONLINE" --ignore-rules
venus -z "Reply with exactly: VENUS_ONLINE" --ignore-rules
orbit -z "Reply with exactly: ORBIT_ONLINE" --ignore-rules
fa -z "Reply with exactly: FA_ONLINE" --ignore-rules
```

---

## Floating NAV (new September 29, 2026)

```
NAV per share  =  portfolio value  /  shares owned by clients
portfolio value = positions at last price + VMRXX cash      (neptune_holdings.json)
shares owned    = sum of ocrff_shares over every client     (venus/crm_clients.json)
```

Client buys and sells execute AT the NAV, so they add or remove shares and dollars in the same
proportion and leave the NAV unchanged. Only price moves change it. `nav_engine.py` is the one
place NAV is calculated; `update_prices.py` and `orbit.py` call it automatically.

| What | Where |
|---|---|
| Calculation and CLI | `nav_engine.py` |
| Fund file fields it writes | `venus/ocrff_fund.json`: `current_nav`, `nav_date`, `shares_outstanding`, `current_aum` |
| Client values | every client's `ocrff_account_value` = `ocrff_shares` × NAV (stored to 6 decimals) |
| History | `venus/nav_history.json` — one line per refresh (date, source, NAV, portfolio value, shares) |
| One-time backups | `*_BEFORE_FLOATING_NAV.json`, `neptune_holdings_BEFORE_RESTORE_<timestamp>.json` |

```bash
python3 nav_engine.py                 # show portfolio value, shares, NAV (writes nothing)
python3 nav_engine.py --apply         # recalculate and save NAV + client values
python3 nav_engine.py --restore-holdings neptune_holdings_ORIGINAL.json   # reset holdings first (backs up current)
```

`nav_date` is the date the holdings were last priced, not the day the script ran.

**Venus closes (October 6):** a close used to add cash with no shares, inflating the NAV (+0.34% on the
Oct 6 run). `venus_new_clients.py` now handles it: the close is split into 2-4 fictional client accounts
(IDs continue after `OC-CLIENT-1000`, linked to the FA's ID) that add up to exactly the deal value;
each is issued `dollars / NAV` shares at the NAV read *before* any cash goes in; the cash and inflow-log
entry are added; then NAV is refreshed. All-or-nothing — if any step fails, both files are put back and
the close is not recorded (rerun Week 4). The FA's `oc_aum` / `oc_clients` in `crm_advisors.json` now
add up across closes instead of being overwritten, and list `oc_account_ids`.

**One-time repair for closes booked the old way** (run from `paper_trade/`):
```bash
python3 venus/repair_venus_nav.py            # preview: shows the pre-injection NAV and the accounts it would open
python3 venus/repair_venus_nav.py --apply    # backs up the files, issues the missing shares, refreshes NAV
```
It uses `pre-injection NAV = (portfolio value − unissued deal money) / client shares`, so run it before
prices move (or pass `--nav X`). Safe to run twice.

**`update_prices.py` (rewritten September 29):** default source is now `--source yfinance` — the latest
daily close for every holding, stored with its real date (`price_date`, `price_source: yfinance_close`);
`last_updated` is the newest of those dates. Pipeline JSON prices are only a fallback for tickers yfinance
cannot return (a warning says their dates are unverified). `--source pipeline` restores the old behaviour.
Run it after 1pm Pacific to get a full-day close.

---

## Returns Backtest (new September 29, 2026)

The performance table in `venus/ocrff_fund.json` is generated, not typed:

```bash
cd ~/paper_trade
# preview (writes nothing)
python3 backtest_fund_returns.py --holdings neptune_holdings.json --fund venus/ocrff_fund.json \
    --prices-csv prices.csv --late-start drop
# refresh the price history first if it is stale
python3 backtest_fund_returns.py --holdings neptune_holdings.json --fund venus/ocrff_fund.json \
    --late-start drop --save-prices prices.csv
# write it (backs up the fund file, updates returns, risk metrics and the inception NAV)
python3 backtest_fund_returns.py --holdings neptune_holdings.json --fund venus/ocrff_fund.json \
    --prices-csv prices.csv --late-start drop --write --rebase-inception-nav --update-risk
```

Method (Option B, "fixed shares"): the fund has held the holdings file's share counts since 2020-01-02
(buy-and-hold), dividend-adjusted closes, SPY as the S&P proxy, cash held at its current weight (about
12.7%) earning 3%/yr. Each period (YTD, 6M, 1Y, 3Y, 5Y, annual years) includes every position that has
a price at the period's start; a position that listed later (GEV 2024, BITQ 2021) is left out only of the
periods that begin before it existed. The run prints "positions in each period" so you can see this.
`--fixed-universe` restores the old behaviour (late listings dropped from every period).
`--cash-mode fixed` (a flat dollar cash balance) is not recommended: cash was ~38% of the fund in 2020.

`ytd_check.py` is an independent cross-check — per-ticker YTD return and contribution, and the fund's YTD
under four assumptions:

```bash
python3 ytd_check.py --holdings neptune_holdings.json --prices-csv prices.csv
```

The `--allocation`, `--rebalance`, `--cash-rate`, `--sp-ticker` options are in `--help`.

---

## Orbit Paperwork Loop (new September 2026)

Address changes and beneficiary designations are now real paperwork, end to end:

1. Orbit asks the client for a form (`AWAITING_FORM`).
2. An image model (Hermes profile `form_agent`, Qwen via OpenRouter, ~$0.078/form) fills in and signs the
   Address Change or Beneficiary Designation form. Beneficiary forms are also notarized.
3. Orbit's reviewer (profile `orbit`, Gemma `google/gemma-4-26b-a4b-it`, ~$0.0005/form) reads the image
   BLIND into JSON; Python rules then decide APPROVE or REJECT. The vision model never makes the decision.
4. Approved forms change `venus/crm_clients.json` (address / beneficiary list) with a `change_history`
   entry. Rejected forms stay `RETURNED` and are re-requested next run, with a CORRECTIONS paragraph
   built from the previous findings. Orbit's LLM writes the approval / rejection letters.

| File | Role |
|---|---|
| `orbit/form_agent.py` | Builds the prompt and generates the form image (`generate_form`, `retry_text`) |
| `orbit/form_review.py` | Blind reader + rules; run alone with `python3 form_review.py <form>.json` to re-review one form |
| `orbit/form_flow.py` | `process_forms` — orchestrates request, generate, review, apply, letters |
| `orbit/orbit_forms/` | Generated form images and their manifests |
| `venus/crm_clients_ORIGINAL.json` | Clean CRM for test resets |

CRM schema v2: `address`, `beneficiary` list, `change_history`, `pending_changes` (by kind; statuses
`AWAITING_FORM`, `FORM_RECEIVED`, `RETURNED`, `APPLIED`), client `status` ACTIVE/INACTIVE/CLOSED.
Reset for testing: `cp venus/crm_clients_ORIGINAL.json venus/crm_clients.json`

**Options and settings**

| Flag / variable | Meaning |
|---|---|
| `--max-images N` | Cap on generated images per run (cost control) |
| `--defect-rate R` | Share of forms given a planted defect; use `0` (real defects come from the image model) |
| `--reprocess` | Re-run issue IDs already in an `orbit_outbox/orbit_run_*.json` (otherwise they are skipped) |
| `ORBIT_REVIEW_READS` | Reads per form (default 2); split findings get a tiebreak third read, majority wins |
| `ORBIT_FILL_PRINTED_NAME` | `1` fills the bottom Printed Name box (default off — the box is optional) |
| `ORBIT_DEFECT_RATE` | Environment fallback for `--defect-rate` |

**Review rules** (finding codes): missing_signature, missing_date, stale_date, future_date, name_mismatch,
wrong_account_number, blank_required_field, illegible_field, current_address_mismatch, address_mismatch,
missing_delivery_choice, wrong_delivery_choice, percent_not_100, designation_mismatch, blank_relationship,
not_notarized, missing_notary_seal, missing_notary_signature, notary_expired, notary_date_before_signing,
notary_signer_mismatch, notary_signature_mismatch, misplaced_entry.

**Robustness layers:** two reads with a majority tiebreak; a separate cropped signature-strip check for
missing_signature; `misplaced_entries` (a value written in the wrong box) collapses its knock-on findings
into one clear finding; a `read_disagreement` note is stored and shown on the dashboard. Concerns that
mention "future" are hidden on the dashboard (the reader believes 2026 is the future).

**Idempotency:** issue IDs are `ISS-<run stamp>-NNN`; `orbit.py` skips any issue already in a saved run
(no duplicate orders). `orbit.py` also refreshes the NAV at start.

---

## Venus 5-Week Pipeline

Venus now uses a 5-week FA sales pipeline, all orchestrated through `venus.py --week N`.

```bash
cd ~/paper_trade/venus

python3 fetch_prospects.py --strategy mixed   # Select 5 FAs
python3 venus.py --week 1                     # Venus sends outreach emails
python3 fa_agent.py --all --verbose           # FA Agent reads and replies
python3 venus.py --week 2                     # Venus proposes meetings
python3 venus.py --week 3                     # FA confirms meeting slot
python3 venus.py --week 4                     # Simulate meeting, close or no-close
python3 venus.py --week 5                     # Venus debrief email to Jay
python3 build_venus_dashboard.py              # Rebuild dashboard
```

**Meetings are pinned to the event.** For a game, the meeting date/time is the game's own
(`sports_hooks[].upcoming.next_home`); each live hook gets its own slot, and Week 3 confirms the one the FA
picks — never a Monday/Tuesday "open slot". If the feed gives only "night"/"afternoon" the slot uses a
stand-in start time (7:00 PM / 1:00 PM) and is flagged `time_estimated`; if the date can't be read, Week 2 warns
and falls back to open slots. Non-game meetings (coffee, lunch, outdoor) use open slots chosen to avoid
the FA's blocked calendar time (previously hardcoded Mon 10:30 / Tue 1:00).

`fetch_prospects.py --strategy` also accepts `sports` (any of MLB/NBA/NFL, generalizing
the older `baseball`-only strategy, which still works on its own).

**Archive old outbox before a new cycle:**
```bash
mkdir -p ~/paper_trade/venus/venus_outbox/archive
mv ~/paper_trade/venus/venus_outbox/email_*.json \
   ~/paper_trade/venus/venus_outbox/archive/
```

**Pipeline week prerequisites:**
- Week 2 requires Week 1 + `fa_agent.py --all` to have run
- Weeks 3-5 each require the previous week complete
- Scripts enforce prerequisites and print clear errors if missing

---

## Mercury ETF Trading (Data-Driven)

Mercury's BUY/SELL/no-order calls for its three ETF proxies (BITQ, VDE, IAU) are now
built entirely from real data in `mercury_latest.json` — no keyword-matching prose and
no random fallback when there's no clear signal.

**Signal logic** (`generate_sample_orders.py`):
- Parses per-ticker momentum (`data.etf_data`: 1d/1m/3m/1y % change, vs. 52-week high)
- Parses the category's macro stance (`data.mercury_backdrop`: e.g. gold/energy/crypto
  stance, with bullish/bearish polarity read from the qualifier in parentheses)
- Parses CFTC Commitment of Traders positioning (`data.cot_report`) for an EXTREME
  long/short flag on the matching asset
- **BUY** when momentum ≥ +5% AND stance isn't bearish AND there's no extreme-long
  COT position working against it
- **SELL** when momentum ≤ -5% AND stance isn't bullish AND the ETF is currently held
- **No order** when none of the above line up — this is expected and normal, not an
  error (you'll see a line like `⚠️ VDE: -0.67% 1m, stance bullish — no clear signal,
  no order` in the output)

**Order sizing** — both Jupiter (equities) and Mercury generate order sizes by reading
`min_order_dollars`/`max_order_dollars` live from `neptune_config.json`'s `order_limits`
(currently $100,000–$250,000). This used to be hardcoded and wider than Neptune's real
approval threshold, which meant orders could be auto-rejected as too large; it's now
always in sync with whatever Neptune is actually configured to approve.

---

## NBA/NFL/MLB Sports Hooks (Venus)

As of September 2026, Venus's "sports hook" personalization covers all three
California pro leagues, not just baseball (useful once baseball season ends).

**CRM fields** (`crm_advisors.json`): an FA's team fandom is stored per-league —
`mlb_team`, `nba_team`, `nfl_team` — each independently optional. Modeled realistically:
about 40% of FAs have at least one sports hook on file across all three leagues
combined (not per-league), split roughly 70/25/5 between following one/two/three teams.
An FA can be a fan without a team code on file if they haven't told us which team yet —
those FAs get no game-based hook, same as a non-fan.

**Schedule data:** `venus/baseball_schedules.json`, `venus/nba_schedules.json`,
`venus/nfl_schedules.json` — same schema across all three (`teams`, `schedules`, each
game with `date`/`day_of_week`/`opponent`/`home`/`time`/`played`).

**Venue data:** `venues.json`'s `stadiums` block is namespaced by league
(`stadiums.mlb.*` / `stadiums.nba.*` / `stadiums.nfl.*`) because team codes collide
across leagues — `LAC` is both the Clippers (NBA) and the Chargers (NFL). Lookups
must always go through the league-qualified path, never a flat dict.

**"Live" hooks only:** `fetch_prospects.py` builds a `sports_hooks` list per prospect —
one entry per league the FA follows, but only ones with an actual upcoming home game in
the next 30 days count as "live." A team with no game coming up (off-season, or just
nothing scheduled soon) isn't offered as a meeting activity — Venus falls back to a
non-sport interest, lunch, or coffee instead. This was the whole point of the change:
previously a fan was always offered a game invite regardless of whether one existed.

**Multiple live hooks → FA chooses:** if an FA follows more than one team and several
have games coming up at once (e.g. Lakers + Chargers + Dodgers all live), Venus's Week 2
meeting-proposal email lists all of them and asks the FA which they'd prefer, instead of
silently picking one by fixed priority. Week 3 (`venus_week3.py`) then actually makes
that choice on the FA's behalf in Python — pre-selected the same way the meeting time
slot is, to avoid the LLM defaulting to whichever option was listed first or last.

**Continuity detection is schedule-aware:** when Venus reads a Week 1 reply to figure
out which activity the FA is responding to, it checks team names/stadiums mentioned in
the text — but only honors a team mention if that team actually has a live game. This
fixes a real bug: an FA who mentioned being "a Padres fan" while asking for coffee (no
Padres game was actually scheduled) previously got a Week 2/3 meeting built around a
game that didn't exist. Now that kind of passing mention correctly falls through to
whatever the FA actually asked for.

---

## File Structure

```
~/paper_trade/
├── .gitignore                # Ignores *_bak.py backup copies
├── ollama_client.py          # Direct Ollama fallback (for Mac compatibility)
├── neptune.py                # Prompt now includes live order/position/cash limits
├── neptune_config.json       # order_limits: $100K-$250K (min raised from $25K)
├── neptune_holdings.json
├── neptune_holdings_ORIGINAL.json
├── generate_sample_orders.py # Mercury rebuilt on real data; order range read live
├── generate_sample_orders_bak.py  # Pre-rewrite backup (gitignored)
├── update_prices.py          # ⭐ yfinance closes for all holdings, real price dates; refreshes NAV
├── nav_engine.py             # ⭐ New — floating NAV (portfolio value / client shares)
├── backtest_fund_returns.py  # ⭐ New — rebuilds OCRFF performance/risk table from price history
├── ytd_check.py              # ⭐ New — independent YTD cross-check by weighting assumption
├── prices.csv                # ⭐ Cached price history (written by --save-prices)
├── sync_pipeline_data.sh
├── build_neptune_dashboard.py
├── neptune_dashboard.html
├── start_gemma4_text_8081.sh
├── obsidian_editor.html      # Web-based JSON editor (port 8765)
├── editor_server.py
├── data/
│   ├── research_latest.json
│   ├── mercury_latest.json
│   ├── rankings_latest.json
│   ├── macro_backdrop.yaml
│   └── watchlist.json
├── reports/
├── venus/
│   ├── venus.py              # ⭐ Pipeline runner (--week 1-5); multi-league Week 1 hooks
│   ├── venus_week2.py        # ⭐ Week 2: replies, calendar, meeting proposals; offers
│   │                         #    ALL live sport hooks; schedule-aware continuity check
│   ├── venus_week3.py        # ⭐ Week 3: FA confirms slot AND which activity (randomized)
│   ├── venus_week4.py        # ⭐ Week 4: simulate meeting; a close opens new client accounts at NAV
│   ├── venus_week5.py        # ⭐ Week 5: debrief email to Jay
│   ├── fa_agent.py           # ⭐ FA gatekeeper (runs between Week 1 and 2); recognizes
│   │                         #    basketball/football fandom in personality, not just baseball
│   ├── fetch_prospects.py    # ⭐ Builds sports_hooks across MLB/NBA/NFL, live-game aware
│   ├── build_venus_dashboard.py  # ⭐ 5-tab dashboard with orbit alert cards
│   ├── venues.json           # ⭐ Real venues: stadiums (league-namespaced), restaurants, outdoor
│   ├── crm_advisors.json     # mlb_team/nba_team/nfl_team fields; ~40% combined sports-hook rate
│   ├── crm_clients.json
│   ├── baseball_schedules.json
│   ├── nba_schedules.json    # ⭐ New — Lakers/Clippers/Kings/Warriors
│   ├── nfl_schedules.json    # ⭐ New — Rams/Chargers/49ers
│   ├── ocrff_fund.json       # ⭐ current_nav, shares, AUM, returns, risk — all generated
│   ├── ocrff_fund.json.bak-*   # backups written by backtest --write
│   ├── crm_clients_ORIGINAL.json  # clean CRM for test resets
│   ├── nav_history.json      # ⭐ New — one line per NAV refresh
│   ├── venus_outbox/
│   │   └── archive/
│   ├── fa_calendars/         # ⭐ Persisted FA weekly calendars
│   ├── fa_responses/
│   └── pipeline_reports/     # ⭐ Week 5 debrief JSON reports
└── orbit/
    ├── orbit.py              # ⭐ --defect-rate, --reprocess; issue idempotency; refreshes NAV
    ├── generate_issues.py
    ├── trade_engine.py       # ⭐ client BUY no longer dilutes cash/NAV
    ├── form_agent.py         # ⭐ New — fills and signs address/beneficiary forms (image model)
    ├── form_review.py        # ⭐ New — blind reader + rules decide APPROVE/REJECT
    ├── form_flow.py          # ⭐ New — paperwork loop orchestration and letters
    ├── orbit_forms/          # ⭐ New — generated forms and manifests
    ├── build_orbit_dashboard.py
    ├── orbit_dashboard.html
    ├── orbit_outbox/
    ├── orbit_statements/
    └── neptune_escalations.json
```

---

## Files Updated October 6, 2026

| File | Change |
|---|---|
| `neptune_config.json` | `position_limits`: 10% equity / 15% ETF (v1.1) |
| `neptune.py` | Per-asset-type concentration check (over-limit tickers can't be bought); realized gain on SELLs; direction-aware cash-after in the prompt; tolerant `DECISION:` parsing; prices rounded to 4 dp |
| `generate_sample_orders.py` | Doesn't propose BUYs for tickers at/near their limit |
| `build_neptune_dashboard.py` | Unrealized / Realized cards; Realized G/L column |
| `venus/venus_new_clients.py` | New. Close → new client accounts, shares at NAV, cash in, NAV refresh, rollback |
| `venus/venus_week4.py` | Uses the above; FA `oc_aum`/`oc_clients` add up |
| `venus/venus_week2.py` | Game-pinned slots per option; conflict-free open slots |
| `venus/venus_week3.py` | Confirms the chosen game's own date/time |
| `venus/repair_venus_nav.py` | New, one-time NAV repair for old-style closes |
| `backfill_realized.py` | New, one-time realized-gain backfill |

## Files Updated September 29, 2026

| File | Change |
|---|---|
| `nav_engine.py` | New. Floating NAV = portfolio value / client shares; writes `current_nav`, `nav_date`, `shares_outstanding`, `current_aum`, re-values every client, appends `nav_history.json`; `--restore-holdings` |
| `update_prices.py` | Default source is now yfinance closes for every holding with their real dates (pipeline JSON is a fallback only); calls `nav_engine.refresh` after saving; `--source pipeline` for the old behaviour |
| `backtest_fund_returns.py` | Per-period position inclusion (`PeriodFund`) so BITQ/GEV count in every period that begins after they listed; `--fixed-universe`; no crash when the fund file has no `inception_nav` |
| `ytd_check.py` | New — per-ticker YTD and four YTD variants (portfolio weighting, positions only, fixed shares, backtest style) |
| `orbit/orbit.py` | Refreshes NAV at start; `--defect-rate`, `--reprocess`; skips issue IDs already processed |
| `orbit/trade_engine.py` | `execute_ocrff_buy` only adds cash (the money stays in VMRXX) — previously it added then subtracted, diluting the NAV |
| `orbit/form_agent.py`, `form_review.py`, `form_flow.py` | The paperwork loop and its reviewer hardening (see Orbit Paperwork Loop); Printed Name box optional; retry CORRECTIONS; letters use full name, no invented policy |
| `orbit/build_orbit_dashboard.py` | Hides "future" concerns; shows "the two reads disagreed on…" |
| `venus/venus.py`, `venus/fa_agent.py` | Performance figures print with their own sign (negative alpha no longer shows as "+-5.2%") |

**Fixed this session**

| Bug | Fix |
|---|---|
| Client values ($213.2M) did not match the portfolio ($148.6M); NAV was a stored number | NAV is calculated from portfolio value / client shares |
| Client BUY diluted the NAV | `execute_ocrff_buy` keeps cash in VMRXX |
| NAV at 4 decimals left client values ~$64 off the portfolio | Stored to 6 decimals |
| `nav_date` stamped with today for older prices | Uses the holdings' `last_updated` |
| Holdings prices labelled "today" but taken from stale pipeline data (NAV overstated by 1.7%) | `update_prices.py` uses dated yfinance closes; `price_date` is the real date |
| BITQ and GEV dropped from YTD/1Y/6M/3Y/5Y, understating short periods by ~2 points | Per-period inclusion in the backtest |
| Risk metrics (Sharpe 1.42, drawdown −18.3%, beta 0.87) were not from the same data as returns | `--update-risk`: 1.2, −23.0% (Feb–Mar 2020), 0.73 |
| Fund file `shares_outstanding` / `current_aum` stale | `nav_engine` maintains them |
| Duplicate orders when a run was repeated | Issue idempotency in `orbit.py` |
| Reader misread checkbox / signature / a 2030 date as 2020 | Per-box booleans, signature-strip crop check, majority tiebreak read |
| Scrambled forms produced 5–8 confusing findings and letters | `misplaced_entries` collapse; letters cleaned |
| Negative performance figures printed as "+-5.2%" | Sign formatting in `venus.py` / `fa_agent.py` |

**Known / open**
- The escalated-sell case has not been checked live for NAV conservation (NAV before and after should match).
- Sample emails written before September 29 (e.g. the Jerry Roberts thread) quote the old "+10.3% YTD alpha"; the fund file now says +2.9%. New outreach uses the correct figures.
- Restoring `neptune_holdings_ORIGINAL.json` without also restoring `venus/crm_clients_ORIGINAL.json` (or vice versa) breaks the NAV: cash and client shares must move together.
- `neptune_holdings_ORIGINAL.json` carries May 21 (HTML-import) prices; always run `update_prices.py` after restoring it.
- Six-month alpha is negative (−5.2%): the fund is roughly flat since May 21 while SPY is up ~3%.

---

## Files Updated September 2026 (earlier this month)

| File | Change |
|---|---|
| `generate_sample_orders.py` | Mercury rebuilt to trade on real `mercury_latest.json` data (momentum + macro stance + COT positioning) instead of keyword-matching prose with a random fallback; both Jupiter's and Mercury's order-size range now read live from `neptune_config.json` instead of a hardcoded tuple |
| `neptune_config.json` | `order_limits.min_order_dollars` raised from $25,000 to $100,000 (max stays $250,000) |
| `neptune.py` | The LLM prompt now includes a `PORTFOLIO GUARDRAILS` block with the live order-size range, max position %, and cash floor % — previously these were loaded from config but never actually included in the prompt, so Neptune's own rationale text could cite stale or invented figures (caught reciting the old $25K floor after the config was raised to $100K) |
| `venus/fetch_prospects.py` | Fixed a stale `fa.get("team", "LAD")` lookup (broken since the CRM rebuild removed that field); now builds a `sports_hooks` list covering MLB/NBA/NFL — whichever leagues the FA follows and has a live (actually scheduled, within 30 days) home game for; added `--strategy sports` |
| `venus/fa_agent.py` | FA personality simulation now recognizes Basketball/Football fandom, not just Baseball/Golf/Surfing |
| `venus/venus.py` | Week 1 prompt now builds its sports-hook context from all leagues the FA follows, not baseball only; persists `sports_hooks` into the Week 1 outbox record so Week 2 has real schedule data to work from |
| `venus/venus_week2.py` | `select_activity_and_venue()` rewritten: only offers a sport as the meeting activity if there's an actual live game; when an FA follows multiple teams with live games at once, offers all of them via `alt_options`; continuity detection (`_detect_activity_in_text`) is now team-specific across all three leagues and schedule-aware — a team mention with no live game no longer gets treated as a real hook (fixes the "confirmed meeting for a game that doesn't exist" bug); fixed a stadium lookup broken by `venues.json`'s league-namespaced restructuring |
| `venus/venus_week3.py` | When Week 2 offered multiple live hooks, Week 3 now actually picks one (pre-selected in Python, same reasoning as the existing time-slot pre-selection) instead of always confirming the primary pick and ignoring the alternatives |
| `venus/venus_week4.py` | Added basketball/football-specific narrative guidance to the meeting recap prompt (previously only baseball had detail guidance) |
| `venus/venues.json` | `stadiums` restructured into `stadiums.mlb/nba/nfl.*` (NBA Clippers and NFL Chargers both use code `LAC`); added all NBA/NFL venues with ticket-demand tiers; added Basketball/Football entries to `activity_close_modifiers` and `activity_to_interest_map` |
| `venus/crm_advisors.json` | Rebuilt sports-fandom data: `mlb_team`/`nba_team`/`nfl_team` fields, ~40% of FAs have at least one hook (combined across leagues, not per-league), assigned by territory |
| `venus/nba_schedules.json`, `venus/nfl_schedules.json` | New — full-season schedules for the CA NBA/NFL teams, same schema as `baseball_schedules.json` |
| `.gitignore` | New — excludes `*_bak.py` backup copies from the repo |

**Also update SOUL.md files on Jetson** (content above already reflects these changes):
```bash
~/.hermes/profiles/neptune/SOUL.md  # No longer hardcodes $25K-$250K — cites live config
~/.hermes/profiles/venus/SOUL.md    # Mentions all 3 leagues, multi-hook choice in Week 2
~/.hermes/profiles/fa/SOUL.md       # Basketball/football added alongside baseball
~/.hermes/profiles/orbit/SOUL.md    # Unchanged this round
```

---

## Files Updated June 25, 2026

Copy these from Mac to Jetson (`~/paper_trade/venus/`):

| File | Change |
|---|---|
| `venus.py` | `--week` flag pipeline runner, retry logic for blank emails |
| `venus_week2.py` | Plain-text calendar generation, materials detection, Ollama fallback |
| `venus_week3.py` | Python pre-selects meeting slot (fixes Tuesday bias), Ollama fallback |
| `venus_week4.py` | 7-reason no-close pool, parent-dir holdings path, Ollama fallback |
| `venus_week5.py` | Ollama fallback import |
| `fa_agent.py` | Ollama fallback import |
| `build_venus_dashboard.py` | 5-tab dashboard, orbit alert cards, scoreboard metrics |
| `venues.json` | New file — real venues by territory and activity |
| `update_prices.py` | IAU/VDE excluded from mercury extraction, $500 price ceiling |

---

## Systemd Services

```bash
# Start all services
sudo systemctl start obsidian-editor  # port 8765 — JSON editor
sudo systemctl start webmcp           # port 8642 — stock MCP
sudo systemctl start macromcp         # port 8643 — macro MCP
sudo systemctl start mercurymcp       # port 8645 — Mercury MCP

# Check status
sudo systemctl status obsidian-editor
```

---

## OCRFF Fund

Every value below is generated by `nav_engine.py` and `backtest_fund_returns.py` — do not edit by hand.

| Metric | Value (as of September 29, 2026) |
|---|---|
| Fund name | Obsidian Capital Research Fund (OCRFF) |
| Inception | January 2020 @ $31.52 NAV (implied by the backtest total return) |
| Current NAV | $102.70 (floating: portfolio value / client shares) |
| Shares outstanding | 2,103,068 |
| AUM | ~$216.0M |
| YTD return | +15.8% vs S&P +12.9% (alpha +2.9%) |
| 6-month | +15.9% vs S&P +21.1% (alpha −5.2%) |
| 1-year return | +19.5% vs S&P +16.4% |
| 3-year / 5-year (annualized) | +29.6% / +16.4% vs S&P +22.8% / +13.5% |
| Since inception | +225.8% (19.1%/yr) vs S&P +158.7% (15.1%/yr) |
| Risk | Sharpe 1.2, max drawdown −23.0% (Feb–Mar 2020), volatility 16.1%, beta 0.73, correlation 0.91 |

---

## Neptune Holdings

**Top positions (snapshot weights; they drift with prices — see the Neptune dashboard):**
- IAU (Gold ETF) — ~15.4% weight (just over its 15% limit; no new IAU buys until it falls back under)
- BITQ (Crypto ETF) — ~13% weight
- VDE (Energy ETF) — ~8.8% weight
- NVDA — ~7.9% weight
- AAPL — ~6.6% weight

**Cash (VMRXX):** tracked in `summary.cash_value` — not as a position entry.
New client money from Venus closes lands here (with matching client shares) via `venus_new_clients.py`.

**Concentration limits** (`neptune_config.json` → `position_limits`): **10%** of portfolio per individual
stock, **15%** per ETF (`max_equity_position_pct`, `max_etf_position_pct`). Python computes the weights and
Neptune treats them as facts. A BUY is rejected if the ticker is already over its limit *or* the order
would push it over; SELLs are never blocked. `generate_sample_orders.py` skips proposing buys Neptune
would bounce, and says why.

**Gain/loss:** *Unrealized* = market value − cost basis of what is still held. *Realized* = locked in by
SELLs, average-cost method (`sell value − pro-rata cost basis`), kept as `summary.realized_gain_total`;
each SELL in the trade log carries its own `realized_gain`. Both appear in the Neptune console output and
dashboard (Unrealized and Realized cards, plus a Realized column in the activity table). Backfill the
Oct 6 sells once with `python3 backfill_realized.py --apply`.

---

## Orbit Issue Types

| Issue Type | Weight | What Happens |
|---|---|---|
| buy | 20% | VMRXX increases, client shares added |
| sell | 25% | VMRXX decreases, escalate if insufficient |
| full_close | 5% | Full redemption + Venus alert card in dashboard |
| statement | 20% | HTML statement in orbit_statements/ |
| address | 15% | Form filled, signed and reviewed; APPROVE updates the CRM address, REJECT returns it |
| beneficiary | 10% | Signed and notarized form, reviewed; APPROVE updates the beneficiary list, REJECT returns it |
| holdings | 5% | Top 5 disclosed, full list withheld |

---

## Testing Neptune Escalation

```bash
# Set VMRXX low to force escalation
python3 - << 'EOF'
import json
with open('/home/jay/paper_trade/neptune_holdings.json') as f:
    h = json.load(f)
h['summary']['cash_value'] = 50000.00
with open('/home/jay/paper_trade/neptune_holdings.json', 'w') as f:
    json.dump(h, f, indent=2)
print("Cash set to $50,000")
EOF

cd ~/paper_trade/orbit
python3 generate_issues.py --force-type full_close --count 1
python3 orbit.py --issues orbit_issues_latest.json --verbose
```

---

## JSON Editor (port 8765)

```bash
sudo systemctl start obsidian-editor
xdg-open http://localhost:8765
```

**Tabs:** FA Profiles (200 FAs), Clients (1,000+ accounts — Venus closes add more), Holdings (39 positions).

---

## Troubleshooting

**llama.cpp crashes on startup:**
- Use `start_gemma4_text_8081.sh` (text-only, no `--mmproj`)
- Add `-fit off -ngl 99` flags

**Hermes -z fails or times out:**
- Check llama.cpp: `curl -s http://localhost:8081/v1/models`
- Check toolsets empty: `grep toolsets ~/.hermes/profiles/neptune/config.yaml`
- `ollama_client.py` provides Ollama fallback if Hermes fails

**Venus Week 1 skipping FAs:**
- New retry logic: model gets 2 attempts before FA is skipped
- `⚠️ Empty/fallback response (attempt 1/2) — retrying...` is normal on cold startup

**Calendar generation falling back to template:**
- Now uses simplified plain-text prompt — should rarely fail
- Template fallback still provides correct open slots — pipeline continues normally

**All FAs confirming Tuesday (slot bias):**
- Fixed in venus_week3.py June 25 — Python now pre-selects the slot randomly
- LLM only writes the confirmation reply, no longer picks the slot

**Neptune rejecting valid sells as "too small":**
- Fixed in SOUL.md June 25 — explicit scope rules added
- Neptune evaluates orders as submitted; size is Jupiter's decision

**IAU price spiking (e.g. $13,483):**
- Fixed in update_prices.py June 24
- IAU/VDE excluded from Mercury narrative extraction
- Both now sourced exclusively from yfinance

**Venus produces "— Venus | Obsidian Capital Sales" sign-off in narratives:**
- Fixed in venus_week4.py June 25 prompt rules
- Narratives are Jay's voice memos — no Venus sign-off

**Neptune's rationale cites a stale order-size limit (e.g. "$25K-$250K" after the
floor was raised to $100K):**
- Fixed in neptune.py September 2026 — the prompt loaded `order_limits`/`soft_criteria`
  from config but never actually included them in the text sent to the LLM, so it was
  reciting a figure from its own assumptions/memory rather than the real config
- Now a `PORTFOLIO GUARDRAILS` block in the prompt states the live thresholds explicitly

**Venus confirms a meeting for a game that isn't on the schedule:**
- Fixed in venus_week2.py September 2026 — continuity detection used to match any team
  name mentioned in an FA's reply (even in passing, e.g. "I'm a Padres fan" while asking
  for coffee) without checking whether that team actually had a game coming up
- Now a team mention only counts as continuity if it's a live hook; otherwise the scan
  falls through to whatever the FA actually asked for

**Mercury generating orders with no real signal, or amounts wider than Neptune's approval range:**
- Fixed September 2026 — Mercury now trades only on real momentum/stance/COT data from
  `mercury_latest.json` (no order at all if there's no clear signal, instead of a random
  BUY/SELL), and both Jupiter's and Mercury's order sizes are read live from
  `neptune_config.json`'s `order_limits` so they can't drift out of sync with what
  Neptune will actually approve

**NAV or account values look wrong / don't match the market:**
- Run `python3 nav_engine.py` (read-only): portfolio value, client shares, NAV
- Check the price dates: `python3 -c "import json;h=json.load(open('neptune_holdings.json'));print(h['last_updated'],{p['price_date'] for p in h['positions'].values()})"`
- The old `update_prices.py` stamped every price with today's date even when the pipeline data was older;
  the current one records each close's real date. Rerun `update_prices.py` (after 1pm Pacific)

**Backtest and the fund file disagree, or the one-pager shows old numbers:**
- The one-pager is `venus/ocrff_fund.json`. Rerun the backtest with `--write --rebase-inception-nav --update-risk`,
  then `python3 nav_engine.py --apply`, then rebuild the Venus dashboard
- Use `--prices-csv prices.csv` to reuse the cache; refresh it with `--save-prices prices.csv`

**Orbit re-requests a form / a form keeps coming back RETURNED:**
- Expected when the reviewer finds a real defect: the form is regenerated next run with a CORRECTIONS paragraph
- Re-review one form without generating a new image: `python3 form_review.py orbit_forms/<FORM>.json`
- Duplicate issues are skipped automatically; add `--reprocess` to force

---

## Cabin Prep Checklist

```bash
# 1. Restore clean holdings
cp ~/paper_trade/neptune_holdings_ORIGINAL.json ~/paper_trade/neptune_holdings.json

# 2. Sync latest data
bash ~/paper_trade/sync_pipeline_data.sh

# 3. Update prices
python3 ~/paper_trade/update_prices.py --verbose

# 4. Start model and verify all agents
bash ~/paper_trade/start_gemma4_text_8081.sh &
sleep 30
neptune -z "NEPTUNE_ONLINE" --ignore-rules
venus -z "VENUS_ONLINE" --ignore-rules
orbit -z "ORBIT_ONLINE" --ignore-rules
fa -z "FA_ONLINE" --ignore-rules

# 5. Start editor service
sudo systemctl start obsidian-editor
```

---

## Model Comparison

| Model | Machine | Speed | Quality | Use for |
|---|---|---|---|---|
| gemma4:e2b Q4_K_M | Jetson | ~12 tok/s | Good | Full pipeline, offline |
| gemma4:12b-it-qat | Mac M4 | ~11 tok/s | Better | Nuanced reasoning, complex prompts |
| gemma4:e4b | Mac M4 | ~25 tok/s | Good | Fast iteration testing |
| Nemotron3 Super (OpenRouter) | Cloud, via Hermes | Full pipeline in under 15 min | Better | When internet is available; switch with `hermes model` |

---

*— Obsidian Capital | paper_trade v1.4 | October 6, 2026*
