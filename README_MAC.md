# Obsidian Capital — Paper Trading System
## Mac Mini M4 Reference Guide
### Updated June 25, 2026

---

## Overview

The paper trading system simulates a $215M investment fund (OCRFF — Obsidian Capital Research Fund) with four AI agents handling trading decisions, client services, and FA outreach. On the Mac Mini, agents call **Ollama directly** via `ollama_client.py` — no Hermes Agent installed.

**Key difference from Jetson:** No Hermes. All agent calls go through `ollama_client.py` which reads SOUL.md from `~/paper_trade/agents/<profile>/SOUL.md` and calls Ollama's API at `http://localhost:11434/api/chat`.

**Relationship to stock_dashboard:** The paper trading pipeline reads research data FROM `~/stock_dashboard/`. Always run `sync_pipeline_data.sh` before a paper trading session.

---

## Hardware & Model

- **Machine:** Mac Mini M4
- **Model:** `gemma4:12b-it-qat` via Ollama (auto-starts at login via menu bar app)
- **Ollama endpoint:** `http://localhost:11434`
- **Context:** 65536 tokens
- **Timeout:** 420 seconds per call (bumped from 300 to handle longer prompts on 12b)
- **Agent calls:** Direct Python HTTP via `ollama_client.py`

**Verify Ollama is running:**
```bash
curl -s http://localhost:11434/v1/models | python3 -c \
  "import json,sys; d=json.load(sys.stdin); print(f'Running: {[m[\"id\"] for m in d[\"data\"]]}')"
```

**If not running:**
```bash
open /Applications/Ollama.app
# Or check menu bar for Ollama icon
```

Ollama auto-starts at login — no systemd or launchd setup needed.

---

## ollama_client.py

Located at `~/paper_trade/ollama_client.py`. This is the core of the Mac pipeline.

**How it works:**
1. Loads SOUL.md from `~/paper_trade/agents/<profile>/SOUL.md`
2. If SOUL.md not found, uses built-in fallback souls in `FALLBACK_SOULS` dict
3. Calls `http://localhost:11434/api/chat` directly
4. Returns response text

**`call_llm_hermes()` function** provides dual-machine compatibility:
- Tries Hermes `-z` subprocess first (works on Jetson/Shogun)
- Falls back to direct Ollama if Hermes fails or isn't installed
- All Venus pipeline scripts import this automatically

**Standalone smoke test:**
```bash
cd ~/paper_trade
python3 ollama_client.py --profile neptune --prompt "Reply with exactly: NEPTUNE_ONLINE" --verbose
python3 ollama_client.py --profile venus   --prompt "Reply with exactly: VENUS_ONLINE"   --verbose
python3 ollama_client.py --profile orbit   --prompt "Reply with exactly: ORBIT_ONLINE"   --verbose
python3 ollama_client.py --profile fa      --prompt "Reply with exactly: FA_ONLINE"       --verbose
```

**Expected response times on M4 (gemma4:12b):**
- Short prompts (FA, Venus smoke test): 10-15 seconds
- Medium prompts (Neptune trading decision): 50-90 seconds
- Long prompts (Orbit, Venus Week 2 proposals): 60-140 seconds
- Calendar title generation (Week 2): 20-40 seconds

---

## SOUL.md Files

Located at `~/paper_trade/agents/<profile>/SOUL.md`.
**Note:** This replaces the old `~/.hermes/profiles/` path — Hermes is not installed on Mac.

```
~/paper_trade/agents/
├── venus/SOUL.md      # Venus sales identity, no AI disclosure, Jay attends meetings
├── fa/SOUL.md         # FA gatekeeper — realistic skepticism, Jay attends not Venus
├── neptune/SOUL.md    # Risk manager — evaluate orders as submitted, no scope creep
└── orbit/SOUL.md      # Client services — warm tone, top-5 policy, full-close alerts
```

**To update a SOUL.md:**
```bash
# Just edit the file directly — no service restart needed
nano ~/paper_trade/agents/venus/SOUL.md
```

