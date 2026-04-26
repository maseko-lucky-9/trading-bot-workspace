# Phase 0 — Intake Validation

**Run ID:** 20260426-position-monitor
**Date:** 2026-04-26
**Verdict:** PASS

## Normalised Requirement

**Feature:** Live order monitoring (`PositionMonitor`) for the MT5 trading bot.

**Problem:** After placing real orders through the MT5 bridge, the bot has no structured way to track open positions, log P&L as trades close, or alert the operator. Operator must manually inspect MT5.

## Acceptance Criteria

1. `PositionMonitor` polls bridge for open positions and closed-trade results at configurable interval (default 5 s).
2. Every position state change (opened, modified, closed) appended to rotating log file `logs/positions.jsonl` (NDJSON).
3. Position close → stdout summary: `[FILL] ticket=<n> symbol=<sym> profit=<$x.xx> at <ISO-timestamp>`.
4. P&L alert threshold: single-trade loss > `risk.alert_loss_usd` (default $50) → WARNING log + Slack POST (if `SLACK_WEBHOOK_URL` set).
5. Background thread inside `main.py`, gated by `--live` flag (current code: `--mode live`); no changes to main trading loop.
6. Full unit-test coverage: mock bridge, verify log output, verify alert threshold, verify Slack call (mocked).
7. All existing 308 tests remain green.

## Constraints

- No new runtime deps unless necessary (prefer stdlib `threading`, `logging`, `urllib.request`).
- Slack URL from env `SLACK_WEBHOOK_URL`; absent → skip silently.
- Log rotation: 7 days OR 10 MB max.
- `PositionMonitor` importable/testable in isolation (no import-time side effects).
- Do NOT modify `LiveBroker` public interface.

## Scope Decomposition Check

- Multiple subsystems: No — single new component inside existing bot process.
- Bounded contexts touched: 2 (new `core/monitoring/`, integration in `main.py`).
- Estimated tasks: 6-8.
- **dual_client:** `false`.
- **scope_warning:** `false`.

## Files Verified (Read this session)

- `/Users/ltmas/trading-bot-workspace/bot/main.py` — `--mode {paper,live}` flag; `_start_autoresearch` thread pattern.
- `/Users/ltmas/trading-bot-workspace/bot/core/execution/live_broker.py` — confirms `get_positions()`, `get_closed()`, `get_account()`.
- `/Users/ltmas/trading-bot-workspace/bot/core/bridge/http_client.py` — confirms bridge surface.
- `/Users/ltmas/trading-bot-workspace/bot/config.yaml` — `risk:` block ready to extend with `alert_loss_usd`.

## Spec Clarifications

1. **Flag naming**: requirement says `--live`; current code uses `--mode live`. Recommendation: gate monitor on `args.mode == "live"` (preserves existing CLI). Plan agent will confirm.
2. **Polling cadence default**: 5 s — confirmed.
3. **Log rotation**: stdlib only — design gate will choose between `RotatingFileHandler` + custom 7-day cleanup vs composite handler.

## Verdict

**PASS** — proceed to Phase 0.5 (Design Gate).
