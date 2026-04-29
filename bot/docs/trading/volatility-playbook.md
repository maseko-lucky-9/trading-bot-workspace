---
title: High-Volatility Day Playbook
last_updated: 2026-04-29
source: FX GOAT Mastery Compendium
---

# High-Volatility Day Playbook

The compendium's §5 *Daily Routine* item 4 specifies the volatility-preparation contract: on news-impact days, either stay out or reduce risk by 50 %. This doc is the explicit decision tree.

## Inputs to the decision

- **Economic calendar**: scan for tier-1 events (NFP, CPI, FOMC, ECB, BoE, BoJ rate decisions, GDP prints, US PMI flash, retail sales) within the active session
- **Surprise potential**: consensus-vs-prior delta — wider ⇒ higher implied vol
- **Pair sensitivity**: EUR/USD reacts to US and Eurozone tier-1; check the calendar for both currencies
- **Currently open positions**: a runner from yesterday is *not* the same exposure as a fresh entry today

## Decision tree

### Is there a tier-1 event in the next 4 hours on either currency in the pair?

- **No** — proceed with the normal [daily-routine.md](./daily-routine.md). No special action.
- **Yes** — go to the next branch.

### Are you in a current position?

- **No** — apply pre-event posture:
  - **Default**: stay out for ±30 minutes around the release.
  - **Acceptable alternative**: reduce per-trade risk by 50 % (e.g. drop `risk.max_risk_per_trade` from 1 % to 0.5 % for the session) AND tighten the structural stop by widening the ATR buffer to absorb the spike.
- **Yes** — manage existing exposure:
  - If trade is already in profit and stop is at breakeven: hold; partial-out is acceptable.
  - If trade is at risk (still inside the original SL distance): close half. Let the other half run with a tighter stop.
  - Never widen a stop to "give the trade room" through an event. Widening stops is the textbook compendium pitfall (§4 *Pitfalls*: "Moving stop losses to avoid a loss").

### After the event — re-engagement criteria

Wait until **at least three M15 bars** have closed and the new structural read is unambiguous before placing fresh orders. If structure is still chaotic, sit out the rest of the session.

## Bot-side guardrails

- The bot has `filters.news_blackout` available in `config.yaml` (currently `enabled: false` because the calendar feed is not hooked up). When you do hook it up, set `buffer_minutes: 30` and provide a CSV at the configured `calendar_path`. Until that work is done, news avoidance is operator-side.
- `RiskManager.preservation_factor` is sized-driven, not event-driven; it does not know about the calendar. Use it in conjunction with this playbook, not as a replacement.

## Anti-patterns

- "I'll just trade smaller" without an explicit halving rule — vague intentions reliably turn into full-size trades when the chart looks "obvious".
- Watching the release live and entering on the first impulse bar — that is gambling on direction, not trading the structural read.
- Skipping the journal entry because "it was a news day" — the journal entry on a skipped day is "skipped per playbook; here is the rule that fired".
