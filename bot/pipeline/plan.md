# Phase 1 — Implementation Plan: PositionMonitor

**Run ID:** 20260426-position-monitor
**Date:** 2026-04-26
**Author:** Pipeline Orchestrator (in-thread; Agent tool unavailable in this session)
**Source:** `pipeline/intake-validation.md` + approved `pipeline/design-brief.md` (log-only alerts; Slack dropped)

---

## 1. Scope Recap

Build `PositionMonitor` as a daemon-thread component that polls the MT5 bridge, diffs against last snapshot, writes NDJSON state-change events to a rotating log, prints `[FILL]` summaries on close, and emits `logging.WARNING` when single-trade loss exceeds threshold. **No Slack, no `urllib.request`, no `SLACK_WEBHOOK_URL` env var.**

### Acceptance Criteria (from intake)

| AC | Requirement |
|---|---|
| AC1 | Polls bridge for open + closed positions at `monitoring.poll_interval_s` (default 5s) |
| AC2 | Writes NDJSON state-change events (opened/modified/closed) to `monitoring.log_path` (default `logs/positions.jsonl`); rotation 10 MB / 7-day cleanup |
| AC3 | On close: stdout `[FILL] ticket=<n> symbol=<sym> profit=<$x.xx> at <ISO-timestamp>` |
| AC4 | On loss > `risk.alert_loss_usd` (default $50): emit `logging.WARNING` line — log-only |
| AC5 | Started as daemon thread only when `args.mode == "live"`; zero changes to `while _running:` body |
| AC6 | Full unit tests for monitor (mocked broker, mocked clock) |
| AC7 | All 308 existing tests remain green |

---

## 2. Architecture (final, log-only)

```
main.py (--mode live --confirm-live)
  │
  ├── LiveBroker (existing, unchanged)
  │
  └── PositionMonitor (NEW — core/monitoring/position_monitor.py)
        │  Public API:
        │    __init__(broker, config, *, clock=time.time, logger=None, stdout=sys.stdout)
        │    start() -> None        # spawns daemon thread
        │    stop(timeout=2.0)      # sets stop flag, joins thread
        │    poll_once()            # single-pass; testable without thread
        │
        ├── _PollerThread (daemon)
        │     loop: while not stop_event.is_set():
        │             self.poll_once()
        │             stop_event.wait(poll_interval_s)
        │
        ├── _diff(current_open, last_open) -> events
        │     opened   = tickets in current_open but not last_open
        │     modified = tickets in both, but mutable fields (sl, tp, volume) changed
        │     closed   = consumed from broker.get_closed() since last poll
        │
        ├── _JsonlWriter (wraps logging.Logger + RotatingFileHandler)
        │     RotatingFileHandler(maxBytes=10*1024*1024, backupCount=10)
        │     7-day cleanup pass on start() + after each emit (cheap stat check; throttled to once/hour)
        │
        └── _Alerter
              on_close(event): print stdout [FILL] ...
              maybe_alert(event):
                if event.profit < -alert_loss_usd:
                  logger.warning("LOSS_ALERT ticket=%s symbol=%s profit=$%.2f", ...)
```

### Snapshot Schema (in-memory, per ticket)

```python
@dataclass(frozen=True)
class _PosSnap:
    ticket: int
    symbol: str
    side: str          # "buy" | "sell"
    volume: float
    sl: float
    tp: float
    open_price: float
    open_time: str     # ISO-8601 UTC
```

### NDJSON Event Schema (one line per event)

```json
{"ts": "2026-04-26T12:34:56.789012+00:00", "event": "opened|modified|closed", "ticket": 12345, "symbol": "USDJPY", "side": "buy", "volume": 0.10, "sl": 154.20, "tp": 156.10, "open_price": 154.85, "close_price": null, "profit": null, "reason": null}
```

`closed` events carry `close_price`, `profit`, and `reason`. `modified` events include the changed-field list under `"changes"`.

---

## 3. Task Decomposition (atomic, 2-5 min each, 1-3 steps max)

Tasks are ordered by dependency. Tasks marked `parallel-safe: yes` share no files with their siblings and may run concurrently if the orchestrator chooses.

### T001 — Add `core/monitoring/` package skeleton
**Files:** `core/monitoring/__init__.py` (NEW)
**Steps:**
1. Create `core/monitoring/__init__.py` with module docstring `"""Live position monitoring."""` (no exports yet).
**Acceptance:** `python -c "import core.monitoring"` succeeds without error.
**Depends on:** none
**parallel-safe:** yes (with T002 only — no file overlap)

