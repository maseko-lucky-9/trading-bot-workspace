# Changelog

## [0.1.1] — 2026-04-29 — Paper-broker resilience + OOS window reset

### Fixed
- **Paper-broker silent fallback fills** (`core/execution/paper_broker.py`): `_current_prices` no longer falls through to a hard-coded `1.10000 / 1.10002` price when the bridge tick fetch returns empty or holds a different symbol. Instead it raises `StaleTickError`. A 5 s last-known-good cache (`LKG_TTL_SECONDS`) softens transient bridge blips so genuine outages are the only thing that surface as rejections. Root-cause of the 173-row corrupt `trades.csv` in OOS window v1.
- **Symbol-mismatch acceptance**: `_current_prices` now requires `tick.symbol == requested_symbol` (or absent — for legacy/test fixtures). Previously, an EA pushing only USDJPY ticks would happily fill EURUSD orders at USDJPY prices.
- **Ticket-counter collision across restarts** (`core/execution/paper_broker.py:_ticket_seq`): counter persists to `checkpoints/paper_broker.json` (atomic JSON write) and reloads on `__init__`. Previously every bot restart reset to `1_000_000`, producing duplicate-ticket rows in the journal.
- **Orphaned open positions on restart**: `_positions` dict persists alongside the counter and reloads on `__init__`. New broker instance can `close_position()` tickets opened by the previous instance.
- **Corrupt-state recovery**: a malformed `paper_broker.json` is logged as WARN and the counter is reseeded from `max(ticket) + 1` in the existing CSV journal; no crash.

### Operations
- **OOS paper-trading window v2 opened 2026-04-29**. Window v1 (opened 2026-04-27) voided due to the bridge-outage data corruption; corrupt journal moved to `logs/archive/trades-corrupt-20260427.csv`.
- `autoresearch/params.yaml` restored from `params.yaml.locked-20260427.bak` (`mean_reversion`, `bb_period=14`, `bb_std=2.25`, `rsi_period=7`, `atr_multiplier=2.25`).
- `config.yaml: autoresearch.enabled: false` (re-enable only after ≥200-trade DSR re-evaluation closes the window).
- Bot launchd agent (`com.ltmas.mt5bot.bot`) unloaded pending operator action: subscribe **EURUSD** in the MT5 MarketWatch on the UTM VM (or open an EURUSD chart) before reload, so the bridge actually receives EURUSD ticks.

### Tests
- 11 new cases in `tests/test_paper_broker.py` pin the new contract (fail-closed raise on bridge exception / empty tick / symbol mismatch; LKG cache hit and TTL expiry; close-position rolls back on stale tick; ticket-counter persistence; open-position reload across restart; corrupt-state-file CSV reseed; atomic write leaves no `.tmp` artefact).
- `tests/test_order_manager.py` fixtures pass an isolated `state_path` so the real `checkpoints/` directory is never polluted by the test run.
- Full suite green: **598 passed**.

### Known limitations carried forward
- Bridge HTTP server still uses a single tick slot (`_state["tick"]`); multi-symbol trading remains unsafe until `core/bridge/http_server.py` adopts per-symbol storage. Tracked separately; not required for single-symbol EURUSD M15 paper trading.

## [0.1.0] — 2026-04-25 ✓ production-verified

### Added
- **HTTP bridge** (`core/bridge/http_client.py`, `core/bridge/http_server.py`): FastAPI-based IPC between the macOS bot and the MT5 EA in the UTM Windows VM. No shared folders required — EA communicates via HTTP on the UTM host-only network (`192.168.64.1:8080`). See ADR 001.
- **Real H1 history accumulation**: `http_server.py` now buffers H1 OHLCV bars from EA tick pushes. `/history` serves real accumulated bars when available and falls back to a deterministic synthetic random walk otherwise, so backtests run immediately without the VM.
- **`PythonBridgeHTTP.mq5`**: MT5 EA that pushes tick/account data and polls for trade commands over HTTP. Replaces the file-based `PythonBridge.mq5`.
- **Backtest engine** (`backtest/engine.py`): EMA crossover and mean-reversion simulation strategies with Sharpe/drawdown/win-rate reporting via `PerformanceTracker`.
- **Paper broker** (`core/execution/paper_broker.py`): Simulates fills at correct bid/ask prices, journalling all trades to a CSV log.
- **Risk manager**, **order manager**, **live broker** (`core/execution/`): Full execution stack for paper and live modes.
- **Autoresearch loop** (`autoresearch/`): Autonomous strategy research with knowledge base persistence.
- **Checkpoint/state management** (`core/state.py`): Auto-saves bot state every 5 minutes.
- **Test suite**: 241 tests, 93% coverage across all core modules (bridge client/server, backtest engine, performance tracker, paper broker, feed, indicators, strategies, order/risk managers, state). `autoresearch/loop.py` at 100%.

### Verified
- **Live E2E test** (2026-04-25): UTM Windows VM + MT5 + `PythonBridgeHTTP.mq5` EA → `ea_connected: true` → H1 bars serving `source: "live"` for USDJPY → paper trade round-trip (`place_order` → `get_positions` → `close_position`) completed successfully.

### Removed
- **File-based IPC bridge** (`core/bridge/mt5_client.py`): Replaced by the HTTP bridge. The old bridge required UTM shared-folder config, macOS volume mounts, and Windows drive mapping — none of which are needed with the HTTP approach.

### Changed
- `pyproject.toml`: Added `[project]` table with `name`, `version = "0.1.0"`, and `requires-python`.

### Known Limitations
- `v0.1.0` is gated on live bridge verification: Windows VM + MT5 + `PythonBridgeHTTP.mq5` EA must be confirmed end-to-end before tagging.
- `/history` serves real bars for H1 only. Other timeframes (M1, M5, H4, D1) still return synthetic data until the EA exposes `CopyRates` for those periods.
- `scripts/detect_bridge.py` is retained as historical reference but is not part of the live path (it targets the old file-based bridge).
