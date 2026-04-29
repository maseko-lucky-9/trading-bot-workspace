---
title: Drawdown Protocol — Capital Preservation
last_updated: 2026-04-29
source: FX GOAT Mastery Compendium
---

# Drawdown Protocol — Capital Preservation

The compendium's §4 *Preservation During Drawdown* directs that during inevitable drawdowns the FX GOAT protocol dictates a reduction in **trade frequency** and **size**. This preserves both balance and "emotional equity" — the ability to remain calm and objective.

## The bot's tiered response

`RiskManager.preservation_factor(peak_equity, current_equity)` returns a multiplier in `[0.0, 1.0]` based on the live drawdown vs the configured trailing-DD thresholds:

| Drawdown vs peak | Tier | Multiplier | Operator action |
|---|---|---|---|
| 0 % to <10 % | green | 1.0 | normal sizing, normal frequency |
| ≥10 % | warn (`trailing_dd_warn`) | 0.5 | halve per-trade volume; consider pausing fresh entries until trend re-asserts |
| ≥15 % | reduce (`trailing_dd_reduce`) | 0.25 | quarter sizing; fresh entries only on Premium-grade setups |
| ≥20 % | halt (`trailing_dd_halt`) | 0.0 | no new entries; manage existing positions to flat |

The multiplier is *available* to consumers but not yet *automatically applied* by `size_position` — by deliberate design, so the existing Kelly-sized risk-manager tests remain green and consumers opt in. Wire it into your discretionary sizing decisions immediately; the bot's automatic consumption is a roadmap item.

## The 24-hour cooling-off rule

The compendium's §4 *Pitfalls* table identifies revenge trading after a loss as a top professional countermeasure target. The remedy named in Lesson 6 is a **mandatory 24-hour cooling-off period** after any stop-out exceeding a threshold — typically the per-trade risk cap (1–2 %).

> **Current bot behaviour:** the cooling-off rule is enforced operator-side only. There is no `risk.cooling_off_hours` config flag wired into the runtime today. A future enhancement (`risk.cooling_off_hours: 24`) will block fresh order placement until the configured window has elapsed since the last stop-out. Until that lands, the rule is a manual checkbox on [daily-prep-checklist.md](./daily-prep-checklist.md).

## Frequency reduction beyond size

Reduction in *frequency* (the compendium's word) is the under-appreciated half. Sizing every trade smaller while still taking 10 trades a day still produces 10 trades' worth of slippage, spread cost, and emotional load. The protocol asks for fewer trades, not just smaller ones. In practice during a drawdown:

- Skip Standard-mode setups; trade only Premium setups
- Skip secondary pairs (when the bot's `bot.instruments:` list eventually expands); trade only EUR/USD
- Skip the New York open if the London session was a stop-out

## Recovery criterion

Promote the multiplier back up only when the drawdown has shrunk to *below* the prior tier's threshold for at least one full trading week. Crossing the threshold momentarily on a single winning trade is not promotion. The compendium's pacing intentionally lags the equity curve to avoid whip-sawing position size against random week-to-week variance.

## Anti-patterns during drawdown

- Increasing size to "make it back faster" — explicit compendium violation; statistically suicidal.
- Lowering the per-trade-risk cap *and* increasing trade frequency in the same week — defeats the frequency-reduction half of the protocol.
- Disabling the bot's regime filter to "find more setups" — same anti-pattern in code form.
