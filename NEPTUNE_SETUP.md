# Neptune Setup Guide
## Obsidian Capital Paper Trading — OC-CLIENT-001

---

## Folder Structure

```
/home/jay/paper_trade/
├── neptune.py                     # Neptune trading agent
├── generate_sample_orders.py      # Simulates Jupiter/Mercury orders
├── update_prices.py               # Refreshes prices from pipeline JSON
├── sync_pipeline_data.sh          # Copies pipeline data into paper_trade/data/
├── start_gemma4_km_8081.sh        # Jetson llama.cpp startup (port 8081)
├── neptune_holdings.json          # Paper portfolio (OC-CLIENT-001)
├── neptune_config.json            # Thresholds and rules
├── NEPTUNE_SETUP.md               # This file
├── data/
│   ├── research_latest.json       # Copied from stock_dashboard/data/
│   ├── mercury_latest.json        # Copied from stock_dashboard/data/
│   ├── rankings_latest.json       # Copied from stock_dashboard/data/
│   ├── macro_backdrop.yaml        # Copied from stock_dashboard/
│   ├── neptune_orders_*.json      # Order files (generated + editable)
│   ├── neptune_trades_*.json      # Executed trade logs
│   └── neptune_snapshot_*.json    # Weekly portfolio snapshots
└── reports/
    └── neptune_report_*.html      # Standalone HTML reports
```

---

## Machine Configurations

### Jetson Orin Nano Super — gemma4:e2b via llama.cpp (CUDA)

**Step 1: Fix the port conflict with SearXNG**

SearXNG occupies port 8080. Use the updated startup script at port 8081:
```bash
cd /home/jay/paper_trade
bash start_gemma4_km_8081.sh
```

The OpenAI-compatible endpoint will be at:
```
http://localhost:8081/v1/chat/completions
```

**Step 2: Create Neptune Hermes profile**
```bash
hermes profile create neptune --clone default
hermes profile set neptune llm_url http://localhost:8081/v1
hermes profile set neptune llm_model local-model
hermes profile set neptune max_tokens 16384
```

Verify the profile:
```bash
hermes profile show neptune
```

**Step 3: Quick smoke test**
```bash
hermes neptune -z "Reply with exactly: NEPTUNE_ONLINE" --skills hermes-cli
```

---

### Mac Mini M4 — gemma4:12b via Ollama

**Step 1: Confirm Ollama is running with gemma4:12b**
```bash
ollama list            # should show gemma4:12b (or similar)
ollama run gemma4:12b  # test interactively, then Ctrl+D to exit
```

Ollama's OpenAI-compatible endpoint:
```
http://localhost:11434/v1/chat/completions
```

**Step 2: Create Neptune Hermes profile**
```bash
hermes profile create neptune --clone default
hermes profile set neptune llm_url http://localhost:11434/v1
hermes profile set neptune llm_model gemma4:12b
hermes profile set neptune max_tokens 16384
```

**Step 3: Smoke test**
```bash
hermes neptune -z "Reply with exactly: NEPTUNE_ONLINE" --skills hermes-cli
```

---

## Weekly Run Order

### Before heading offline (sync pipeline data):
```bash
cd /home/jay/paper_trade
bash sync_pipeline_data.sh
```

### Standard weekly paper trading run:
```bash
# 1. Update all position prices from pipeline data
python3 update_prices.py --verbose

# 2. Generate orders from current sector rankings
python3 generate_sample_orders.py --max-orders 8 --include-sells

# 3. (Optional) Edit the orders file for testing
#    nano data/neptune_orders_YYYYMMDD_HHMM.json

# 4. Run Neptune — review and execute
python3 neptune.py --orders data/neptune_orders_YYYYMMDD_HHMM.json

# 5. View the report
#    open reports/neptune_report_YYYYMMDD_HHMM.html
```

### Dry run (no changes saved):
```bash
python3 neptune.py --orders data/neptune_orders_YYYYMMDD_HHMM.json --dry-run
```

### Skip LLM (test execution logic only):
```bash
python3 neptune.py --orders data/neptune_orders_YYYYMMDD_HHMM.json --skip-llm
```

---

## Testing Neptune's Rejection Logic

Edit an order file to intentionally trigger rejections:

```json
{
  "order_id": "TEST-001",
  "agent": "jupiter",
  "ticker": "HUBG",
  "action": "BUY",
  "dollar_amount": 10000,
  "rationale": "Testing restricted ticker rejection"
}
```

Expected rejections Neptune will catch:
- `HUBG` → RESTRICTED_TICKER (Critical legal risk)
- Dollar amount > $50,000 → ORDER_TOO_LARGE
- Dollar amount < $5,000 → ORDER_TOO_SMALL
- Mercury ordering NVDA → MERCURY_UNAUTHORIZED_TICKER
- Selling a ticker not in portfolio → NO_POSITION
- Buy that drops cash below 5% floor → CASH_FLOOR_BREACH
- Buy that pushes position above 10% of portfolio → CONCENTRATION_LIMIT
- BUY on an AVOID-rated ticker → VERDICT_CONFLICT

---

## neptune_config.json — Key Settings

| Setting | Default | Notes |
|---------|---------|-------|
| `min_order_dollars` | $5,000 | Hard floor |
| `max_order_dollars` | $50,000 | Hard ceiling |
| `max_single_position_pct` | 10% | Position concentration cap |
| `min_cash_floor_pct` | 5% | ~$127K minimum at current portfolio size |
| `max_sector_pct` | 25% | Soft warning (not yet hard-enforced) |
| `reject_buy_on_avoid_verdict` | true | Hard reject ACCUMULATE/AVOID conflicts |
| `restricted_tickers` | ["HUBG"] | Always rejected, any action |
| `mercury_allowed_tickers` | ["BITQ","VDE","IAU"] | Mercury ETF proxy universe |

---

## Offline at Virginia Lakes — July 2026

Both machines work fully offline once `sync_pipeline_data.sh` has been run:

1. **Jetson**: Start llama.cpp with `bash start_gemma4_km_8081.sh`, then run pipeline
2. **Mac Mini**: Ensure Ollama is running (`ollama serve`), then run pipeline

No internet required — all pipeline data is local, all model inference is local.

---

*Neptune watches the gates. The signal is always there.*
*— Obsidian Capital*
