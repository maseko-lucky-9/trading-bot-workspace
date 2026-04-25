# Implementation Plan — MT5 Supervisor

## Task Decomposition

### T001 — Pure helpers: `is_market_open` + `compute_backoff`
- **Files:** `scripts/supervisor.py` (new)
- **Steps (3):**
  1. Create `scripts/` directory and `scripts/supervisor.py` with module docstring + imports
  2. Implement `is_market_open(now_utc: datetime) -> bool` — Sunday 22:00 UTC → Friday 21:00 UTC
  3. Implement `compute_backoff(restart_count: int, base=30, mult=2, cap=900) -> int`
- **Acceptance:**
  - `is_market_open(Saturday 12:00 UTC)` == False
  - `is_market_open(Monday 10:00 UTC)` == True
  - `is_market_open(Sunday 22:30 UTC)` == True
  - `is_market_open(Friday 22:00 UTC)` == False
  - `compute_backoff(0)` == 30, `(1)` == 60, `(2)` == 120, `(5)` == 900 (capped)

### T002 — `Supervisor` class skeleton + health writer
- **Files:** `scripts/supervisor.py` (modify)
- **Steps (3):**
  1. Define `Supervisor.__init__(spawn_fn, clock_fn, health_path, max_restarts, market_hours_enabled)`
  2. Implement `_write_health_snapshot()` that writes the exact JSON schema from spec
  3. Implement `_health_loop()` thread target: write every 30s until stop_event set; first write within 5s
- **Acceptance:**
  - Health file written at supervisor start with all 7 keys: pid, child_pid, uptime_s, restart_count, last_exit_code, last_restart_at, market_open
  - Atomic write (tmp + rename)

### T003 — Main run loop with backoff + market gate
- **Files:** `scripts/supervisor.py` (modify)
- **Steps (3):**
  1. Implement `run()`: loop spawning child, waiting, applying backoff on crash
  2. Track child uptime; if uptime > 3600s on exit, reset restart_count to 0 before next backoff
  3. Block in `_wait_for_market_open()` polling every 60s when `market_hours_enabled` and market closed
- **Acceptance:**
  - Backoff doubles consecutively, capped at 900s
  - After child uptime > 3600s, next crash restarts with backoff(0) == 30s
  - When market closed and gate enabled, no spawn happens
  - Halt after `max_restarts` (when > 0)

### T004 — Signal handling + graceful shutdown
- **Files:** `scripts/supervisor.py` (modify)
- **Steps (3):**
  1. Install SIGTERM/SIGINT handlers that set `_stop_requested`
  2. Implement `_shutdown(child)`: SIGTERM child, wait up to 10s, SIGKILL if alive
  3. Ensure `run()` returns 0 on graceful shutdown, stops health thread
- **Acceptance:**
  - SIGTERM to supervisor → child receives SIGTERM → exits → supervisor exits 0
  - If child ignores SIGTERM, SIGKILL after 10s
  - Health thread terminates cleanly

### T005 — CLI entrypoint
- **Files:** `scripts/supervisor.py` (modify)
- **Steps (2):**
  1. Implement `main(argv=None)` with argparse: `--max-restarts`, `--dry-run`, `--no-market-hours`
  2. `if __name__ == "__main__": sys.exit(main())`
- **Acceptance:**
  - `--dry-run` prints command and exits 0 without spawning
  - `--max-restarts N` halts after N restarts
  - `--no-market-hours` bypasses market gate

### T006 — Unit tests
- **Files:** `tests/test_supervisor.py` (new)
- **Steps (3):**
  1. Test pure helpers: `is_market_open` (4+ cases) and `compute_backoff` (4+ cases)
  2. Test `Supervisor` with `FakeProcess` mock: health write, restart-on-crash, backoff reset, max-restarts halt, dry-run
  3. Test signal-driven shutdown via direct `_stop_requested` flip + mock process
- **Acceptance:**
  - ≥10 tests, all passing
  - No real subprocess spawned
  - Full suite (`python3 -m pytest tests/ -q`) shows 262 + new tests, 0 failures

## Self-Review
- **self_review_pass:** true
- **self_review_notes:** ""
- Spec coverage: every acceptance criterion mapped to a task. Placeholder scan: clean. File boundaries: `scripts/supervisor.py` (new) + `tests/test_supervisor.py` (new) only. Granularity: each task 2-3 steps, single file. Scope: independent, single buildable unit.

## Dependencies
- T001 → T002 → T003 → T004 → T005 (sequential; each builds on prior in same file)
- T006 depends on T001-T005 complete (tests cover full module)
