# Phase 1 — Implementation Plan

**Run ID:** 20260426-h1backfill
**Source:** `pipeline/design-brief.md` (decisions 1-5 locked)
**Date:** 2026-04-26

---

## Critical context discovery (recon results)

| Finding | Implication |
|---|---|
| `MT5BridgeClient.get_history(symbol, timeframe, bars)` accepts ONLY a `bars` count — no `from_time`/`to_time`/`offset` | True backward-walking pagination is impossible without bridge endpoint changes (out of scope). Single-request fetch with `bars=target` is the only path. |
| `MT5BridgeClient` already uses `tenacity` retries (3 attempts, 1s wait, retries on httpx ConnectError/ReadTimeout/HTTPError) at the transport layer | `HistoricalDataClient` does NOT need to re-implement transport retries — already covered. It owns *application-level* concerns: explicit BridgeUnavailableError, schema coercion, dedup-on-fetch. |
| `BridgeDisconnected` already exists in `core/bridge/http_client.py` | Reuse rather than introduce a parallel exception. New code raises a `BridgeUnavailableError` that subclasses it for clarity in CLI exit messages. |
| Existing `HistoryFetcher` silently falls back to synthetic data | New code MUST NOT fall back. Raises `BridgeUnavailableError` on bridge failure or empty response. |
| Parquet schema confirmed: `time: datetime64[ms, UTC]`, `open/high/low/close: float64`, `volume: int64`, sorted ascending by `time`, no index | Lock this exact schema in `history_store`; assert before write. |
| `core/data/` already exists (contains `feed.py`, `history.py`) | New modules land here: `historical_client.py`, `history_store.py`. |
| Config: `bot.instruments: [USDJPY]`, `bot.timeframe: H1`, `bridge.base_url: http://192.168.64.1:8080` | CLI default reads `bot.instruments`; user passes `--symbols EURUSD,GBPUSD,USDJPY` for the wider triple. |
| Test count: 324 passing | Final run target: ≥ 324 + new tests, all green. |

---

## Revised approach (vs. design brief)

The design brief's "pagination loop walking backwards" is degenerate today — bridge endpoint doesn't support it. The wrapper still earns its keep:

- **Owns** schema coercion (raw dict list → typed DataFrame matching parquet schema).
- **Owns** explicit failure semantics (no silent synthetic fallback).
- **Owns** dedup-on-fetch (sort + drop dup timestamps inside the fetched batch).
- **Defers** transport retry/backoff to `MT5BridgeClient` (already there).
- **Sized** request as `target - already_cached` (top-up semantics — Decision 2).

Pagination becomes a no-op today; the seam exists for future bridge endpoint upgrades.

---

## Task breakdown

| ID | Task | New files | Tests |
|---|---|---|---|
| **T1** | `core/data/history_store.py` — read existing parquet, merge prefer-existing, atomic write, schema assertion | `core/data/history_store.py` | `tests/test_history_store.py`: schema preservation, prefer-existing on conflict, atomic write (.tmp → rename), dedup |
| **T2** | `core/data/historical_client.py` — `HistoricalDataClient` wrapper + `BridgeUnavailableError` | `core/data/historical_client.py` | `tests/test_historical_client.py`: bridge-down raises, empty response raises, schema coercion, dedup-on-fetch |
| **T3** | `scripts/backfill_history.py` — argparse CLI, top-up logic, per-symbol logging, exit codes | `scripts/backfill_history.py` | `tests/test_backfill_history.py`: noop when target met, fetches gap when under, multi-symbol loop, exit code on bridge fail |
| **T4** | `scripts/backfill_history.sh` — venv-activate + forward args (mirrors `start_bridge.sh`) | `scripts/backfill_history.sh` | manual smoke (no test — too thin) |

**Estimated lines:** ~120 (T1) + ~100 (T2) + ~120 (T3) + ~10 (T4) = ~350 LOC + ~250 LOC tests.

---

## TDD order per task

1. Write failing test(s) describing behaviour.
2. Implement minimum to pass.
3. Run targeted test, confirm green.
4. Move to next task.

After all tasks: full suite run.

---

## Acceptance criteria mapping

| AC | Task |
|---|---|
| AC1 (≥5000 bars after run) | T3 — top-up logic |
| AC2 (idempotent) | T1 (prefer-existing merge) + T3 (noop when target met) |
| AC3 (schema match) | T1 (assert schema) + T2 (coerce dtypes) |
| AC4 (tests green) | All tasks |
| AC5 (clear error on bridge down) | T2 (`BridgeUnavailableError`) + T3 (CLI exit non-zero) |
| AC6 (progress logging) | T3 (per-symbol logger) |
| AC7 (partial-fetch resumption) | T1 (read existing first) + T3 (gap calc) |

---

## Out of scope (re-confirmed)

- Modifying bridge `/history` endpoint to accept date range
- Changing `HistoryFetcher` (existing) — leave it for the synthetic-fallback live path
- Other timeframes (M5/M15/D1)
- Live tail streaming / incremental updates
- `backtest/engine.py` changes

---

## Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | MT5 bridge may cap `bars` per request below 5000 | Discover empirically post-market-open; if hit, log warning + write what we got. Future: add bridge endpoint pagination. |
| R2 | Schema drift in existing parquet (e.g., `datetime64[ns]` vs `[ms]`) | T1 asserts and coerces to canonical schema before write. |
| R3 | Atomic write race if two backfills run concurrently | T1 uses `.tmp` + `os.replace` (atomic on POSIX). Cross-process locking out of scope. |

---

## Verdict

Plan approved. Phase 1.5 (plan review) is rolled into this analysis given the small scope (4 tasks) and locked design. Proceeding to Phase 4 (Implement).
