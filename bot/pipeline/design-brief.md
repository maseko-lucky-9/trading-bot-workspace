# Design Brief — Live Position Monitor

**Run ID:** 20260426-position-monitor
**Status:** AWAITING USER APPROVAL

## Goal
Add a `PositionMonitor` background component that polls the MT5 bridge for open/closed positions, logs every state change as NDJSON, prints fill summaries, and alerts on large losses (log + optional Slack) — without modifying any existing public interface.

## Chosen Approach

### Architecture (ASCII)

```
main.py (--mode live)
  │
  ├── LiveBroker (existing, unchanged)
  │
  └── PositionMonitor (new — core/monitoring/position_monitor.py)
        │
        ├── _PollerThread (daemon)
        │     loop: every poll_interval_s
        │       open_now    = broker.get_positions()
        │       closed_new  = broker.get_closed()
        │       diff vs last snapshot → events
        │       for each event: write NDJSON, maybe alert
        │
        ├── _JsonlWriter
        │     RotatingFileHandler(maxBytes=10MB, backupCount=10)
        │     7-day cleanup at start() + after each rollover
        │
        └── _Alerter
              if loss > risk.alert_loss_usd: WARNING log
              if SLACK_WEBHOOK_URL set: urllib POST (timeout=2s)
```

### Decisions

| # | Concern | Decision | Rationale |
|---|---|---|---|
| 1 | Log rotation | `logging.handlers.RotatingFileHandler(maxBytes=10*1024*1024, backupCount=10)` + 7-day cleanup pass at start + after rollover | Stdlib only; size cap exact; age cleanup cheap and lazy |
| 2 | State diffing | Snapshot-based: `dict[ticket, position_dict]` of last-seen positions; closes consumed from `broker.get_closed()` since last poll | Single source of truth; opened/modified/closed all derived in one place |
| 3 | Slack delivery | **DROPPED** — log-only alerts. No Slack integration, no env-var dependency. | User decision: simpler, zero external surface area |
| 4 | Module layout | `core/monitoring/__init__.py`, `core/monitoring/position_monitor.py`, `tests/test_position_monitor.py` | Mirrors existing `core/<bounded-context>/` layout |
| 5 | Config additions | `risk.alert_loss_usd: 50.0`, `monitoring.poll_interval_s: 5`, `monitoring.log_path: "logs/positions.jsonl"` | Existing `risk:` block extended; new `monitoring:` block for monitor-specific keys |
| 6 | `main.py` integration | Construct `PositionMonitor(broker, cfg)` only when `args.mode == "live"`; `start()` after `LiveBroker` init; `stop()` in `finally` | Mirrors existing `_start_autoresearch` thread pattern; zero changes to `while _running:` body |
| 7 | Test isolation | Inject `broker`, `clock`, and `slack_post_fn` (override `urllib`) via constructor with sensible defaults | Pure-unit testing without sleep/network |

### Trade-offs Accepted

- **Polling latency:** up to `poll_interval_s` (5s) between fill and operator notification.
- **Slack blocking:** alert path may block poller for up to 2s on slow Slack endpoint.
- **7-day cleanup is lazy:** runs at `start()` and after rotation, not on a wall-clock timer. Bounded by 10 MB cap.
- **Snapshot drift on crash:** restart re-emits current open positions as `opened` events (no monitor-state persistence). Documented behaviour.

### Open Question

- Flag naming: requirement says `--live`; existing CLI uses `--mode live`. Plan will gate monitor on `args.mode == "live"` (no CLI churn).

## Files Touched

| File | Action |
|---|---|
| `core/monitoring/__init__.py` | NEW (empty) |
| `core/monitoring/position_monitor.py` | NEW |
| `tests/test_position_monitor.py` | NEW |
| `config.yaml` | MODIFY — add `risk.alert_loss_usd` + `monitoring:` block |
| `main.py` | MODIFY — instantiate + start/stop monitor only in live mode |

## Acceptance Criteria Mapping

| AC | Component(s) |
|---|---|
| 1. Polling | `_PollerThread` |
| 2. NDJSON log | `_JsonlWriter` |
| 3. `[FILL]` stdout | `_Alerter.on_close()` |
| 4. Loss alert (log-only, no Slack) | `_Alerter.send()` |
| 5. Live-only, no main-loop change | `main.py` integration |
| 6. Unit tests | `tests/test_position_monitor.py` |
| 7. 308 tests stay green | Phase 4.5 full pytest run |

## User Approval

**APPROVED** — 2026-04-26. Decision 3 changed: Slack integration dropped; log-only alerts. Proceed to Phase 1.
