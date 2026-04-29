---
title: Risk Rules — Consolidated Rule Sheet
last_updated: 2026-04-29
source: FX GOAT Mastery Compendium
---

# Risk Rules — Consolidated Rule Sheet

The single, canonical rule sheet for trading this account. Drawn from FX GOAT compendium §4 *Essential Risk Management Rules*, §4 *Pitfalls and Professional Countermeasures*, and §7 *Vital Rules for Long-Term Success*.

## The Three Vital Rules (FX GOAT §7)

These three rules are the operating contract. Any violation invalidates the trade regardless of outcome.

1. **Structure is Absolute.** Never trade against the higher-timeframe market structure. The bot's `TrendFollowing` strategy enforces this through the H4 bias gate; manual trades must apply the same discipline.
2. **Capital is Life.** Your stop loss is your best friend; it keeps you in the game. A hit stop is a "business expense", not a failure of the trade.
3. **Discipline is the Edge.** Your routine separates you from the 95 % of retail traders who treat the market like a casino. Every doc in this directory exists in service of that routine.

## Essential Risk Management Rules (§4)

- **Strict stop-loss placement.** Always set stops at levels where the technical thesis is invalidated. Prevents stop-hunts from prematurely closing a viable trade and protects against catastrophic moves. The bot places ATR-buffered structural stops; manual entries follow the same logic.
- **Fixed risk percentage.** Never risk more than **1–2 %** of account equity on any single trade. Consistency is born from survivability. The bot's `risk.max_risk_per_trade` defaults to 0.01 (1 %); raise to 0.02 only after the [scaling-strategies.md](./scaling-strategies.md) gates have cleared.
- **Capital neutrality.** Treat every trade as an independent statistical event. Winning or losing the current trade has no bearing on the validity of the next setup.

## Compendium-vs-Code Caveat — Premium-zone definition

The compendium defines a *Premium* technical zone as an "unmitigated supply/demand area" with a confluence of timeframe alignment, **liquidity sweeps**, and specific candlestick triggers. The bot's `TrendFollowing(mode="premium")` simplifies this to the Fibonacci 0.618–0.786 retracement of the most recent impulsive leg — a well-understood proxy that captures part but not all of the compendium's criteria.

When trading manually, prefer the broader read (look for unmitigated zones and liquidity sweeps; the Fib retracement zone is one of several inputs, not the whole filter). When supervising the bot, accept the proxy and inspect the false-signal rate in the Saturday review; persistent miss patterns are the input to a future strategy refinement.

## Pitfalls and Professional Countermeasures (§4)

| Common Pitfall | Professional Countermeasure |
|---|---|
| Over-leveraging on a "sure thing" | Adhere to the 1–2 % risk protocol regardless of confidence level |
| Moving stop losses to avoid a loss | Accept the hit. A hit stop loss is simply a "business expense" |
| Revenge trading after a loss | Implement a mandatory 24-hour cooling-off period (Lesson 6) — operator-side until `risk.cooling_off_hours` is wired into the runtime; see [drawdown-protocol.md](./drawdown-protocol.md) |

## Bot-side guardrails currently active

- `risk.max_risk_per_trade` cap
- `risk.daily_loss_limit` (2 %) hard stop
- `risk.trailing_dd_warn` / `_reduce` / `_halt` thresholds (10/15/20 %)
- `RiskManager.preservation_factor` available for opt-in size halving on drawdown
- T08 lock canaries — `tests/test_oos_locks.py` fails the suite if `params.yaml`, `ema_crossover.py`, `mean_reversion.py`, or the `autoresearch.enabled: false` flag are changed

## What is NOT yet enforced in code

- Cooling-off period after a stop-out (operator checklist only)
- Automatic preservation-factor consumption inside `size_position` (must be applied by the caller)
- News-blackout filter (`filters.news_blackout.enabled: false` until calendar feed is wired)

If you find yourself wanting to "just override one rule this once", read this document again. Every rule on this sheet exists because someone — the compendium authors, the bot author, or your past self — paid for it in losses.