**Key rules in each SOUL (as of June 25 2026):**

*Venus:* Never introduce yourself as an AI. Jay attends meetings, not you. Never say "I have openings on their calendar." Signs off as `— Venus | Obsidian Capital`.

*FA Agent:* Jay attends the meeting, not Venus. Realistic skepticism — busy FAs decline generic pitches. New FAs (book < 30) are more receptive.

*Neptune:* Evaluate orders exactly as submitted. Never reject a valid order because you prefer a larger size — that's Jupiter's call. Size-based rejections only for mechanical limits ($25K min, $250K max).

*Orbit:* Top 5 holdings only. Written auth for address/beneficiary changes. Full-close triggers Venus alert immediately.

---

## Venus 5-Week Pipeline

Venus uses a 5-week FA sales pipeline, all orchestrated through `venus.py --week N`.

```
~/paper_trade/venus/
├── venus.py               # Pipeline runner — single entry point (--week 1-5)
├── venus_week2.py         # Week 2: read FA replies, propose meetings
├── venus_week3.py         # Week 3: FA confirms meeting slot
├── venus_week4.py         # Week 4: simulate meeting, close or no-close
├── venus_week5.py         # Week 5: Venus debrief email to Jay
├── fa_agent.py            # FA gatekeeper (runs between Week 1 and Week 2)
├── fetch_prospects.py     # Prospect fetcher
├── build_venus_dashboard.py
├── venues.json            # Real venues: stadiums, restaurants, outdoor activities
├── crm_advisors.json
├── baseball_schedules.json
├── ocrff_fund.json
├── venus_outbox/          # Emails + Orbit alerts (current cycle)
│   └── archive/           # Previous cycle emails (not shown in dashboard)
├── fa_calendars/          # Persisted FA weekly calendars (one per FA)
├── fa_responses/          # FA agent response records
└── pipeline_reports/      # Week 5 debrief JSON reports
```

**Full pipeline run:**
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
open venus_dashboard.html
```

**Archive old outbox before a new cycle:**
```bash
mkdir -p ~/paper_trade/venus/venus_outbox/archive
mv ~/paper_trade/venus/venus_outbox/email_*.json \
   ~/paper_trade/venus/venus_outbox/archive/
