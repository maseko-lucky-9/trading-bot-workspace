# ADR 001 — HTTP Bridge over File-Based IPC

**Date:** 2026-04-25
**Status:** Accepted

## Context

The bot must communicate with the MT5 EA running inside a UTM Windows VM on the same macOS M5 Pro host. Two approaches were considered:

| Concern | File-based IPC (`mt5_client.py`) | HTTP bridge (`http_client.py` + `http_server.py`) |
|---|---|---|
| Transport | Shared SMB/UTM folder via `/Volumes/mt5bridge` | HTTP on `192.168.64.1:8080` (UTM host-only network) |
| Setup friction | Requires UTM shared-folder config, macOS volume mount, Windows drive mapping | EA only needs WebRequest enabled for one URL |
| Network dependency | None (filesystem) | UTM host-only adapter (always present) |
| Testability | Requires real filesystem mounts; hard to mock | FastAPI TestClient + `httpx.MockTransport` — full in-process testing |
| Concurrency | File locking; race on write/read cycles | FastAPI + `threading.Lock`; proper atomic queues |
| Observability | Opaque file contents | HTTP status codes, structured JSON, `/ping` health endpoint |
| EA complexity | EA writes JSON blobs to disk | EA POSTs to `/tick`, `/account`; polls `/command` |

## Decision

Replace the file-based bridge (`core/bridge/mt5_client.py`) with an HTTP bridge:
- **Server**: `core/bridge/http_server.py` — FastAPI app running on `0.0.0.0:8080` on macOS
- **Client**: `core/bridge/http_client.py` — `MT5BridgeClient` with tenacity retry, `httpx`
- **EA**: `mql5/PythonBridgeHTTP.mq5` — pushes tick/account data, polls for commands

The old `mt5_client.py` is removed. `scripts/detect_bridge.py` is retained as historical reference only (its volume-scanning logic is no longer part of the live path).

## Consequences

- **Positive**: In-process unit testing with `TestClient`; retry/timeout policy via tenacity; structured `/ping` liveness; no SMB/mount dependency.
- **Positive**: `/history` endpoint serves real accumulated H1 bars from EA tick pushes; falls back to deterministic synthetic data before the EA is live — backtests run immediately.
- **Negative**: Requires the FastAPI bridge server process to be running before `main.py` starts (`scripts/start_bridge.sh`).
- **Neutral**: UTM host-only network (`192.168.64.1`) is always present when the VM is running; no additional network config needed.
