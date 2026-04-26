# Phase 2 — Context Map: PositionMonitor

**Run ID:** 20260426-position-monitor
**Date:** 2026-04-26
**Source:** `pipeline/plan.md` (APPROVED) + `pipeline/plan-review.md`

---

## 1. Primary Files (touched by this plan)

| File | Status | Purpose | Tasks |
|---|---|---|---|
| `core/monitoring/__init__.py` | NEW | Package marker for `core.monitoring` namespace | T001 |
| `core/monitoring/position_monitor.py` | NEW | Single module containing `_PosSnap`, `_diff`, `_JsonlWriter`, `_Alerter`, `PositionMonitor` | T003-T007 |
| `tests/test_position_monitor.py` | NEW | Unit tests for all classes/functions in the new module | T009-T013 |
| `config.yaml` | MODIFY | Add `risk.alert_loss_usd`, `monitoring.poll_interval_s`, `monitoring.log_path` | T002 |
| `main.py` | MODIFY | Construct + start/stop `PositionMonitor` only when `args.mode == "live"` | T008 |

---

## 2. Existing Codebase Dependencies (read in this session)

| File | Lines verified | Purpose for this plan |
|---|---|---|
| `main.py` | 1-243 | Hosts the live-mode lifecycle. Pattern reference: `_start_autoresearch` at lines 74-80 (daemon thread idiom). Insertion point for monitor construction: after line 138 (`om = OrderManager(...)`). Insertion point for stop: inside `finally:` block lines 231-238, before `bridge.close()`. |
| `config.yaml` | 1-35 | Existing `risk:` block at lines 12-19; `bot:` block at lines 5-10. Append new `monitoring:` block at end. |
| `core/execution/live_broker.py` | 1-82 | Confirms `get_positions() -> list[dict]` (line 73) and `get_closed() -> list[dict]` (line 77). PositionMonitor depends on these as the broker contract. |
| `core/bridge/http_client.py` | 1-165 | Confirms `MT5BridgeClient.get_results()` (line 148) is the underlying source for `get_closed()`. Returns raw `/results` payload — semantics on dedupe NOT documented in client → carry-forward note for T006. |

---

## 3. Patterns to Follow

### 3.1 Daemon thread idiom (mirrored from `main.py` `_start_autoresearch`)

```python
# main.py:74-80 (existing pattern)
def _start_autoresearch(loop: AutoresearchLoop, iterations: int) -> threading.Thread:
    t = threading.Thread(
        target=loop.run, kwargs={"max_iterations": iterations}, daemon=True
    )
    t.start()
    print(f"autoresearch started iterations_per_run={iterations}")
    return t
```

`PositionMonitor.start()` must use the **same** `daemon=True` + named-thread idiom. Use `name="PositionMonitor"` for identifiable stack traces.

### 3.2 Module docstring + `from __future__ import annotations`

All `core/` modules use `from __future__ import annotations` at the top (verified in `live_broker.py:7`, `order_manager.py:7`, `http_client.py:20`). Apply in T003 step 1.

### 3.3 Config access defensive pattern

Existing code uses `(cfg.get("risk") or {}).get("min_equity", MIN_EQUITY_USD)` (verified in `live_broker.py:31-33`). PositionMonitor `__init__` must mirror this pattern for `(cfg.get("monitoring") or {}).get("poll_interval_s", 5)` etc.

### 3.4 Test conventions

Tests live under `tests/test_<module_name>.py`. Use pytest fixtures `tmp_path`, `capsys`, `caplog` (stdlib pytest). Existing test count = 308 — verified via plan intake. New file `tests/test_position_monitor.py` adds ~15 tests.

---

## 4. Carry-forward Notes (from Phase 1.5 plan review)

| ID | Note | Action in Phase 4 |
|---|---|---|
| CF-1 | Broker `get_closed()` semantics undocumented — may return cumulative or delta | T006: implement defensive `self._seen_closed_tickets: set[int]` dedupe; `closed = [c for c in raw if c["ticket"] not in seen]; seen.update(c["ticket"] for c in closed)` |
| CF-2 | `_PosSnap.open_time` source unclear when bridge dict lacks the field | T006: fall back to `datetime.now(timezone.utc).isoformat()` recorded at first-seen time and stored in `self._last_snapshot` |

These two notes MUST be inlined into T006's implementation prompt by the inner-loop controller.

---

## 5. Test Coverage Map

| Component | Test file | Tests |
|---|---|---|
| `_diff()` + `_PosSnap` | `tests/test_position_monitor.py` | T009 (3 tests: opened, modified, unchanged) |
| `_JsonlWriter` | `tests/test_position_monitor.py` | T010 (3 tests: append, rotate, cleanup) |
| `_Alerter` | `tests/test_position_monitor.py` | T011 (4 tests: stdout fill, warn-on-loss, silent-on-small-loss, **no-slack-imports source scan**) |
| `PositionMonitor.poll_once` | `tests/test_position_monitor.py` | T012 (2 tests: end-to-end open/modify/close, exception swallow) |
| `start()/stop()` lifecycle | `tests/test_position_monitor.py` | T013 (3 tests: daemon thread, idempotent start, idempotent stop) |
| Full suite (regression) | `tests/` (all) | T014 (308 existing + ~15 new = ~323 total) |

---

## 6. Unknown Dependencies

**None.** All external libraries used are Python stdlib: `logging`, `logging.handlers.RotatingFileHandler`, `threading`, `json`, `dataclasses`, `pathlib`, `os`, `sys`, `time`, `datetime`, `inspect` (test-only). No new requirements.txt entries needed.

---

## 7. Bounded Context Boundary

```
trading-bot-workspace/bot/
├── core/
│   ├── bridge/           ← READ-ONLY for this plan (confirms broker surface)
│   ├── execution/        ← READ-ONLY for this plan (LiveBroker contract)
│   └── monitoring/       ← NEW bounded context (this plan owns)
│       ├── __init__.py   ← T001
│       └── position_monitor.py  ← T003-T007
├── tests/
│   └── test_position_monitor.py  ← T009-T013
├── config.yaml           ← T002 (additive only)
└── main.py               ← T008 (additive only — surrounding setup/teardown)
```

No file outside this map will be modified. Any agent attempting to edit other files in Phase 4 should be flagged by the Doublecheck reviewer.

---

```yaml
## Agent Output Contract
contract_version: "1.1"
agent: "Context Architect (in-thread by orchestrator)"
phase: "phase_2"
status: "success"
confidence: "high"
files_changed: []
files_created:
  - "pipeline/context-map.md"
unknown_dependencies: []
carry_forward_count: 2
next_phase: "phase_3 (Pre-flight Validation via Governance Enforcer)"
blockers: []
```
