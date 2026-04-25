# Design Brief — MT5 Supervisor

**Source:** Inline user requirement (treated as pre-existing spec; design gate auto-satisfied per Phase 0.5 rule)

## Goal
Provide a stdlib-only Python supervisor that spawns, monitors, restarts, and gracefully shuts down the MT5 paper-trading bot, gated by forex market hours.

## Chosen Approach
Single-file `scripts/supervisor.py` with a `Supervisor` class. Subprocess spawn function injected via constructor for testability. Health writer runs in a background thread with a stop event. Main loop is a synchronous restart-with-backoff loop. Signal handlers flip a stop flag.

### Module Layout
```
scripts/supervisor.py
  - is_market_open(now_utc) -> bool          # pure, testable
  - compute_backoff(restart_count) -> int    # pure, testable
  - class Supervisor:
      __init__(config, spawn_fn, clock_fn, health_path)
      run() -> int                            # main loop, returns exit code
      _write_health()                         # called by health thread
      _shutdown()                             # SIGTERM->wait->SIGKILL
  - main(argv) -> int                         # CLI entry
```

## Trade-offs Accepted
- Synchronous main loop (vs. asyncio): simpler, matches stdlib-only constraint, no event loop required for one child.
- Background thread for health file (vs. signal-based): smoother 30s cadence, easy to stop via `threading.Event`.
- Inject `spawn_fn` (callable returning a process-like object) for tests — avoids real subprocess in unit tests.
- Inject `clock_fn` (callable returning UTC datetime) so market-hours and uptime logic are deterministic in tests.

## Open Questions
None — spec is complete.

## Approval
Spec is the contract. Proceeding.
