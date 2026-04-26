# Phase 0: Intake Validation

**Run ID:** 20260426-h1backfill
**Date:** 2026-04-26 (Sunday — forex market closed until ~Sun 17:00 ET)
**Project root:** `/Users/ltmas/trading-bot-workspace/bot`
**Status:** VALIDATED
**Note:** This run supersedes the prior PositionMonitor intake (archived in git history).

---

## Normalized Requirement

### Problem statement
The MT5 trading bot's backtest engine (`backtest/engine.py`) requires ≥ `WARN_BARS=4176` real H1 bars per symbol to produce statistically valid Sharpe / drawdown numbers. Current cached parquet files in `bridge_data/history/` contain only 200–500 real bars, so 90–96% of recent 5,000-bar backtest runs are filled with seeded synthetic random walk. This makes results directionally informative only — not trade-worthy.

### Goal
A reusable backfill module + script that pulls ≥ 5,000 real H1 OHLCV bars per symbol via the MT5 bridge and writes them to `bridge_data/history/<SYMBOL>_H1.parquet` using the existing schema, idempotently.

### In scope
1. Connect to running MT5 bridge (existing client in `core/bridge/`; likely `MT5BridgeClient`).
2. Pull ≥ 5,000 H1 bars per symbol for the symbol set finalized in design gate.
3. Upsert into `bridge_data/history/<SYMBOL>_H1.parquet`, preserving existing schema.
4. Idempotent merge keyed on timestamp; no duplicate rows.
5. Runnable as `python -m scripts.backfill_history` or `bash scripts/backfill_history.sh` (follow project convention; `scripts/detect_bridge.py` precedent suggests a Python module is fine).
6. Progress logging: bars fetched per symbol, bars skipped (already cached), elapsed time.
7. Pytest unit tests with mocked bridge — verify schema preservation, idempotent merge, dedup, partial-fetch resumption.
8. Graceful failure when bridge is unreachable (clear error; never silent fallback to synthetic).

### Acceptance criteria
- **AC1**: After successful run, each target parquet contains ≥ 5,000 real H1 bars (verified by row count).
- **AC2**: Re-running the script with no new bridge data produces zero new rows (idempotency).
- **AC3**: Schema (columns + dtypes + tz-aware UTC timestamp) of resulting parquet matches the existing parquet — verified by reading existing file first and comparing.
- **AC4**: All new pytest tests pass; the existing 324-test suite remains green.
- **AC5**: Bridge-down scenario raises a clear `BridgeUnavailableError` (or equivalent) with actionable message; no synthetic fallback.
- **AC6**: Progress is logged per symbol with bar counts and elapsed time.
- **AC7**: Partial-fetch resumption — if a previous run got 3,000 of 5,000 bars and was killed, next run continues from the last cached timestamp without re-fetching.

### Explicit non-goals
- Other timeframes (M5, M15, D1) — H1 only.
- Live streaming / incremental tail updates — separate feature.
- Replacing the parquet cache loader in `backtest/engine.py` — we only feed it more data.
- Any change to the backtest engine itself.

---

## Constraints / Gotchas

| # | Constraint | Mitigation |
|---|-----------|------------|
| C1 | Today is Sunday 2026-04-26 — forex market closed | Design + tests must complete without live bridge. User runs live execution post-market-open. |
| C2 | MT5 bridge may be down | Script must fail gracefully with a clear, actionable error. No synthetic fallback. |
| C3 | Parquet schema must match existing | Read an existing parquet first; assert dtype/column equality before writing. |
| C4 | Repo at `/Users/ltmas/trading-bot-workspace/bot` (NEVER `/Repo 2`) | Confirmed; primary repo. |
| C5 | Existing pytest suite has 324 green tests | Final test run must remain fully green; no flake regressions. |
| C6 | `config.yaml` lists ONLY `USDJPY` under `bot.instruments` | Symbol set discrepancy — surfaced as Design Gate Decision #1 below. |

---

## Open Items for Design Gate (Phase 0.5)

These are NOT blockers — they need user sign-off in the design phase:

1. **Symbol set discrepancy.** `config.yaml` currently lists only `USDJPY` under `bot.instruments`, but the requirement and existing parquet files name EURUSD, GBPUSD, USDJPY. Need to confirm the canonical source.
2. **Lookback strategy.** Fixed bar count vs. fixed date range vs. "fill until N bars cached".
3. **Merge semantics on overlap.** Prefer-new vs. prefer-existing vs. error-on-conflict.
4. **Bridge connection pattern.** Reuse existing `MT5BridgeClient` vs. introduce dedicated `HistoricalDataClient` (likely needed if MT5 has per-request bar caps requiring pagination).

---

## Scope Decomposition Check

- Single bounded context (data ingestion / backfill).
- Single subsystem (the bot's `bridge_data` cache layer).
- Estimated task count: 6–10. Below the 20-task threshold.
- **Verdict:** Scope appropriately sized for one pipeline run. No `scope_warning` raised.

---

## Dual-Client Detection

- No web client. No mobile client. Single-target Python script + module.
- **`dual_client: false`** — single-client inner loop in Phase 4.

---

## Files Verified (Read this session)

- `/Users/ltmas/trading-bot-workspace/bot/config.yaml` — bridge URL, instrument list, timeframe.
- `/Users/ltmas/trading-bot-workspace/bot/scripts/detect_bridge.py` — script style precedent.
- `/Users/ltmas/trading-bot-workspace/bot/scripts/start_bridge.sh` — shell wrapper precedent.

Files NOT yet verified (deferred to Phase 2 — Context Architect):
- `core/bridge/` — actual bridge client interface (assumed `MT5BridgeClient`).
- `bridge_data/history/EURUSD_H1.parquet` — actual schema (will be confirmed in Phase 1 plan and verified in Phase 2).
- `backtest/engine.py` — `WARN_BARS` constant and parquet loader pattern.

---

## Validation Verdict

**PASS.** Requirement is parseable, complete, has clear acceptance criteria, and bounded scope. Proceeding to Phase 0.5 (Design Gate) — no pre-existing spec linked, so inline Socratic refinement is required (HARD-GATE).