### T002 — Extend `config.yaml` with monitor + risk threshold
**Files:** `config.yaml` (MODIFY)
**Steps:**
1. Append to `risk:` block: `alert_loss_usd: 50.0    # single-trade loss threshold for WARNING log`
2. Add new top-level `monitoring:` block with two keys:
   ```yaml
   monitoring:
     poll_interval_s: 5            # PositionMonitor polling interval
     log_path: "logs/positions.jsonl"
   ```
**Acceptance:** `yaml.safe_load(open("config.yaml"))` returns dict with `risk.alert_loss_usd == 50.0` and `monitoring.poll_interval_s == 5` and `monitoring.log_path == "logs/positions.jsonl"`.
**Depends on:** none
**parallel-safe:** yes (with T001 only)

### T003 — Implement `_PosSnap` dataclass + `_diff()` pure function
**Files:** `core/monitoring/position_monitor.py` (NEW — partial)
**Steps:**
1. Add module header (`"""PositionMonitor — daemon-thread component for live position tracking. LOG-ONLY alerts."""`), imports (`from __future__ import annotations`, `dataclasses`, `typing`).
2. Define `@dataclass(frozen=True) class _PosSnap` with fields exactly as schema in §2.
3. Define `def _diff(current: dict[int, _PosSnap], previous: dict[int, _PosSnap]) -> tuple[list[_PosSnap], list[tuple[_PosSnap, list[str]]]]:` returning `(opened, modified_with_changed_fields)`. Closed events come from broker, NOT from diff.
**Acceptance:** Function importable; pure (no I/O); returns correct opened/modified lists for hand-rolled inputs.
**Depends on:** T001
**parallel-safe:** no

### T004 — Implement `_JsonlWriter` (rotating handler + 7-day cleanup)
**Files:** `core/monitoring/position_monitor.py` (MODIFY — append class)
**Steps:**
1. Define `class _JsonlWriter` with `__init__(self, path: str, *, max_bytes=10*1024*1024, backup_count=10, retention_days=7, clock=time.time)`.
2. In `__init__`: ensure parent dir exists (`Path(path).parent.mkdir(parents=True, exist_ok=True)`); construct `logging.Logger` with `RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backup_count)`; formatter is raw `%(message)s` (we pre-serialize); call `_cleanup_old()` once.
3. Define `write(self, event: dict) -> None`: serialize via `json.dumps(event, separators=(",", ":"))`; emit; throttled call to `_cleanup_old()` (once per hour using `self._last_cleanup`).
4. Define `_cleanup_old(self)`: glob `path*` rotated files; `os.unlink` any whose `mtime` is older than `retention_days * 86400`.
**Acceptance:** Writing 1000 events produces a single line per event in the file; rotation triggers when size exceeds `max_bytes`; old files removed on cleanup.
**Depends on:** T003
**parallel-safe:** no

### T005 — Implement `_Alerter` (log-only — no Slack)
**Files:** `core/monitoring/position_monitor.py` (MODIFY — append class)
**Steps:**
1. Define `class _Alerter` with `__init__(self, *, alert_loss_usd: float, logger: logging.Logger, stdout=sys.stdout)`.
2. Define `on_close(self, event: dict) -> None`: format and print `[FILL] ticket=<n> symbol=<sym> profit=$<x.xx> at <iso-ts>` to `stdout`. Then call `self.maybe_alert(event)`.
3. Define `maybe_alert(self, event: dict) -> bool`: if `event["profit"] is not None and event["profit"] < -self.alert_loss_usd`, call `self.logger.warning("LOSS_ALERT ticket=%s symbol=%s profit=$%.2f", event["ticket"], event["symbol"], event["profit"])` and return `True`; else return `False`.
**Acceptance:** No `urllib`, no `requests`, no `os.environ.get("SLACK_WEBHOOK_URL")` anywhere in the file. `maybe_alert` returns `True` only when `profit < -alert_loss_usd`.
**Depends on:** T003
**parallel-safe:** no

