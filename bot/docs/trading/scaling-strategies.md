---
title: Scaling Strategies
last_updated: 2026-04-29
source: FX GOAT Mastery Compendium
---

# Scaling Strategies

The compendium (§6 *Scaling Strategies*) distinguishes between *conservative* and *aggressive* scaling. Conservative is the default; aggressive is reserved for explicitly identified setups.

## Conservative Scaling — the default

Increase position size by **10 %** only after a month of consistent profitability. "Consistent" is defined by the [success-timeline.md](./success-timeline.md) Phase-2 streak — 20 consecutive 1:2 R:R rule-respecting trades — *and* a Saturday weekly reflection clean of rule violations across each of the four weeks.

In the bot, this maps to incrementing `risk.max_risk_per_trade` from 0.01 (1 %) toward 0.012 (1.2 %), or equivalently lifting the `kelly_fraction` cap by a small amount. **Never** increase risk into a drawdown; the `RiskManager.preservation_factor` tiers (warn 0.5×, reduce 0.25×, halt 0.0×) override scaling targets. Recovery to flat-or-better is the precondition, not the calendar.

## Aggressive Scaling — the exception

Reserved for high-conviction *Premium* setups where multiple timeframes align perfectly with institutional liquidity. The compendium is explicit that this is **not** a routine size — it is opt-in per setup, justified by the analysis, and recorded in the journal alongside the entry.

Operationally:

- Daily and 4-hour structures both confirm the trend direction
- Price has retraced to an unmitigated demand/supply zone (or to the bot's Fib 0.618–0.786 proxy for the same)
- Lower-TF confirmation (M15 break of structure) is fresh, not lagging
- No high-impact news inside the holding window

Even in aggressive mode, the per-trade risk cap (1–2 %) is non-negotiable. "Aggressive" refers to taking the trade at all, not to over-leveraging once in.

## What scaling is not

- Doubling down on losers ("revenge trading"). Per FX GOAT §4 *Pitfalls* the countermeasure is the 24-hour cooling-off period, documented in [drawdown-protocol.md](./drawdown-protocol.md).
- Increasing size after a single big winner. The Phase-2 streak metric is what unlocks the next conservative-scale tier.
- Adding to a winning position without a fresh, independent setup. Each leg must satisfy entry rules from scratch.

## Pace check

If you have more than two losing weeks in a calendar month, you do not scale up that month — regardless of cumulative P&L. The compendium frames the market as a high-performance business; in a business, you do not invest more capital into a process that recently underperformed without a documented hypothesis for the divergence.
