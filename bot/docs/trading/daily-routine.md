---
title: Daily Trading Routine
last_updated: 2026-04-29
source: FX GOAT Mastery Compendium
---

# Daily Trading Routine

Derived from FX GOAT Mastery Compendium §5 *Operational Excellence: Routines, Schedules, and Psychology*. The routine creates a professional environment where decisions are made from pre-defined data rather than impulsive reactions to price movement.

## 1. Global Macro Review

Before the bot or your manual session opens, scan the economic calendar for high-impact news on the active session pairs (EUR/USD currently). Look specifically for NFP, CPI, central-bank rate decisions, and unscheduled geopolitical events. Anything classified as "high-impact" within the next 4 hours either suspends trading or downsizes risk per the *Volatility Preparation* step below.

## 2. Market Structure Update

Update structural levels on the 4-hour and Daily charts. Mark the dominant trend (HH/HL bullish, LH/LL bearish, or range), the most recent break of structure, and the unmitigated demand or supply zones nearest to current price. The bot's `core/strategy/structure.py` exposes the same swing-point logic for programmatic use; manual annotation is for your discretionary read of conviction.

## 3. Daily Preparation Checklist

Confirm directional bias and identify the *Kill Zones* — high-volatility windows when institutional liquidity drives the largest moves. The compendium's "Kill Zones" map directly to the bot's existing `filters.sessions: ["london", "new_york"]` (07:00–16:00 UTC and 12:00–21:00 UTC respectively). Trades attempted outside these windows are filtered out of the bot's signal stream by design. Use [daily-prep-checklist.md](./daily-prep-checklist.md) for the explicit pre-flight items.

## 4. Volatility Preparation

If a high-impact news event is scheduled inside the active session, you have two valid responses:

- **Stay out of the market** for ±30 minutes around the release.
- **Reduce risk by 50 %** by halving the position-size multiplier (or by setting `RiskManager.preservation_factor`-aware sizing, when that path is wired into `size_position` — currently the multiplier is read but consumers must opt in).

See [volatility-playbook.md](./volatility-playbook.md) for the full event-day decision tree.