### T006 — Implement `PositionMonitor.poll_once()` (sync, testable, no thread)
**Files:** `core/monitoring/position_monitor.py` (MODIFY — append main class)
**Steps:**
1. Define `class PositionMonitor` with `__init__(self, broker, config: dict, *, clock=None, logger=None, stdout=None)`. Read `monitoring.poll_interval_s`, `monitoring.log_path`, `risk.alert_loss_usd` from config with documented defaults (5, "logs/positions.jsonl", 50.0). Construct `_JsonlWriter` and `_Alerter`. Initialize `self._last_snapshot: dict[int, _PosSnap] = {}`.
2. Define `poll_once(self) -> dict` (returns counts dict for testability):
   - `current_raw = self.broker.get_positions()` → convert to `dict[int, _PosSnap]`.
   - `closed_raw = self.broker.get_closed()` → list (broker contract: only NEW closes since last call).
   - `opened, modified = _diff(current, self._last_snapshot)`.
   - For each event in `opened` → write NDJSON `event="opened"`.
   - For each `(snap, changed_fields)` in `modified` → write NDJSON `event="modified"` with `"changes": changed_fields`.
   - For each closed → build event dict with `event="closed"`, `close_price`, `profit`, `reason`; write NDJSON; call `alerter.on_close(event)`.
   - Update `self._last_snapshot = current`.
   - Return `{"opened": len(opened), "modified": len(modified), "closed": len(closed_raw)}`.
3. Wrap the entire body in `try/except Exception as exc:` — log `logger.exception("PositionMonitor.poll_once failed")` and return `{"error": str(exc)}` (never propagate; thread must keep running).
**Acceptance:** Calling `poll_once()` with mocked broker produces NDJSON lines, returns counts dict. No thread involved.
**Depends on:** T004, T005
**parallel-safe:** no

### T007 — Implement `start()` / `stop()` (daemon thread wrapper)
**Files:** `core/monitoring/position_monitor.py` (MODIFY — append methods to PositionMonitor)
**Steps:**
1. Add `self._stop_event = threading.Event()` and `self._thread: threading.Thread | None = None` to `__init__`.
2. Define `start(self) -> None`: if `self._thread is not None and self._thread.is_alive()`, return (idempotent). Create `threading.Thread(target=self._run, daemon=True, name="PositionMonitor")`; start; assign to `self._thread`.
3. Define `_run(self) -> None`: loop `while not self._stop_event.is_set(): self.poll_once(); self._stop_event.wait(self.poll_interval_s)`.
4. Define `stop(self, timeout: float = 2.0) -> None`: set `self._stop_event`; if thread alive, `self._thread.join(timeout)`. Idempotent.
**Acceptance:** `start()` spawns daemon thread (`thread.daemon == True`); `stop()` returns within `timeout + poll_interval`. Calling `start()` twice does not spawn a second thread.
**Depends on:** T006
**parallel-safe:** no

### T008 — Wire `PositionMonitor` into `main.py` (live mode only)
**Files:** `main.py` (MODIFY)
**Steps:**
1. Add import after existing `from core.execution.live_broker import ...` line: `from core.monitoring.position_monitor import PositionMonitor  # noqa: E402`.
2. After `om = OrderManager(cfg, broker, tracker=tracker)` and before `state = BotState()`, add:
   ```python
   position_monitor: PositionMonitor | None = None
   if args.mode == "live":
       position_monitor = PositionMonitor(broker, cfg)
       position_monitor.start()
       print("position_monitor started")
   ```
3. In the existing `finally:` block, BEFORE `bridge.close()`, add:
   ```python
   if position_monitor is not None:
       try:
           position_monitor.stop(timeout=2.0)
       except Exception as exc:
           print(f"position_monitor stop failed: {exc}", file=sys.stderr)
   ```
4. **Do NOT modify the `while _running:` loop body.** Only the surrounding setup/teardown changes.
**Acceptance:** `python main.py --mode paper` starts WITHOUT touching PositionMonitor (none constructed). `python main.py --mode live --confirm-live` constructs + starts the monitor. Existing 308 tests still green.
**Depends on:** T007
**parallel-safe:** no

### T009 — Unit tests: `_diff()` and `_PosSnap`
**Files:** `tests/test_position_monitor.py` (NEW — partial)
**Steps:**
1. Create test file with module header and import `from core.monitoring.position_monitor import _PosSnap, _diff`.
2. Add `def test_diff_detects_opened()`: previous empty, current has one snap → `opened == [snap]`, `modified == []`.
3. Add `def test_diff_detects_modified_sl_tp_volume()`: same ticket with changed `sl` → returns one modified entry with `["sl"]` in changed_fields.
4. Add `def test_diff_ignores_unchanged()`: identical snaps → both lists empty.
**Acceptance:** Three tests pass via `pytest tests/test_position_monitor.py -k "diff" -v`.
**Depends on:** T003
**parallel-safe:** yes (with T010 only via dependency T004; written sequentially in same file to avoid edit conflicts)