```

**Pipeline week prerequisites:**
- Week 2 requires Week 1 emails + `fa_agent.py --all` to have run
- Weeks 3-5 each require the previous week to be complete
- Scripts enforce this and print clear errors if prerequisites are missing

---

## Key Pipeline Improvements (June 25, 2026)

### Week 1 — Retry logic
If Venus gets an empty or fallback response from the LLM, she retries once automatically. If both attempts fail, the FA is skipped cleanly (no blank email saved to outbox). Blank emails from cold-model startup are no longer a problem.

### Week 2 — Calendar generation
FA calendars are now generated by asking the LLM for **plain-text meeting titles only** — we build the JSON structure ourselves. This eliminates the JSON parse failures that caused the template fallback on every calendar call. The LLM just lists 8 realistic meeting names; Python assigns times and slots.

### Week 2 — Materials request detection
Venus now reads the FA's reply and detects requests for pitch decks or one-pagers before drafting her meeting proposal. If detected, her follow-up acknowledges the materials request and pivots naturally to the meeting ask in one email.

### Week 3 — Slot selection
Meeting slots are now pre-selected randomly in Python before the FA Agent sees the prompt. This eliminates the recency bias where the LLM always picked the last slot offered (always Tuesday). Expect a natural mix of Monday/Tuesday confirmations across the batch.

### Week 4 — No-close variety
The no-close narrative pool now has 7 distinct reasons (approved product list review, competing product redemption lock, branch transition, quant fund skepticism, home office bottleneck, loyalty to existing wholesaler, needs one more quarter). Each FA gets a randomly selected reason — no more every loss citing "compliance review."

### Orbit alerts in Venus dashboard
Full-close alerts written by Orbit to `venus_outbox/` now appear in the Dialogue tab as red warning cards at the top of the list. The scoreboard shows an **Orbit Alerts 🚨** counter. Only current-cycle alerts are shown (archive folder is excluded).

---

## Venus Prospect Strategies

```bash
python3 fetch_prospects.py --strategy mixed    # Default: warm + baseball + new_fa scored
python3 fetch_prospects.py --strategy warm     # Warm leads only
python3 fetch_prospects.py --strategy new_fa   # FAs with <50 clients — more receptive
python3 fetch_prospects.py --strategy baseball # Baseball fans only
python3 fetch_prospects.py --seed 42           # Reproducible selection
```

**Expected conversion rates on Mac (gemma4:12b is more nuanced than e2b):**
- Warm + baseball: 80-100% Week 1 reply rate, 40-65% close rate
- Mixed: 60-80% reply rate, 25-50% close rate
- Cold with hooks: 30-50% reply rate, 15-30% close rate

**Venus email troubleshooting:**
- If you see `⏭️ Skipping [FA] — no usable LLM response after retries` — model was too slow, FA skipped cleanly
- Run with `--verbose` to see raw LLM output
- `⚠️ Empty/fallback response (attempt 1/2) — retrying...` is normal on cold model startup

---

## File Structure

```
~/paper_trade/
├── ollama_client.py              # ⭐ Core Mac component — direct Ollama API
├── agents/                       # ⭐ SOUL.md files (replaces ~/.hermes/profiles/)
│   ├── venus/SOUL.md
│   ├── fa/SOUL.md
│   ├── neptune/SOUL.md
│   └── orbit/SOUL.md
├── neptune.py
├── neptune_config.json
├── neptune_holdings.json
├── neptune_holdings_ORIGINAL.json
├── generate_sample_orders.py
├── update_prices.py
├── sync_pipeline_data.sh
├── build_neptune_dashboard.py
├── neptune_dashboard.html
├── data/
│   ├── research_latest.json
│   ├── mercury_latest.json
│   ├── rankings_latest.json
│   ├── macro_backdrop.yaml
│   └── watchlist.json
├── reports/
├── venus/
│   ├── venus.py
│   ├── venus_week2.py
│   ├── venus_week3.py
│   ├── venus_week4.py
│   ├── venus_week5.py
│   ├── fa_agent.py
│   ├── fetch_prospects.py
│   ├── build_venus_dashboard.py
│   ├── venues.json
│   ├── crm_advisors.json
│   ├── crm_clients.json
│   ├── baseball_schedules.json
│   ├── ocrff_fund.json
│   ├── venus_outbox/
│   │   └── archive/
│   ├── fa_calendars/
│   ├── fa_responses/
│   └── pipeline_reports/
└── orbit/
    ├── orbit.py
    ├── generate_issues.py
    ├── trade_engine.py
    ├── build_orbit_dashboard.py
    ├── orbit_dashboard.html
    ├── orbit_outbox/
    ├── orbit_statements/
    └── neptune_escalations.json
```

---

## OCRFF Fund

| Metric | Value |
|---|---|
| Fund name | Obsidian Capital Research Fund (OCRFF) |
| Inception | January 2020 @ $40.00 NAV |
| Current NAV | $101.39 |
| Shares outstanding | 2,121,794 |
| AUM | ~$215M |
| YTD return | +22.4% vs S&P +12.1% |
| 1-year return | +31.2% vs S&P +19.8% |
| Since inception | +153.5% vs S&P +94.2% |
| Alpha YTD | +10.3% |

---

## Neptune Holdings

**Top positions:**
- IAU (Gold ETF) — ~16% weight — ~$34M (IAU bug fixed: now correctly sourced from yfinance, not mercury copper price)
- BITQ (Crypto ETF) — ~13% weight — ~$28M
- VDE (Energy ETF) — ~8.8% weight — ~$17.7M
- NVDA — ~7.9% weight
- AAPL — ~6.6% weight

**Cash (VMRXX):** tracked in `summary.cash_value` — not as a position entry.
New client AUM from Venus closes is added here automatically by `venus_week4.py`.

---

## Orbit Issue Types

| Issue Type | Weight | What Happens |
|---|---|---|
| buy | 20% | VMRXX increases, client shares added |
| sell | 25% | VMRXX decreases, escalate if insufficient |
| full_close | 5% | Full redemption + Venus alert in outbox |
| statement | 20% | HTML statement in orbit_statements/ |
| address | 15% | Pending verification — no immediate update |
| beneficiary | 10% | Signed form required — pending |
| holdings | 5% | Top 5 disclosed, full list withheld |

```bash
# Force specific issue type for testing
cd ~/paper_trade/orbit
python3 generate_issues.py --force-type full_close --count 1
python3 orbit.py --issues orbit_issues_latest.json --verbose

