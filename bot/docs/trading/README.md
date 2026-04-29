---
title: Operator Trading Documentation — Index
last_updated: 2026-04-29
source: FX GOAT Mastery Compendium
---

# Operator Trading Documentation — Index

Documentation derived from the [FX GOAT Mastery Compendium](https://example.invalid/) and applied to the MT5 trading bot. Every doc in this directory is *operator-facing* — it describes the human routines, checklists, journaling templates, and growth structures the bot complements but cannot replace.

The bot's job is execution; your job is supervision, audit, and the slow compounding of the framework. These docs are how that division of labour holds up over months.

## Daily

- [daily-routine.md](./daily-routine.md) — the four-item daily rhythm: macro review, structure update, prep checklist, volatility prep
- [daily-prep-checklist.md](./daily-prep-checklist.md) — explicit binary checklist; nothing trades until every item clears
- [volatility-playbook.md](./volatility-playbook.md) — high-impact-news decision tree

## Weekly

- [weekly-routine.md](./weekly-routine.md) — Sunday outlook → Mon-Fri execution → Friday close → Saturday audit
- [weekly-reflection.md](./weekly-reflection.md) — the leading question is "Did I follow my rules?" — not "Did I make money?"

## Per-trade

- [journal-template.md](./journal-template.md) — every trade records technical reason, emotional state, lesson learned
- [trade-review-process.md](./trade-review-process.md) — six-step post-trade audit
- [simulated-trade-walkthrough.md](./simulated-trade-walkthrough.md) — the compendium's four-phase walkthrough applied to a real trade

## Strategy progression

- [getting-started.md](./getting-started.md) — three actionable steps to begin: structure map, standardise confluences, psychological audit
- [growth-roadmap.md](./growth-roadmap.md) — the compendium's Month-One four-week plan
- [success-timeline.md](./success-timeline.md) — Phases 1–3 with the 1:2 R:R × 20 consecutive trades graduation gate

## Risk and capital

- [risk-rules.md](./risk-rules.md) — consolidated rule sheet; the Three Vital Rules + Essential Risk Management + Pitfalls
- [drawdown-protocol.md](./drawdown-protocol.md) — tiered preservation response + 24-hour cooling-off rule
- [scaling-strategies.md](./scaling-strategies.md) — conservative 10 %-per-month default, aggressive Premium-only exception

## Reading order for new operators

1. Read [getting-started.md](./getting-started.md) and complete the three actionable steps before you do anything else.
2. Read [risk-rules.md](./risk-rules.md) and internalise the Three Vital Rules.
3. Read [growth-roadmap.md](./growth-roadmap.md) and follow the four-week structure.
4. Once you reach Week 4, adopt [daily-routine.md](./daily-routine.md), [daily-prep-checklist.md](./daily-prep-checklist.md), and [journal-template.md](./journal-template.md) as your daily operating loop.
5. Saturday: [trade-review-process.md](./trade-review-process.md) → [weekly-reflection.md](./weekly-reflection.md).
6. Refer to [drawdown-protocol.md](./drawdown-protocol.md) and [volatility-playbook.md](./volatility-playbook.md) when conditions warrant.

## Compendium ↔ Code provenance

Where the bot's code simplifies a compendium concept (premium-zone proxy via Fibonacci, single-leg exit at 1:2 marker, no automatic cooling-off enforcement), the relevant doc carries an explicit "Current bot behaviour" callout. The simplifications are listed in `pipeline/build-summary.md` under the *Compendium ↔ Code delta* heading; do not let the docs imply behaviour the code does not deliver.