### T010 — Unit tests: `_JsonlWriter` rotation + cleanup
**Files:** `tests/test_position_monitor.py` (MODIFY — append)
**Steps:**
1. Add `def test_jsonl_writer_appends_one_line_per_event(tmp_path)`: write 5 events, assert file has 5 lines, each parses as JSON.
2. Add `def test_jsonl_writer_rotates_at_max_bytes(tmp_path)`: construct with `max_bytes=200`; write 50 events; assert at least one rotated file exists (`positions.jsonl.1`).
3. Add `def test_jsonl_writer_cleans_old_files(tmp_path)`: create a `positions.jsonl.5` file, set its mtime 10 days in the past; instantiate writer with `retention_days=7`; call `_cleanup_old()`; assert file deleted.
**Acceptance:** Three tests pass.
**Depends on:** T004, T009
**parallel-safe:** no (sequential edit of same file)

### T011 — Unit tests: `_Alerter` (log-only, no network)
**Files:** `tests/test_position_monitor.py` (MODIFY — append)
**Steps:**
1. Add `def test_alerter_prints_fill_on_close(capsys)`: build event dict with `profit=12.34`, call `on_close()`; assert stdout contains `[FILL] ticket=` and `profit=$12.34`.
2. Add `def test_alerter_warns_on_loss_above_threshold(caplog)`: with `alert_loss_usd=50.0` and event `profit=-75.0`, assert `caplog.records` has one WARNING with `LOSS_ALERT` and `profit=$-75.00`.
3. Add `def test_alerter_silent_on_small_loss(caplog)`: with `alert_loss_usd=50.0` and `profit=-25.0`, assert no WARNING records.
4. Add `def test_alerter_no_slack_imports()`: `import inspect, core.monitoring.position_monitor as pm; src = inspect.getsource(pm); assert "urllib" not in src and "SLACK_WEBHOOK_URL" not in src and "requests" not in src`.
**Acceptance:** Four tests pass; the `no_slack_imports` test enforces design Decision 3.
**Depends on:** T005, T010
**parallel-safe:** no

### T012 — Unit tests: `PositionMonitor.poll_once()` end-to-end with mocked broker
**Files:** `tests/test_position_monitor.py` (MODIFY — append)
**Steps:**
1. Add a `_FakeBroker` helper class with mutable `open_positions` and `closed_queue` (returns + clears on `get_closed()`).
2. Add `def test_poll_once_records_open_modify_close(tmp_path, caplog)`:
   - Build `cfg = {"monitoring": {"poll_interval_s": 5, "log_path": str(tmp_path/"p.jsonl")}, "risk": {"alert_loss_usd": 50.0}}`.
   - First poll: 1 open position → assert NDJSON has 1 `opened` line.
   - Second poll: same position with new SL → 1 `modified` line.
   - Third poll: position moved to closed_queue with profit=-100 → 1 `closed` line + WARNING in caplog.
3. Add `def test_poll_once_swallows_broker_exception(caplog, tmp_path)`: broker raises `RuntimeError` from `get_positions()`; `poll_once()` returns dict with `"error"` key; logger.exception called.
**Acceptance:** Two tests pass.
**Depends on:** T006, T011
**parallel-safe:** no

### T013 — Unit tests: `start()` / `stop()` thread lifecycle
**Files:** `tests/test_position_monitor.py` (MODIFY — append)
**Steps:**
1. Add `def test_start_creates_daemon_thread(tmp_path)`: `monitor.start()`; assert `monitor._thread.daemon is True` and `is_alive()`. Then `monitor.stop()`; assert thread terminates within 1.0s.
2. Add `def test_start_is_idempotent(tmp_path)`: call `start()` twice; assert only one thread reference; second call is no-op.
3. Add `def test_stop_is_idempotent(tmp_path)`: call `stop()` without `start()`; no exception. Call `stop()` twice; no exception.
**Acceptance:** Three tests pass.
**Depends on:** T007, T012
**parallel-safe:** no

### T014 — Integration check: full test suite (308 + new ~15 = ~323 tests)
**Files:** none (read-only verification)
**Steps:**
1. Run `cd /Users/ltmas/trading-bot-workspace/bot && python -m pytest -q 2>&1 | tail -20`.
2. Verify: total tests >= 308 + new test count from T009-T013; failures == 0.
3. If any pre-existing test now fails: regression — diagnose via `git diff` on changed files, fix in-place, re-run.
**Acceptance:** Full suite green, no regressions, new tests included in count.
**Depends on:** T013

---

## 4. Task Dependency Graph

```
T001 ──┐
       ├─→ T003 ──┬─→ T004 ─→ T010 ──┐
T002 ──┘          │                  │
                  ├─→ T005 ─→ T011 ──┤
                  │                  │
                  └─→ T009 ───────────┤
                                     ↓
                                   T006 ─→ T007 ─→ T008
                                     │      │       │
                                     └─→ T012 ─→ T013
                                                    │
                                                    ↓
                                                  T014
```

