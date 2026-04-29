---
title: Growth Roadmap — Beginner to Advanced
last_updated: 2026-04-29
source: FX GOAT Mastery Compendium
---

# Growth Roadmap — Beginner to Advanced

The compendium (§6 *Month One Growth Plan*) lays out a four-week structured progression. The bot accelerates the technical execution but does not replace the educational compounding — both must happen in parallel.

## Week 1 — Foundations

Complete FX GOAT Lessons 1 & 2. Draft the Market Structure Map for EUR/USD per [getting-started.md](./getting-started.md). The bot's `tests/strategy/test_structure.py` shows you exactly what swings, classifications, and breaks of structure look like on synthetic frames; reproduce the same patterns by hand on real charts.

## Week 2 — Standardisation

Study FX GOAT Lessons 3 & 4. Begin identifying *Standard* setups (the bot's `TrendFollowing(mode="standard")`) on demo using the Full Technical rules. Run the bot through `python backtest/engine.py --params autoresearch/params.trend.yaml --symbol EURUSD --timeframe M15 --bars 5000 --guard` to see how the same rules score historically. Walter Peters' *Naked Forex* (Ch 3 *Back-Testing Your System*) names the three goals of the exercise: validate the edge, build conviction in execution, and refine rules where the data shows persistent bias.

## Week 3 — Refinement

Integrate FX GOAT Lesson 5 *Premium Technical Analysis*. Switch the bot's strategy parameter to `mode: "premium"` (in your *backtest* params overlay only — never in the locked `autoresearch/params.yaml`) and re-run the engine. The strike rate should improve materially; watch the trade count drop. The compendium's Premium criteria are intentionally restrictive — fewer trades, higher quality.

## Week 4 — Psychological Alignment

Implement FX GOAT Lesson 6. Open a live journal per [journal-template.md](./journal-template.md) with strict focus on routine and risk management. Run the daily and weekly routines for a full week with no rule violations. Only when this baseline is reached do you graduate to Phase 2 of the [success-timeline.md](./success-timeline.md).

## After Month One

The compendium's three success phases (0–30 days education, 30–90 days consistency, 90+ days professionalism) are gated by demonstrable consistency, not by clock time. Do not advance phase boundaries because the calendar says so — advance them because the data says so.
