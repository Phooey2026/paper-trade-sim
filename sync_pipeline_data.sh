#!/bin/bash
# sync_pipeline_data.sh
# Copies the latest pipeline data files into paper_trade/data/ for offline use.
# Run this BEFORE generate_sample_orders.py and neptune.py each week.
# Usage: bash sync_pipeline_data.sh [--host shogun]

PAPER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$PAPER_DIR/data"

# Default stock_dashboard path (Jetson). Override with --host flag.
SD="/home/jay/stock_dashboard"
HOST="jetson"

while [[ "$#" -gt 0 ]]; do
  case $1 in
    --host) HOST="$2"; shift ;;
  esac
  shift
done

if [[ "$HOST" == "shogun" ]]; then
  SD="/home/shogun/stock_dashboard"
fi

echo "================================================"
echo " Obsidian Capital — Neptune Data Sync"
echo " Source : $SD"
echo " Target : $DATA_DIR"
echo " $(date)"
echo "================================================"

mkdir -p "$DATA_DIR"

# ── Latest research JSON (Jupiter stock summaries) ─────────────────────────
RESEARCH=$(ls -t "$SD/data/research_"*.json 2>/dev/null | head -1)
if [[ -n "$RESEARCH" ]]; then
  cp "$RESEARCH" "$DATA_DIR/research_latest.json"
  echo "✅ research_latest.json  ← $(basename $RESEARCH)"
else
  echo "⚠️  No research_*.json found in $SD/data/"
fi

# ── Latest Mercury CCC JSON ────────────────────────────────────────────────
MERCURY=$(ls -t "$SD/data/mercury_"*.json 2>/dev/null | head -1)
if [[ -n "$MERCURY" ]]; then
  cp "$MERCURY" "$DATA_DIR/mercury_latest.json"
  echo "✅ mercury_latest.json   ← $(basename $MERCURY)"
else
  echo "⚠️  No mercury_*.json found in $SD/data/"
fi

# ── Latest sector rankings JSON ────────────────────────────────────────────
RANKINGS=$(ls -t "$SD/data/rankings_"*.json 2>/dev/null | head -1)
if [[ -n "$RANKINGS" ]]; then
  cp "$RANKINGS" "$DATA_DIR/rankings_latest.json"
  echo "✅ rankings_latest.json  ← $(basename $RANKINGS)"
else
  echo "⚠️  No rankings_*.json found in $SD/data/"
fi

# ── Macro backdrop YAML ────────────────────────────────────────────────────
MACRO="$SD/macro_backdrop.yaml"
if [[ -f "$MACRO" ]]; then
  cp "$MACRO" "$DATA_DIR/macro_backdrop.yaml"
  echo "✅ macro_backdrop.yaml"
else
  echo "⚠️  macro_backdrop.yaml not found at $MACRO"
fi

# ── Watchlist JSON ─────────────────────────────────────────────────────────
WATCHLIST="$SD/watchlist.json"
if [[ -f "$WATCHLIST" ]]; then
  cp "$WATCHLIST" "$DATA_DIR/watchlist.json"
  echo "✅ watchlist.json"
else
  echo "⚠️  watchlist.json not found at $WATCHLIST"
fi

echo ""
echo "Sync complete. Files in $DATA_DIR:"
ls -lh "$DATA_DIR/"
