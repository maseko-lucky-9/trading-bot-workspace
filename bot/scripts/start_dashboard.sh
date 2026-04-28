#!/usr/bin/env bash
# Start the local read-only MT5 bot dashboard.
# Run from any directory: bash bot/scripts/start_dashboard.sh
#
# Binds to 127.0.0.1:8090 only — never exposed to the LAN.
# Override the port with DASHBOARD_PORT=9000 bash scripts/start_dashboard.sh.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOT_DIR="$(dirname "$SCRIPT_DIR")"
VENV="$BOT_DIR/.venv/bin/python"

PORT="${DASHBOARD_PORT:-8090}"

echo "Starting MT5 Bot Dashboard..."
echo "  URL : http://127.0.0.1:$PORT/"
echo "  CWD : $BOT_DIR"
echo ""

cd "$BOT_DIR"
exec env DASHBOARD_PORT="$PORT" "$VENV" -m dashboard "$@"
