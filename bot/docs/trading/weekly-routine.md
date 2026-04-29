---
title: Weekly Operational Schedule
last_updated: 2026-04-29
source: FX GOAT Mastery Compendium
---

# Weekly Operational Schedule

The compendium's §5 weekly schedule is built around the principle that pre-week analysis and post-week audit are non-negotiable bookends to mid-week execution.

## Sunday — Weekly Market Outlook

Identify the top 3 high-probability pairs for the week. Even though the bot currently trades only EUR/USD on M15, the discretionary scan keeps your eye trained on the broader FX universe and informs whether you should expand the bot's `bot.instruments:` list after the OOS window closes. For each candidate pair, document on the Daily and 4-hour timeframes:

- Dominant structure (HH/HL, LH/LL, range)
- Nearest unmitigated demand/supply zone
- Any scheduled high-impact news during the week
- Conviction (1–5)

Only pairs scoring ≥4/5 conviction are added to the live watchlist.

## Mon–Fri — Execution Phase

Open the day with the [daily-routine.md](./daily-routine.md). Monitor the bot's positions through the London and New York Kill Zones. Your manual role during the execution phase is **supervisory**, not executional: you intervene only if the bot mis-routes, the bridge disconnects, or the market behaviour materially diverges from the structural read you set on Sunday.

## Friday PM — Market Close

Exit all intraday positions before your local close-of-week. **No weekend exposure.** The bot's launchd agent should be unloaded for the weekend if you cannot supervise overnight; the paper-trading window is in any case insulated from real risk.

## Saturday — Performance Audit

Detailed journaling and chart review of every trade taken during the week. Use [trade-review-process.md](./trade-review-process.md) for the per-trade rubric and [weekly-reflection.md](./weekly-reflection.md) for the higher-level "Did I follow my rules?" reflection. The Saturday audit feeds the next Sunday's outlook — the cycle is what makes the routine compounding rather than disposable.
