# Phase 0 — Intake Validation

**Run ID:** `20260427-bot-dashboard`
**Date:** 2026-04-27
**Source:** User requirement (verbatim, see prompt)
**Previous run archived to:** `pipeline/state-20260426-h1backfill.json`

## Normalised requirement

Build a **read-only local web dashboard** for the running MT5 paper-trading bot that surfaces four panes (bot health, equity curve + drawdown, trade table, performance metrics) on `127.0.0.1:8090` via FastAPI + a single HTML page polling JSON endpoints. No bot-runtime changes, no new deps, no auth, no LAN exposure.

## Acceptance criteria (lifted, not paraphrased)

1. `python -m dashboard` starts a server on 127.0.0.1:8090.
2. Page renders all four views with real data from the running bot.
3. Bot killed → reload → health pane flips to `not_running`; other panes still render last-known data.
4. Bridge stopped → reload → health pane flips to `bridge unreachable`; no 500s.
5. `pytest -q` reports ≥ 551 + N passing, 0 failed.
6. `python scripts/detect_bridge.py` and `python main.py --mode paper` continue unchanged.
7. `dashboard/README.md` documents start command, URL, and source files.

## Hard constraints (lifted)

- No edits to `main.py`, `core/execution/*`, `core/risk/*`, `autoresearch/*`.
- No new pip deps. FastAPI/uvicorn/pandas/pyyaml are already in `requirements.txt` (verified Phase 2).
- No CSP/CORS that allows non-localhost origins.
- Tests offline — mock bridge `/ping`, mock `trades.csv`, mock pgrep.
- Existing 551 tests stay green.
- No writes to `bridge_data/history/*.parquet`.

## Out of scope (lifted)

- Auth, TLS, LAN exposure, persistence beyond reads, websockets, npm/node, containerisation, ArgoCD/k8s, secrets management.

## Scope decomposition check

- Single bounded context (read-only consumer of existing artefacts).
- Estimated task count: 6–9 (single-file backend, single HTML page, tests for each endpoint, README + ADR).
- `scope_warning: false`
- `dual_client: false` (no mobile target).

## Schema observation (correction to requirement text)

The requirement quoted the trades.csv schema as `ticket,symbol,side,volume,entry,exit,pnl,sl,tp`. **Actual** schema (verified by reading `logs/trades.csv:1`):

```
ticket,symbol,type,volume,open_price,open_time,close_price,close_time,profit,sl,tp
```

`type` (not `side`), `open_price/close_price` (not `entry/exit`), `profit` (not `pnl`). This affects the trade-table column mapping but not the form-factor decisions. Logged here as the canonical schema for downstream phases.

## Phase folding decision (per user instruction)

- Phase 1.5 (Plan Review) **rolled into Phase 1** — small surface, single-file feature.
- Phase 2 (Context) **rolled into Phase 1** — primary files already enumerated in the requirement; verified during this Phase 0 read.
- Phase 3 (Pre-Flight) **rolled into Phase 4 baseline check** — capture `pytest -q` baseline before T1 implementation.
- Phase 0.5 (ADR) **NOT skipped** — user instruction explicitly preserves the design gate.
- Phase 5 (Self-Review) **NOT skipped** — user instruction explicitly preserves it.

## Verified primary files (Read this session)

- `/Users/ltmas/trading-bot-workspace/bot/scripts/daily_health_check.sh` — pgrep filter pattern `main\.py.*--mode paper` + `comm=python` ps filter (lines 42–48).
- `/Users/ltmas/trading-bot-workspace/bot/scripts/detect_bridge.py` — bridge `/ping` probe via `urllib`, reads `config.yaml` `bridge.base_url` (lines 25–48).
- `/Users/ltmas/trading-bot-workspace/bot/config.yaml:2` — `bridge.base_url: "http://192.168.64.1:8080"`.
- `/Users/ltmas/trading-bot-workspace/bot/core/performance/tracker.py` — `PerformanceTracker.summary()` returns sharpe, max_drawdown, win_rate, profit_factor, payoff_ratio, expectancy, avg_r_multiple (line 147–157). **Does NOT compute DSR** — dashboard must add a small DSR helper (Bailey/López de Prado).
- `/Users/ltmas/trading-bot-workspace/bot/core/regime/detector.py:38` — `RegimeDetector.current_regime(df)` returns int regime from a bar DataFrame.
- `/Users/ltmas/trading-bot-workspace/bot/core/risk/manager.py:162` — `RiskManager.check_circuit_breakers()` returns `(ok, reason)` from account/peak inputs. The dashboard will compute current drawdown vs peak from `trades.csv`-derived equity directly (best-effort, per the requirement) since live `account` snapshots aren't persisted.
- `/Users/ltmas/trading-bot-workspace/bot/requirements.txt` — fastapi>=0.110, uvicorn[standard]>=0.29, pandas>=2.0, pyyaml>=6.0 already present.
- `/Users/ltmas/trading-bot-workspace/bot/logs/trades.csv:1` — schema `ticket,symbol,type,volume,open_price,open_time,close_price,close_time,profit,sl,tp`.

## Gate decision

**PASS.** Requirement is parseable, scope is single-context, AC are testable, all primary files exist, no MCP-dependent paths, no destructive operations.