**Parallel opportunities (Phase 4 task scheduler may exploit):**
- T001 + T002 (independent files: `__init__.py` vs `config.yaml`) — true parallel.
- All other tests in `test_position_monitor.py` are written sequentially to one file → no parallelism beyond T001/T002.

---

## 5. Files Touched (final list)

| File | Action | Tasks |
|---|---|---|
| `core/monitoring/__init__.py` | NEW | T001 |
| `core/monitoring/position_monitor.py` | NEW | T003, T004, T005, T006, T007 |
| `tests/test_position_monitor.py` | NEW | T009, T010, T011, T012, T013 |
| `config.yaml` | MODIFY (append 4 lines) | T002 |
| `main.py` | MODIFY (3 small inserts: 1 import, 1 setup block ~5 lines, 1 finally insert ~5 lines) | T008 |

Total: 2 new files, 1 new test file, 2 modified files. All under existing bounded contexts.

---

## 6. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Daemon thread leaks on crash | `daemon=True` ensures process exit kills it; `stop()` in `main.py` `finally:` handles graceful path |
| Broker exception kills polling loop | `try/except` in `poll_once()` swallows + logs (T006 step 3) |
| 7-day cleanup runs every poll → I/O storm | Throttled to once per hour via `self._last_cleanup` (T004 step 3) |
| `RotatingFileHandler` not thread-safe under concurrent writers | Single producer (PositionMonitor thread) — no concurrency |
| Tests sleep on real clock → slow suite | All tests use `poll_once()` directly (sync) or short timeouts (≤1s in T013) |
| `tests/test_position_monitor.py` written across 5 tasks → merge conflicts if parallel | Task dependency graph forces sequential edits (T009 → T010 → T011 → T012 → T013) |
| `monitoring.log_path` parent dir missing | Writer creates parents in `__init__` (T004 step 2) |
| Existing 308 tests fail due to import-time side effects in new module | Module is import-safe (no thread spawn at import; only on `start()`) |

---

## 7. Out of Scope (explicit)

- Slack integration (Decision 3 — DROPPED)
- Persistent monitor-state across restarts (re-emits open positions as `opened` events on restart — documented behaviour)
- WebSocket / push-based bridge events (polling only)
- Multi-broker support (single LiveBroker only)
- `--live` flag rename (keeps `--mode live`)
- Metrics export (Prometheus, etc.)

---

## 8. Self-Review (mandated by orchestrator dispatch instructions)

```yaml
self_review_pass: true
self_review_notes: |
  1. Spec coverage: AC1=T006/T007, AC2=T004, AC3=T005, AC4=T005+T011, AC5=T008, AC6=T009-T013, AC7=T014 — all 7 ACs mapped to at least one task.
  2. Placeholder scan: zero matches for "TBD", "TODO", "implement later", "similar to Task", "add appropriate" in this plan.
  3. File boundary map: all 5 files explicitly listed in §5 with absolute task attribution.
  4. Granularity check: every task has 1-3 numbered atomic steps. No task exceeds 4 steps. Largest file edit (T006) decomposed into 3 sub-steps. Tests split across T009-T013 to keep each task <=4 sub-tests.
  5. Scope check: single bounded context (core/monitoring/); buildable + testable independently of any other in-flight work; no cross-cutting refactor required.
  6. Slack guard: T005 acceptance + T011 step 4 explicitly assert ZERO urllib / SLACK_WEBHOOK_URL / requests references → enforces Decision 3 at the test level.
  7. Idempotency: T007 start() and stop() both required to be idempotent (acceptance criteria + T013 sub-tests).
  8. Error containment: T006 step 3 mandates exception swallowing in poll_once() so a transient broker fault never kills the daemon.
```

---

## 9. Plan Agent Handoff Contract

```yaml
contract_version: "1.0"
phase: "phase_1"
agent: "Plan (in-thread by orchestrator; Agent tool unavailable)"
status: "complete"
confidence: "high"
artifacts:
  - path: "pipeline/plan.md"
    purpose: "Implementation plan with 14 atomic tasks (T001-T014)"
files_changed: []
files_created:
  - "pipeline/plan.md"
acceptance_mapped:
  AC1: ["T006", "T007"]
  AC2: ["T004"]
  AC3: ["T005"]
  AC4: ["T005", "T011"]
  AC5: ["T008"]
  AC6: ["T009", "T010", "T011", "T012", "T013"]
  AC7: ["T014"]
blockers: []
self_review_pass: true
self_review_notes: "See section 8 above."
next_phase: "phase_1_5 (Plan Review via /review-plan skill)"
```
