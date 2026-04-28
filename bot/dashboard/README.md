# MT5 Bot Dashboard

A single-process FastAPI app that serves a read-only HTML page on
`http://127.0.0.1:8090/` summarising the running paper-trading bot.

- **Read-only.** Never writes to the bot's runtime files.
- **Local-only.** Binds to `127.0.0.1` — not the LAN.
- **No new dependencies.** Reuses fastapi/uvicorn/pandas/pyyaml from `requirements.txt`.

## Start

```bash
# Easiest — wraps the venv and exec's `python -m dashboard`
bash scripts/start_dashboard.sh

# Direct (cwd must be bot/ root)
cd /Users/ltmas/trading-bot-workspace/bot
python -m dashboard
```

Then open <http://127.0.0.1:8090/>. The page polls four JSON endpoints
every 7 seconds via `Promise.allSettled` so any single failing endpoint
leaves the other panes intact.

Override the port with `DASHBOARD_PORT=9000 python -m dashboard`.

## Files it reads

| Source | Used by | Read pattern |
|---|---|---|
| `config.yaml` | every endpoint | `yaml.safe_load`, no caching |
| `logs/trades.csv` | health / equity / trades / metrics | `pd.read_csv`, `usecols`, `on_bad_lines="skip"` |
| `<config.bridge.base_url>/ping` | health | `urllib.request.urlopen`, 3 s timeout |
| `pgrep -f 'main\.py.*--mode paper'` + `ps -p $pid -o comm=` | health | mirrors `scripts/daily_health_check.sh:42–48` |
| `bridge_data/history/<SYMBOL>_<TF>.parquet` | health (regime) | `pd.read_parquet`, last 200 bars, **never written** |

It does **not** touch:

- `main.py`, `core/execution/*`, `core/risk/*`, `autoresearch/*` (the bot's hot path).
- `logs/positions.jsonl`, `logs/health.jsonl` (kept for the launchd job).
- Anything under `bridge_data/history/` for writing.

## API

All endpoints return JSON with an explicit `status` field. Any failure
(bridge down, bot killed, parquet missing) returns `status: "unavailable"`
with an HTTP 200 — routes never 500.

| Endpoint | Description |
|---|---|
| `GET /` | The dashboard HTML |
| `GET /api/health` | `{ process, bridge, regime, circuit_breaker }` |
| `GET /api/equity?limit=N` | `{ timestamps, equity, peak, drawdown, current_drawdown, peak_equity }` |
| `GET /api/trades?limit=N&side=BUY\|SELL\|ALL&symbol=EURUSD` | `{ count, rows[] }` |
| `GET /api/metrics` | `{ sharpe, dsr, expectancy, win_rate, payoff_ratio, trade_count, ... }` |

## Degraded states (expected behaviour)

| Condition | Health pane shows | Other panes |
|---|---|---|
| Bot killed | `process: not_running` (red) | Render last-known data from `trades.csv` |
| Bridge stopped | `bridge: unreachable` (red) | Equity / trades / metrics still render |
| EA disconnected | `bridge: ok`, `EA: no` (yellow) | Unaffected |
| Parquet missing | `regime: unknown` (yellow) | Unaffected |
| `trades.csv` missing | All four panes empty / zeroed | No 500 |

## Security

- Hard-coded bind to `127.0.0.1`. Never `0.0.0.0`.
- Strict CSP: `default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; ...`
- No CORS middleware (same-origin only).
- No auth — the loopback binding is the security boundary.

## Tests

```bash
cd /Users/ltmas/trading-bot-workspace/bot
python -m pytest -q tests/dashboard
```

All tests are offline — `subprocess.run`, `urllib.request.urlopen`, and
all file reads are monkeypatched. No live network calls in CI.

## Reusable artefacts referenced

- `scripts/daily_health_check.sh` — pgrep + ps comm filter pattern
- `scripts/detect_bridge.py` — `/ping` probe pattern
- `core/performance/tracker.py` — Sharpe / expectancy / win rate / payoff
- `core/regime/detector.py` — regime classification

The DSR (Deflated Sharpe Ratio, Bailey & López de Prado) is computed
locally in `dashboard/sources.py::_compute_dsr` because no existing
module in the bot exposes it.

## See also

- ADR `docs/decisions/0020-bot-dashboard.md` — form-factor + framework rationale.
- `pipeline/build-summary.md` — full build report for run `20260427-bot-dashboard`.
