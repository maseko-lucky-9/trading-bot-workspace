---
title: Step-by-Step Trade Review Process
last_updated: 2026-04-29
source: FX GOAT Mastery Compendium
---

# Step-by-Step Trade Review Process

A systematic post-trade audit. Used during the Saturday performance audit (per [weekly-routine.md](./weekly-routine.md)) and immediately after any single trade that materially deviated from expectation.

## Step 1 — Reconstruct the entry

Open the chart at the bar where the entry fired. Annotate the structural read you had at entry time, *not* with the benefit of hindsight. The bot's `logs/trades.csv` and the per-trade journal entry should agree on entry price, time, and the technical thesis from [journal-template.md](./journal-template.md).

## Step 2 — Re-derive the stop-loss thesis

Was the stop placed where the technical thesis was *invalidated*? Or was it placed at an arbitrary pip distance to "feel safer"? The compendium is explicit (§4 *Essential Risk Management Rules*): the stop is a thesis-invalidation marker, not a comfort dial. A hit stop is a "business expense", not a failure of the trade.

## Step 3 — Walk forward to the exit

Replay the bars from entry to exit. For every meaningful structural event (new swing, break of structure against you, return to entry zone) ask: did the rules say to act, hold, or trail? The compendium's 4-phase walkthrough (§3, mirrored in [simulated-trade-walkthrough.md](./simulated-trade-walkthrough.md)) is your reference.

## Step 4 — Categorise the outcome

Four buckets:

1. **Won + followed rules** — the only category that compounds. Add the trade to the win-streak tally for the success-timeline milestone (1:2 R:R × 20 consecutive trades — see [success-timeline.md](./success-timeline.md)).
2. **Won + broke rules** — the dangerous bucket. Outcome was lucky; behaviour is unsound. Do not let the P&L mask this.
3. **Lost + followed rules** — neutral. Pay the business expense, log the lesson, move on.
4. **Lost + broke rules** — the diagnostic bucket. Walk back through the journal entry's "Emotional state" field; this is where pattern-of-error appears.

## Step 5 — Update the journal

Add one explicit sentence to the journal's "Lesson learned" field. Even if the trade was textbook-perfect, the lesson can be "system worked exactly as designed; nothing to change". Recording the ratio of cat-1 to the other three over time is the single most valuable measurement you can make.

## Step 6 — Roll into the weekly reflection

If the categorisation was 2 or 4 (rules broken), tag the trade for the Saturday weekly reflection. The reflection's leading question is "Did I follow my rules?" — not "Did I make money?" — so cat-2 (won-but-broke-rules) trades count as failures that must be addressed.
