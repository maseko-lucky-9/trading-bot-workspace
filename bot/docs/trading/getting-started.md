---
title: Getting Started — Three Actionable Steps
last_updated: 2026-04-29
source: FX GOAT Mastery Compendium
---

# Getting Started — Three Actionable Steps

The compendium's §1 *Foundational Architecture* prescribes three concrete actions to begin the journey from beginner to professional. Do them in order; do not skip ahead to the bot or the strategy until each is complete.

## 1. Construct a Market Structure Map

Manually chart the last 30 days of EUR/USD on the Daily and 4-hour timeframes. Identify and annotate:

- Every confirmed swing high and swing low (use the `swing_left=2, swing_right=2` fractal definition from `core/strategy/structure.py` so your manual reading and the bot's algorithmic reading agree)
- Every break of structure event (close beyond a prior confirmed swing)
- The dominant trend label per FX GOAT's HH/HL → uptrend, LH/LL → downtrend, otherwise → range
- The unmitigated demand/supply zones nearest current price

Do not move on until the "break of structure" logic feels intuitive. The bot can compute these labels automatically (`classify_trend(swings)`), but the goal here is for *you* to internalise the read so your discretionary supervision of the bot is grounded.

## 2. Standardise Technical Confluences

Adopt FX GOAT's Lesson 4 ruleset for what counts as a "valid" setup. The bot's `TrendFollowing` strategy in standard mode encodes a minimal version: HTF bias agreement + lower-TF break of structure + ATR-derived structural stop + 1:2 reward-to-risk minimum. Any setup that does not satisfy *every* condition is not a setup — it is noise.

Standardisation eliminates subjectivity. The compendium's point is that consistent rules beat sporadic insight; the journal is what proves to you that you are following them.

## 3. Baseline Psychological Audit

Engage with FX GOAT Lesson 6 *Premium Psychology* before risking real capital. Identify your personal cognitive biases:

- Do you double-down on losers ("revenge trading")?
- Do you move stop losses to avoid the hit?
- Do you over-leverage on "sure things"?
- Do you abandon the system after a single loss?

The 24-hour cooling-off period (FX GOAT §4 *Pitfalls*) is the operator-side defence; the bot itself does not yet enforce a cooling-off window in code (see [drawdown-protocol.md](./drawdown-protocol.md) for the proposed future config flag).

Recognising your emotional triggers *before* risking capital is, per the compendium, the hallmark of the FX GOAT strategist.
