---
title: Daily Market-Prep Checklist
last_updated: 2026-04-29
source: FX GOAT Mastery Compendium
---

# Daily Market-Prep Checklist

A binary, do-it-or-don't checklist that must be cleared before the bot is allowed to trade or before you take a discretionary entry. Derived from FX GOAT §5 *Daily Routine* and the compendium's emphasis on data-driven over impulsive decisions.

## Pre-flight (≥30 minutes before London open)

- [ ] Bot launchd agent loaded and `paper.log` shows `bot start mode=paper symbols=EURUSD`
- [ ] `curl http://192.168.64.1:8080/state | jq .tick.symbol` returns `"EURUSD"` (per the resilience PR's required operator action)
- [ ] EA on the UTM Windows VM is running and connected; MarketWatch shows EUR/USD subscribed
- [ ] No high-impact news within the next 4 hours, OR a volatility-mitigation plan is in place per [volatility-playbook.md](./volatility-playbook.md)

## Structural alignment

- [ ] Daily chart structure confirmed: uptrend / downtrend / range
- [ ] 4-hour chart structure agrees with Daily (or, if disagreement, you have explicitly decided which timeframe wins)
- [ ] Most recent break of structure annotated; level recorded
- [ ] Nearest unmitigated demand/supply zone annotated

## Risk parameters

- [ ] Account drawdown checked — if you are inside `trailing_dd_warn` (10 %), `RiskManager.preservation_factor` returns 0.5 and your sizing should already be halved
- [ ] Per-trade risk limit (max 1–2 % of account) verified against the live equity figure, not the launch-time figure
- [ ] Daily-loss circuit breaker confirmed at 2 % (`risk.daily_loss_limit`) — trip means stop trading until tomorrow

## Mental state

- [ ] No revenge-trading impulse from yesterday's outcome (24-hour cooling-off rule per [drawdown-protocol.md](./drawdown-protocol.md))
- [ ] You are not chasing — bias is the structural read, not the immediate price action

## Bot readiness

- [ ] `pytest -q` last run was green (locked-file canaries in `tests/test_oos_locks.py` are part of the suite)
- [ ] `autoresearch.enabled` is `false` (locked while the OOS window is open)
- [ ] Latest `logs/health.jsonl` entry is `bridge: ok`, `ea_connected: true`, `bot: running`

If any item is unchecked, do not trade until it is.