# Rebuild Venus dashboard to see the alert
cd ~/paper_trade/venus
python3 build_venus_dashboard.py
```

---

## Troubleshooting

**Ollama not responding:**
```bash
curl -s http://localhost:11434/v1/models | head -3
open /Applications/Ollama.app
```

**Venus Week 1 skipping FAs:**
- `⏭️ Skipping [FA] — no usable LLM response after retries` = model too slow or cold
- Model warms up after the first call — subsequent FAs are usually fine
- If persistent: try `gemma4:e4b` (faster 4B model) in `ollama_client.py`

**Calendar generation falling back to template:**
- Now uses simplified plain-text prompt — should rarely fail
- Template fallback still produces correct open slots — pipeline continues normally

**Week 2 not reading FA replies:**
- Ensure `fa_agent.py --all` ran after Week 1 before running `--week 2`
- Check outbox: `ls ~/paper_trade/venus/venus_outbox/email_*.json`

**Orbit alerts not showing in Venus dashboard:**
- Alerts only appear if Orbit ran during the current pipeline session
- Archive folder is excluded — only `venus_outbox/email_*.json` is scanned
- Rebuild dashboard after Orbit runs: `python3 build_venus_dashboard.py`

**Neptune rejecting valid sell orders as "too small":**
- Fixed in Neptune SOUL.md June 25 — scope rules now explicit
- Neptune evaluates orders as submitted; size decisions belong to Jupiter

**IAU price spiking to $13,000+:**
- Fixed in `update_prices.py` June 24 — IAU excluded from Mercury narrative extraction
- IAU now always sourced from yfinance (~$75-80 range)

---

## Model Comparison

| Model | Machine | Speed | Quality | Use for |
|---|---|---|---|---|
| gemma4:e2b Q4_K_M | Jetson | ~12 tok/s | Good | Full pipeline, offline |
| gemma4:12b-it-qat | Mac M4 | ~11 tok/s | Better | Nuanced FA replies, complex reasoning |
| gemma4:e4b | Mac M4 | ~25 tok/s | Good | Fast iteration testing |

**12b vs e2b quality differences:**
- Neptune 12b: more sophisticated rejection rationale, better scope discipline
- FA Agent 12b: more realistic gatekeeper skepticism — asks for materials before meeting
- Venus 12b: stronger personalization, correctly attributes Jay as meeting attendee
- Orbit 12b: better compliance consistency on address/beneficiary policy

---

## Full Pipeline Run (Quick Reference)

```bash
# 1. Restore clean holdings (if needed)
cp ~/paper_trade/neptune_holdings_ORIGINAL.json ~/paper_trade/neptune_holdings.json

# 2. Sync data and update prices
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
open ~/paper_trade/neptune_dashboard.html
open ~/paper_trade/venus/venus_dashboard.html
open ~/paper_trade/orbit/orbit_dashboard.html
```

**Estimated runtime:** ~60-90 minutes for full pipeline with gemma4:12b on M4.

---

*— Obsidian Capital | paper_trade v1.1 Mac Edition | June 25, 2026*
