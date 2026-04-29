---
title: Simulated Trade Walkthrough
last_updated: 2026-04-29
source: FX GOAT Mastery Compendium
---

# Simulated Trade Walkthrough

A faithful reproduction of the compendium's §3 *Simulated Trade Walkthrough* — the four-phase process from trend identification to exit.

> **Current bot behaviour**: v1 closes the full position at the 1:2 marker; partial-fill, BE-trail, and HTF-target are roadmap items. The book's process below is what an *operator* should follow; the bot's `TrendFollowing` strategy currently emits a single `Signal` with a fixed `tp = entry + 2.0 × risk`. When you walk through a real trade in the journal, evaluate it against the **book's** rubric, not the bot's.

## Phase 1 — Trend Identification

Using the FX GOAT Lesson 4 framework, identify a clear bullish structure on the **Daily** chart. We are looking for *Higher Highs* and *Higher Lows*. Annotate the most recent confirmed swing high and swing low. The bot's `core/strategy/structure.py:classify_trend` returns `"uptrend"` for the same input; if your manual read disagrees, you have either misread the swings or chosen a different swing-confirmation window — investigate before continuing.

## Phase 2 — Setup Recognition

Wait for price to return to a *Premium* technical zone — a major demand zone or a Fibonacci retracement (0.618–0.786) of the most recent impulsive leg, identified per Lesson 5. The bot's `TrendFollowing(mode="premium")` uses the Fib retracement as its proxy for this zone; the compendium's broader definition (unmitigated supply/demand area, liquidity sweep) is more nuanced and is currently approximated. When trading manually, prefer the broader read; when supervising the bot, accept the proxy and inspect false signals during the Saturday review.

## Phase 3 — Entry Execution

On the **15-minute** timeframe, wait for a *Shift in Market Structure* — i.e. price breaks a short-term high to the upside, confirming continuation. Enter the trade on or shortly after the close of the breakout bar. **Stop loss goes below the structural low** — the level at which the technical thesis is invalidated. This is the compendium's non-negotiable rule from §4: stop placement is thesis-driven, not pip-distance-driven.

The bot's `last_break_of_structure` helper in `core/strategy/structure.py` detects this event programmatically; the `TrendFollowing` strategy combines it with the HTF bias gate to emit the entry signal.

## Phase 4 — Trade Management and Exit

The compendium prescribes a **three-step exit**:

1. Take **partial profits** at a 1:2 reward-to-risk ratio.
2. Move the stop loss to **breakeven** on the runner.
3. Let the runner pursue a **higher-timeframe target** — typically the next prior swing high on the Daily chart, or the next Fibonacci extension.

> **Current bot behaviour**: v1 closes the full position at the 1:2 marker; partial-fill, BE-trail, and HTF-target are roadmap items. The single-leg `OrderManager` does not yet support partial-fill or trailing stops in code. Until that lands, the bot captures the partial-take-1:2 step only — the BE-trail and HTF-target are operator-side actions if you are running discretionary alongside the bot.

## After the trade

Log the trade per [journal-template.md](./journal-template.md). Walk it through [trade-review-process.md](./trade-review-process.md). Roll any rule violations into the Saturday [weekly-reflection.md](./weekly-reflection.md).
