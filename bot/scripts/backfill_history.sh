#!/usr/bin/env bash
# Top up bridge_data/history/<SYMBOL>_H1.parquet from the running MT5 bridge.
# Run from any directory:  bash bot/scripts/backfill_history.sh [args]
#
# All args forward to scripts.backfill_history. See --help for options:
#     bash bot/scripts/backfill_history.sh --help
#
# Common invocations:
#     bash bot/scripts/backfill_history.sh
#         (default: target=5000, symbols from config.yaml)
#     bash bot/scripts/backfill_history.sh --symbols EURUSD,GBPUSD,USDJPY
#     bash bot/scripts/backfill_history.sh --target 7500 --log-level DEBUG

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOT_DIR="$(dirname "$SCRIPT_DIR")"
VENV_PY="$BOT_DIR/.venv/bin/python"
LOG_DIR="$BOT_DIR/../logs"

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/backfill_history.log"

if [[ ! -x "$VENV_PY" ]]; then
    echo "ERROR: venv python not found at $VENV_PY" >&2
    exit 2
fi

cd "$BOT_DIR"

echo "Backfilling H1 history..."
echo "  Bot dir : $BOT_DIR"
echo "  Log     : $LOG_FILE"
echo ""

exec "$VENV_PY" -m scripts.backfill_history "$@" 2>&1 | tee -a "$LOG_FILE"
