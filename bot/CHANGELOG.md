# Changelog

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
