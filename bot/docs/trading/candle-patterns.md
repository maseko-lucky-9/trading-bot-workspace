---
title: Candle-Pattern Reading Guide
last_updated: 2026-04-30
source: FX GOAT Mastery Compendium
---

# Candle-Pattern Reading Guide

Operator companion to the bot's mechanical signal layer. The FX GOAT compendium (§3 *Strategic Entry Methods*) names "specific candlestick triggers" as a Premium-execution requirement; this doc defines the patterns you will see, how to read each one, and which ones the code currently filters on.

## Anatomy of a Candle

Every candle on an OHLC chart encodes four price points across one bar of time:

- **Open** — first traded price in the bar
- **High** — highest traded price in the bar
- **Low** — lowest traded price in the bar
- **Close** — last traded price in the bar

The **body** is the rectangle between Open and Close (filled / coloured one way for bearish, the other for bullish). The thin lines extending above and below the body to the High and Low are the **upper tail** and **lower tail** (also called wicks or shadows). The full vertical distance from High to Low is the bar's **range**. Body-to-range ratio and tail-to-body ratio are the two measurements that classify nearly every candlestick pattern in this guide.

## Doji

A Doji has Open ≈ Close — the body is essentially zero relative to the bar's range. Both tails are visible. Visually it looks like a cross or a plus sign.

A Doji says the buyers and sellers fought to a standstill in this bar — momentum has stalled. After a strong trend, a Doji at a structural level often warns of an impending pullback or reversal. **The bot does not trade Dojis directly**, but the pin-bar filter explicitly rejects them (`body / range > 1/3` requirement) so a Doji at the BoS bar will not pass the Premium gate.

## Marubozu

A Marubozu is the opposite extreme — the body fills the entire range. Open == Low (bullish) or Open == High (bearish), and Close == the other extreme. No tails.

A Marubozu signals strong, decisive momentum in the body's direction with no opposition. Useful for confirming impulse legs, *not* for entry confirmation. **The bot's pin-bar filter explicitly rejects Marubozu candles** (zero tail fails `tail_ratio_min`); the marubozu-rejection unit test in `tests/strategy/test_trend_following.py` proves the gate fires.

## Hammer / Hanging Man

A Hammer has a small body in the **upper third** of the range, a long **lower** tail (≥ 2× body), and little-to-no upper tail. Same shape, two names depending on context: at the bottom of a downtrend it's a *Hammer* (bullish reversal candidate); at the top of an uptrend it's a *Hanging Man* (bearish reversal candidate).

The Hammer/Hanging Man is mechanically the same as a bullish/bearish **pin bar** — see below. The bot's `is_pin_bar(direction="bullish")` returns True on a Hammer that closes above the prior bar's close.

## Engulfing (Big Shadow)

An Engulfing pattern is a two-bar setup. The current bar's body **completely engulfs** the prior bar's body in the opposite direction: a bullish engulfing has a green body that opens below the prior red body's close and closes above its open. Bearish engulfing is the inverse.

Walter Peters calls this the **"Big Shadow"** in *Naked Forex* Ch 6: a strong reversal trigger when it appears at a structural level. **Big Shadow is on the future-roadmap path** — the bot does not yet detect engulfing patterns; only the pin-bar trigger from Ch 8 is mechanised. Read engulfings manually for now; they're a stronger signal than a pin in volatile conditions.

## Pin Bar (Kangaroo Tail)

A Pin Bar has a small body in the upper or lower third of the range, a long tail (≥ 2× body) in the **opposite** direction (rejection of price away from the body), and a close that breaks against the rejected move. Walter Peters' *Naked Forex* Ch 8 calls this the **"Kangaroo Tail"**.

The bot mechanises this pattern in `core/strategy/candles.py:is_pin_bar`. The canonical Naked Forex definition uses `tail_ratio_min=2.0` (tail must be at least twice the body); the function default is 2.0, but the strategy call site in `TrendFollowing.generate_signal` passes `tail_ratio_min=1.5` because the Fib zone + pin AND-gate is otherwise impossibly restrictive on small data windows. The 2.0 strict variant remains available for callers who want stricter filtering.

In Premium mode the pin must close beyond the prior bar's close in the trade direction (bullish pin closes above prior close; bearish pin closes below). This is the `prior_close` argument to `is_pin_bar`.

## How the bot uses these signals

Today, the **only** candlestick pattern the strategy code consults is the **pin bar** (`is_pin_bar_at`), and **only in Premium mode**. Standard mode emits a signal as soon as HTF bias agrees with the M15 break of structure — no candle gate. Premium mode adds two more confluences: the latest close must sit inside a 0.618–0.786 Fib retracement of the most recent impulsive leg (proxy for an unmitigated demand/supply zone), AND the trigger bar must be a pin in the direction of the BoS.

When you trade manually alongside the bot, treat the pin-bar gate as a *floor*, not a ceiling — Engulfing / Big Shadow / Hammer at structural levels are all valid additional confluences that the code does not yet check. Note them in [journal-template.md](./journal-template.md) so the gap between operator skill and code coverage stays visible. The roadmap items captured in `bot/pipeline/build-summary.md` include adding engulfing and demand-zone detection to close that gap.

## Quick-reference table

| Pattern | Body | Tails | Bot filter today |
|---|---|---|---|
| Doji | ≈ 0 | both visible | rejected by pin gate |
| Marubozu | full range | none | rejected by pin gate |
| Hammer (bottom) | small, upper third | long lower | accepted as bullish pin |
| Hanging Man (top) | small, upper third | long lower | accepted as bullish pin (only useful when context says bearish) |
| Bullish pin / Kangaroo Tail | small, upper third | long lower | **gate fires Premium BUY** when in Fib zone + HTF up |
| Bearish pin / Kangaroo Tail | small, lower third | long upper | **gate fires Premium SELL** when in Fib zone + HTF down |
| Bullish engulfing / Big Shadow | engulfs prior red body | varies | not detected (roadmap) |
| Bearish engulfing / Big Shadow | engulfs prior green body | varies | not detected (roadmap) |

The roadmap line above is the actionable summary: every "not detected" cell is a follow-up Ralph cycle waiting for the data and the operator hours.
