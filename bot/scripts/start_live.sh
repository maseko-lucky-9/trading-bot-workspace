#!/usr/bin/env bash
# start_live.sh — wait for EA account data, then launch bot in live mode.
# Usage: bash scripts/start_live.sh [--max-seconds N]
#
# Exits 1 if account data doesn't appear within 120s.

set -euo pipefail
BRIDGE="http://localhost:8080"
MAX_WAIT=120
MAX_SECONDS="${1:-300}"   # default 5-minute live window; override with first arg
BOT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Live launch sequence ==="
echo "Waiting for EA account data (up to ${MAX_WAIT}s)..."

elapsed=0
while true; do
    acct_keys=$(curl -sf "${BRIDGE}/state" 2>/dev/null \
        | python3 -c "import sys,json; d=json.load(sys.stdin); a=d.get('account',{}); print(len(a))" 2>/dev/null || echo "0")
    equity=$(curl -sf "${BRIDGE}/state" 2>/dev/null \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('account',{}).get('equity',''))" 2>/dev/null || true)

    if [[ "$acct_keys" -gt 0 ]]; then
        echo "Account confirmed: equity=${equity} (min_equity=0.0 set in config)"
        break
    fi

    if [[ $elapsed -ge $MAX_WAIT ]]; then
        echo "ERROR: account data not received after ${MAX_WAIT}s." >&2
        echo "  → Redeploy PythonBridgeHTTP.mq5 on the VM and retry." >&2
        exit 1
    fi

    printf "  waiting... %ds (account fields=%s)\n" "$elapsed" "$acct_keys"
    sleep 5
    elapsed=$((elapsed + 5))
done

echo ""
echo "Bridge:    $(curl -sf "${BRIDGE}/ping" | python3 -c "import sys,json; d=json.load(sys.stdin); print('ea_connected=' + str(d['ea_connected']))")"
echo "Mode:      live"
echo "Max run:   ${MAX_SECONDS}s"
echo ""
echo "Starting bot..."
cd "$BOT_ROOT"
exec python3 main.py --mode live --confirm-live --max-seconds "$MAX_SECONDS"
