#!/usr/bin/env bash
# Start the MT5 HTTP bridge server.
# Run from any directory: bash bot/scripts/start_bridge.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOT_DIR="$(dirname "$SCRIPT_DIR")"
VENV="$BOT_DIR/.venv/bin/python"
SERVER="$BOT_DIR/core/bridge/http_server.py"
LOG="$BOT_DIR/../logs/bridge.log"

mkdir -p "$(dirname "$LOG")"

echo "Starting MT5 HTTP bridge..."
echo "  Server : http://0.0.0.0:8080"
echo "  EA URL : http://192.168.64.1:8080"
echo "  Log    : $LOG"
echo ""

exec "$VENV" "$SERVER" 2>&1 | tee "$LOG"
