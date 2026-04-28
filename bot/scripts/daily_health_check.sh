#!/usr/bin/env bash
# Daily health check for the MT5 paper-trading bot.
#
# - Pings the bridge (/ping) and reads ea_connected
# - Confirms `python ... main.py --mode paper` is running
# - Counts rows in logs/trades.csv and computes delta vs last run
# - Appends a JSONL record to logs/health.jsonl
# - Updates logs/health-state.json
# - Prints a one-line human summary on stdout
#
# Exit codes:
#   0  healthy
#   1  warning (e.g. ea_disconnected with bridge reachable)
#   2  bridge unreachable
#   3  bot process not running

set -uo pipefail

BOT_ROOT="/Users/ltmas/trading-bot-workspace/bot"
HEALTH_LOG="$BOT_ROOT/logs/health.jsonl"
TRADES_CSV="$BOT_ROOT/logs/trades.csv"
STATE_FILE="$BOT_ROOT/logs/health-state.json"
BRIDGE_URL="http://192.168.64.1:8080/ping"

mkdir -p "$BOT_ROOT/logs"

ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# --- Bridge ping ---
ping_resp="$(curl -sS -m 5 "$BRIDGE_URL" 2>/dev/null || true)"
if [[ -z "$ping_resp" ]]; then
  bridge_status="unreachable"
  ea_connected="false"
else
  bridge_status="ok"
  ea_connected="$(printf '%s' "$ping_resp" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print('true' if d.get('ea_connected') else 'false')" 2>/dev/null || echo "false")"
fi

# --- Bot process (filter to python only; pgrep -f also matches shell wrappers) ---
bot_pid=""
for candidate in $(pgrep -f 'main\.py.*--mode paper' 2>/dev/null); do
  comm="$(ps -p "$candidate" -o comm= 2>/dev/null)"
  if [[ "$comm" == *python* ]]; then
    bot_pid="$candidate"
    break
  fi
done
if [[ -n "$bot_pid" ]]; then
  bot_status="running"
  bot_etime="$(ps -p "$bot_pid" -o etime= 2>/dev/null | tr -d ' ' || echo '')"
else
  bot_status="not_running"
  bot_etime=""
fi

# --- Trade count + delta ---
if [[ -f "$TRADES_CSV" ]]; then
  raw_lines="$(wc -l < "$TRADES_CSV" | tr -d ' ')"
  trade_count=$(( raw_lines > 0 ? raw_lines - 1 : 0 ))   # subtract header row
else
  trade_count=0
fi

prev_count=0
if [[ -f "$STATE_FILE" ]]; then
  prev_count="$(python3 -c "import json; print(json.load(open('$STATE_FILE')).get('trade_count', 0))" 2>/dev/null || echo 0)"
fi
delta=$(( trade_count - prev_count ))

# --- Alert assembly ---
alerts=()
[[ "$bridge_status" == "unreachable" ]] && alerts+=("bridge_unreachable")
[[ "$ea_connected" != "true" ]] && alerts+=("ea_disconnected")
[[ "$bot_status" == "not_running" ]] && alerts+=("bot_not_running")

alert_csv=""
if (( ${#alerts[@]} > 0 )); then
  alert_csv="$(IFS=,; echo "${alerts[*]}")"
fi

# --- JSONL record (env vars → python, no shell interpolation in heredoc) ---
TS="$ts" \
BRIDGE_STATUS="$bridge_status" \
EA_CONNECTED="$ea_connected" \
BOT_STATUS="$bot_status" \
BOT_PID="$bot_pid" \
BOT_ETIME="$bot_etime" \
TRADES_TOTAL="$trade_count" \
TRADES_DELTA="$delta" \
ALERT_CSV="$alert_csv" \
python3 - <<'PYEOF' >> "$HEALTH_LOG"
import json, os
alerts = [a for a in os.environ.get("ALERT_CSV", "").split(",") if a]
print(json.dumps({
    "ts": os.environ["TS"],
    "bridge": os.environ["BRIDGE_STATUS"],
    "ea_connected": os.environ["EA_CONNECTED"] == "true",
    "bot": os.environ["BOT_STATUS"],
    "bot_pid": os.environ["BOT_PID"] or None,
    "bot_etime": os.environ["BOT_ETIME"] or None,
    "trades_total": int(os.environ["TRADES_TOTAL"]),
    "trades_delta": int(os.environ["TRADES_DELTA"]),
    "alerts": alerts,
}))
PYEOF

# --- State file update ---
TRADES_TOTAL="$trade_count" TS="$ts" python3 - <<'PYEOF' > "$STATE_FILE"
import json, os
print(json.dumps({"trade_count": int(os.environ["TRADES_TOTAL"]), "last_check": os.environ["TS"]}))
PYEOF

# --- Human summary ---
echo "[$ts] bridge=$bridge_status ea=$ea_connected bot=$bot_status trades=$trade_count (+$delta) alerts=${alert_csv:-none}"

# --- macOS notification on any alert (no-op if osascript missing or under launchd without GUI) ---
if (( ${#alerts[@]} > 0 )) && command -v osascript >/dev/null 2>&1; then
  title="MT5 bot health: ${alert_csv}"
  subtitle="trades=$trade_count (+$delta)"
  body="bridge=$bridge_status ea=$ea_connected bot=$bot_status"
  osascript -e "display notification \"$body\" with title \"$title\" subtitle \"$subtitle\" sound name \"Basso\"" >/dev/null 2>&1 || true
fi

# --- Exit code precedence: bridge > bot > warning > healthy ---
if [[ " ${alerts[*]:-} " == *" bridge_unreachable "* ]]; then exit 2; fi
if [[ " ${alerts[*]:-} " == *" bot_not_running "* ]]; then exit 3; fi
if (( ${#alerts[@]} > 0 )); then exit 1; fi
exit 0
