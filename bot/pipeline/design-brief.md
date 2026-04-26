# Design Brief — H1 OHLCV History Backfill

**Run ID:** 20260426-h1backfill
**Date:** 2026-04-26
**Status:** APPROVED (decisions locked by user)
**Source:** Inline Socratic refinement (no pre-existing spec)
**Note:** This brief supersedes the prior PositionMonitor brief (archived in git history).

---

## Goal

Build a reusable backfill module + CLI script that pulls real H1 OHLCV bars from the running MT5 bridge and idempotently tops up `bridge_data/history/<SYMBOL>_H1.parquet` until each symbol has at least the target cached bar count (default 5,000), so the backtest engine produces statistically valid results from real (not synthetic) data.

---

## Chosen Approach

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | **Symbol set source** | CLI `--symbols` override; default reads `config.yaml` `bot.instruments` | Single source of truth (config) with operator escape hatch for ad-hoc backfills (e.g., warm a new symbol before adding it to config). |
| 2 | **Lookback strategy** | Top-up to target (`--target 5000`); fetch only the gap to reach the target cached bar count | Idempotent and incremental. Resumes after partial runs naturally (AC7). Avoids re-fetching bars already on disk. |
| 3 | **Merge semantics on overlap** | Prefer-existing — cached row wins; bridge row skipped on timestamp conflict | Conservative; protects historical cache from any bridge revisions. Deterministic and easy to reason about. |
| 4 | **Bridge connection pattern** | New `HistoricalDataClient` wrapper module owning pagination/retries/rate-limiting; depends on `MT5BridgeClient` for transport | Separation of concerns: transport stays in `MT5BridgeClient`; history-specific concerns (pagination, retry/backoff, dedup-on-fetch) live in the wrapper. Keeps `MT5BridgeClient` lean and reusable. |
| 5 | **Script style** | Both: `scripts/backfill_history.py` (Python module with `if __name__ == "__main__"`) **plus** `scripts/backfill_history.sh` thin shell wrapper that activates venv and forwards args | Python module is testable and importable; shell wrapper matches `start_bridge.sh` precedent for operators. Forwards `"$@"` so all CLI flags pass through. |

---

## Architecture (high level)

```
scripts/backfill_history.sh
        │  exec python -m scripts.backfill_history "$@"
        ▼
scripts/backfill_history.py  (CLI: argparse, logging, exit codes)
        │
        ▼
core/data/historical_client.py  (new HistoricalDataClient)
        │   • pagination loop (request N bars at a time, walk backwards)
        │   • retry/backoff on transient bridge errors
        │   • rate-limiting (sleep between requests)
        │   • returns DataFrame with canonical schema
        ▼
core/bridge/MT5BridgeClient  (existing — transport only)
        │
        ▼
   MT5 Bridge HTTP API

────────────────────────────────────────────────────────────

core/data/history_store.py  (new — parquet I/O + merge)
        • read_existing(symbol) -> DataFrame | None
        • merge_prefer_existing(cached, fetched) -> DataFrame
        • write_atomic(df, path)  (write to .tmp then rename)
```

---

## Trade-offs Accepted

- **Prefer-existing merge** means we cannot retroactively absorb bridge corrections to historical bars without manually deleting the cache. Acceptable: bridge corrections to closed H1 candles are vanishingly rare; safety > freshness.
- **New `HistoricalDataClient` module** adds one more file/abstraction vs. fattening `MT5BridgeClient`. Cost: small. Benefit: clear seam for mocking in unit tests; isolates pagination logic the live trading path doesn't need.
- **Top-up-to-target** strategy means the script's runtime depends on current cache state — first run fetches a lot, subsequent runs are near-noops. Acceptable; documented in `--help`.
- **Both Python module + shell wrapper** = two surface areas to maintain. Cost is negligible — wrapper is ~5 lines, just forwards args.

---

## Open Questions

None. All five decisions locked by user 2026-04-26.

---

## Acceptance Criteria Mapping (from intake)

| AC | Addressed by |
|---|---|
| AC1 (≥5000 bars) | Decision 2 (top-up to target); CLI default `--target 5000`. |
| AC2 (idempotent) | Decision 2 + Decision 3 (top-up + prefer-existing). |
| AC3 (schema match) | `history_store.read_existing` reads schema from existing parquet first; `HistoricalDataClient` produces matching dtypes; assert before write. |
| AC4 (tests green) | New pytest tests with mocked `HistoricalDataClient`; existing 324-test suite untouched. |
| AC5 (clear error on bridge down) | `HistoricalDataClient` raises `BridgeUnavailableError` on transport failure after retries exhausted; CLI exits non-zero with actionable message. |
| AC6 (progress logging) | CLI logs per-symbol: target / cached / fetched / skipped / elapsed. |
| AC7 (partial-fetch resumption) | Decision 2: top-up reads existing cache first, fetches only the gap. |

---

## Approval

User explicitly approved decisions 1–5 on 2026-04-26 prior to resuming the pipeline. Phase 0.5 gate satisfied. Proceeding to Phase 1.
