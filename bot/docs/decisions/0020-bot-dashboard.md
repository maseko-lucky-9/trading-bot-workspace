# ADR 0020 — Bot Dashboard form factor and framework

**Status:** Accepted
**Date:** 2026-04-27
**Run ID:** `20260427-bot-dashboard`
**Decider:** Operator (form factor pre-locked) + pipeline orchestrator (internal architecture, auto-mode)

## Context

The MT5 paper-trading bot runs locally and writes artefacts to disk
(`logs/trades.csv`, `bridge_data/history/*.parquet`, `logs/health.jsonl`).
Today there is no at-a-glance monitoring view — the operator runs ad-hoc
shell commands (`bash scripts/daily_health_check.sh`, `tail logs/trades.csv`,
`python scripts/detect_bridge.py`) and reads the launchd JSONL log to
piece together state.

We need a single page that surfaces:

1. Bot process health (running / not_running, PID, uptime).
2. Bridge connectivity (`/ping` reachability + `ea_connected`).
3. Current regime classification.
4. Live drawdown vs peak equity from realised trades.
5. Equity curve + drawdown chart.
6. Sortable trade table with side / symbol filters.
7. Performance tiles: Sharpe, DSR, expectancy, win rate, payoff ratio.

It must be quick to build, contain no operational footprint
(no daemon to manage, no DB), and never interfere with the bot's runtime.

## Decision

Build a single FastAPI app under `bot/dashboard/` with four polling
JSON endpoints and one static HTML page that renders charts via
**Chart.js loaded from CDN**. Bind uvicorn explicitly to `127.0.0.1:8090`.

- Backend: FastAPI (already a project dependency for the MT5 bridge
  server). One process. ~6 source files, ~1100 LOC including tests.
- Frontend: vanilla HTML + JS + Chart.js. **No node/npm build step.**
- Polling at 7 s. **No websockets** (overkill for the cadence of trade events).
- Read-only consumption: process probe via `pgrep`/`ps`, bridge probe via
  `urllib`, trades via `pandas`, regime via the existing `RegimeDetector`.
- Strict CSP locks scripts to `self` + `cdn.jsdelivr.net`.

## Options considered

| Option | Pros | Cons | Why rejected |
|---|---|---|---|
| **A. FastAPI + vanilla HTML + Chart.js (CDN)** | No new pip deps; tiny surface; Chart.js mature | CSP must allow one CDN origin | **Chosen** — simplest path. |
| B. Streamlit | One-file UI, batteries included | New pip dep (~150 MB transitive); auto-reruns the whole page on every input — wasteful for polling; opinionated styling clashes with the operator's terminal-first aesthetic | Violates "no new deps" constraint. |
| C. Textual (terminal TUI) | No browser at all; fits the operator's CLI-heavy workflow | New pip dep; no chart visualisation; harder to share via screenshot | Violates "no new deps"; charts are an explicit requirement. |
| D. FastAPI + WebSocket + React | Real-time push, no polling overhead | Polling at 7 s is sufficient; React requires npm build step (explicitly out of scope); adds reconnection complexity | Violates "no node/npm" constraint. |
| E. Static HTML + JS files served from a `python -m http.server` | Zero framework | No JSON endpoints — would need to write static JSON files on a timer (stateful side-effects on the bot's filesystem) | Violates "no new persistence" constraint. |

## Consequences

### Positive

- **Zero new pip dependencies.** Everything required is already in `requirements.txt`.
- **Bot-runtime-isolated.** No edits to `main.py` / `core/execution` / `core/risk` / `autoresearch`.
- **Graceful degradation by construction.** Each adapter returns a typed envelope; routes never raise.
- **Mockable.** All I/O is at module boundaries — `subprocess`, `urllib.request.urlopen`, `pd.read_csv`, `pd.read_parquet` — easily monkeypatched in offline tests.
- **Cold-start under 2 s.** No DB connection, no model loading, no cache warm-up. uvicorn boot + first poll is sub-second on the operator's M-series MacBook.

### Negative

- **macOS-only process probe.** `pgrep -f` + `ps -p <pid> -o comm=` follows BSD semantics. The bot only runs on the operator's macOS laptop, so this is acceptable; if we ever ship to Linux the probe needs a `/proc/<pid>/cmdline` fallback.
- **Polling, not push.** A trade closing won't appear until the next 7-s tick. Acceptable — paper trading is not latency-sensitive on the dashboard side.
- **CSP allows one CDN origin.** `https://cdn.jsdelivr.net` is pinned to a specific Chart.js version with SRI; an upstream takeover would still require breaking SRI.
- **DSR computed locally.** `core/performance/tracker.py` does not expose DSR, so we add a small Bailey/López de Prado helper inside `dashboard/sources.py`. Code duplication is one ~15-line function — acceptable to avoid touching `tracker.py` (per the "no edits to core" constraint).

### Operational

- Start: `bash scripts/start_dashboard.sh` or `python -m dashboard` from the `bot/` root.
- Stop: `Ctrl-C` (no daemon).
- Logs: uvicorn stdout (no file logging — the dashboard is a foreground tool).
- No launchd plist in this run; the operator can request one via a follow-up if the dashboard becomes a daily habit.

## Verification

- `pytest -q tests/dashboard` — all new tests offline, ≥ 12 tests.
- Existing test count (551 baseline) unchanged.
- Manual smoke matrix: bot up + bridge up; bot killed; bridge stopped; parquet missing.
- AC verification recorded in `pipeline/build-summary.md`.

## Related

- `pipeline/intake-validation.md` — Run 20260427 intake.
- `pipeline/design-brief.md` — Run 20260427 design brief (decisions A1–A12).
- `pipeline/plan.md` — Run 20260427 plan (tasks T1–T7).
- `dashboard/README.md` — Operator-facing usage docs.
