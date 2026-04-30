---
title: Trend-Reversal Practice Exercise
last_updated: 2026-04-30
source: FX GOAT Mastery Compendium
---

# Trend-Reversal Practice Exercise

The FX GOAT compendium (§3 *Trend Reversal Practice Exercise*) prescribes a 20-instance training drill: identify 20 historical instances where a trend failed and record what the chart looked like before, during, and after the change. The aim is to train the eye to recognise that a trend has lost momentum *before* the equity curve says so.

This doc packages the drill as a repeatable, journal-friendly procedure.

## Why this exercise matters

The compendium is blunt about why traders fail to recognise reversals: ego refuses to admit a trend has changed, and they hold losers as the new direction develops. The 20-instance exercise is purely diagnostic — it has nothing to do with placing live trades. By forcing yourself to scroll back through 20 historical reversals on EUR/USD M15 and Daily charts, you build a visual library of "what the moment of failure looks like." That library is what lets you, in real time, compare the live chart to the catalogue and act on conviction rather than hope.

There is also a code-side reason this matters. The bot's `TrendFollowing` strategy implements a structural reversal short-circuit (`reversal_lookback`); when its detection fires, you'll want to recognise the same pattern by eye and decide whether to override. You can't override what you don't recognise.

## How to source 20 historical instances

Use real historical data, not synthetic. The bot's M15 cache (`bridge_data/history/EURUSD_M15.parquet`, ~3683 bars after the 2026-04-29 yfinance backfill) plus the Daily cache covers the recent past well. Open the parquet in any chart tool (TradingView, MetaTrader on the VM, Python plotting in a Jupyter notebook) and scroll back at least 6 months on Daily, 2 months on M15. Tag each candidate reversal with its date and the timeframe you spotted it on.

Look for **structural** reversals — sequences of higher-highs / higher-lows that transition into lower-highs / lower-lows (or vice versa). A single deep retracement that recovers is *not* a reversal; that's a pullback. The chapter's wording is "the trend has lost momentum" — the new direction must put in at least one confirmed swing in the opposite direction before you count it.

Spread your 20 instances across different volatility regimes if possible. Reversals during news events look different from reversals during quiet sessions, and you want exposure to both.

## The drill (per-instance steps)

For each of the 20 instances:

1. **Pick the chart.** Open the chart, navigate to the candidate reversal date, and zoom out so you can see at least 30 bars before and 30 bars after.
2. **Mark prior swings.** Use the same `swing_left=2, swing_right=2` fractal definition the bot uses (`core/strategy/structure.py:detect_swings`). Annotate the last three confirmed swing highs and last three confirmed swing lows on the in-trend side.
3. **Annotate the reversal.** Identify the bar where the trend's higher-low (or lower-high) failed — i.e. the first bar that closed beyond the prior structural pivot in the new direction. Mark it. This is the *structural break* on the reversal side.
4. **Capture the candle.** Look at the bar that delivered the structural break and the 2–3 bars surrounding it. Was there a Pin Bar (Kangaroo Tail)? An Engulfing / Big Shadow? A Doji that preceded the break? Record the pattern from [candle-patterns.md](./candle-patterns.md). Most reversals in the compendium have a recognisable trigger candle at or near the failure point.
5. **Log the outcome.** How far did the new trend run before its first pullback? How long did the reversal take to confirm (in bars)? Write a one-paragraph description of what the moment looked like in your own words.

Use one entry per instance in `journal/practice/reversals/YYYY-MM-DD-NNN.md`. By the time you finish all 20, you'll have a personal catalogue of reversal patterns you trust.

## Bot-side reference

The strategy's reversal detection lives in `core/strategy/trend_following.py`. The relevant logic checks the `reversal_lookback` window (default 10 HTF bars); if the most recent HTF trend classification differs from the prior dominant one, the strategy emits HOLD with reason `structural_reversal_in_progress` rather than firing a fresh entry signal in the new direction. That's the code's way of saying "I see a reversal forming — don't trade the freshly-broken direction yet, wait for it to stabilise."

When you do this exercise manually, you're training yourself on the same signal the bot uses. Your annotations from step 3 (structural break) and step 4 (candle pattern) are exactly the inputs the strategy considers. If you and the strategy disagree on whether a given moment is a "real" reversal, the disagreement is data — it tells you either (a) the strategy's `reversal_lookback` window needs a tune, or (b) your eye is over-reading noise. Both are valuable lessons.

## Reflection template

After all 20 instances, write a one-page reflection in `journal/practice/reversals/REFLECTION.md` answering:

- Which patterns showed up most often? (Pin / engulfing / doji-then-break / clean break with no special candle?)
- How many of your 20 had a Premium-zone setup (Fib retracement or unmitigated demand/supply) the bot's code would have caught?
- How many reversed quickly versus those that took 5+ bars to confirm? Did the slow-confirm ones share a common feature (e.g. lower volatility, news-event lull)?
- For the 5 most striking reversals, capture a screenshot and paste into the reflection. Visual recall is the entire point.

The reflection feeds into your weekly Saturday audit ([weekly-routine.md](./weekly-routine.md)). Re-do this exercise quarterly — the market changes, your eye should keep up.
