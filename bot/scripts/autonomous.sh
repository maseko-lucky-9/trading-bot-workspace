#!/usr/bin/env bash
# Full autonomous mode: bot + autoresearch cycle, paper mode only.
# Runs until killed. Logs to logs/autonomous.log.
set -euo pipefail

BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$BOT_DIR/.venv/bin/python"
LOG="$BOT_DIR/../logs/autonomous.log"
TRADES="$BOT_DIR/logs/trades.csv"

mkdir -p "$BOT_DIR/../logs" "$BOT_DIR/logs"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG"; }

log "=== AUTONOMOUS MODE START (paper) ==="
log "Bot dir : $BOT_DIR"
log "Log     : $LOG"

ITERATION=0
BOT_CYCLE_SECS=300      # run bot for 5 min per cycle
AUTORESEARCH_EVERY=3    # run autoresearch every 3 bot cycles

while true; do
    ITERATION=$((ITERATION + 1))
    log "--- Cycle $ITERATION ---"

    # Start bot in paper mode for one cycle
    log "Starting bot (paper, ${BOT_CYCLE_SECS}s)..."
    cd "$BOT_DIR"
    PYTHONUNBUFFERED=1 "$PYTHON" -u main.py --mode paper \
        --max-seconds "$BOT_CYCLE_SECS" \
        --resume >> "$LOG" 2>&1 || true

    # Count closed trades
    TRADES_COUNT=0
    if [[ -f "$TRADES" ]]; then
        TRADES_COUNT=$(( $(wc -l < "$TRADES") - 1 ))
    fi
    log "Closed trades so far: $TRADES_COUNT"

    # Run autoresearch every N cycles, or when we have enough trades
    if (( ITERATION % AUTORESEARCH_EVERY == 0 )) || (( TRADES_COUNT >= 30 )); then
        log "Running autoresearch (50 iterations)..."
        "$PYTHON" -c "
import sys; sys.path.insert(0, '.')
from autoresearch.loop import AutoresearchLoop
from pathlib import Path
loop = AutoresearchLoop(
    config_path=Path('config.yaml'),
    params_path=Path('autoresearch/params.yaml'),
    results_path=Path('autoresearch/results.tsv')
)
r = loop.run(max_iterations=50)
print(f'autoresearch: sharpe={r[\"final_sharpe\"]:.4f} iterations={r[\"iterations\"]} decision={r[\"decision\"]}')
" 2>&1 | tee -a "$LOG" || true
    fi

    # Performance snapshot
    log "Performance snapshot:"
    "$PYTHON" -c "
import sys; sys.path.insert(0, '.')
from core.checkpoint.state import CheckpointManager
s = CheckpointManager().load()
if s and s.performance_summary:
    for k,v in s.performance_summary.items():
        print(f'  {k}: {v}')
else:
    print('  no trades yet')
" 2>&1 | tee -a "$LOG" || true

    log "Cycle $ITERATION complete. Restarting in 5s..."
    sleep 5
done
