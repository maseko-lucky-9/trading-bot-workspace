# Phase 0.5 Reflection — H1 Backfill Run

## What worked
- User pre-approved all 5 design decisions out-of-band, satisfying the HARD-GATE without needing inline AskUserQuestion rounds.
- Decisions are clean and orthogonal: source / strategy / merge / transport / packaging — each maps to one architectural concern.
- The chosen approach (HistoricalDataClient + history_store) gives a natural seam for unit testing without live bridge calls (key for Sunday market-closed constraint C1).

## What failed
- Old `design-brief.md` from the superseded PositionMonitor run was still on disk and had to be overwritten. State.json should track `design_brief_source` per-run with version tag to prevent stale-artifact confusion across resumed runs.

## Carry-forward to Phase 1
- Plan must explicitly confirm the existing parquet schema by reading one file FIRST (constraint C3) — make that T001.
- Plan must NOT make any live bridge call in any test (constraint C1, today is Sunday).
- Plan must keep MT5BridgeClient untouched at the public-API level — wrapper-only changes (decision 4).
- Top-up logic needs to handle the "no existing cache" case (cold start) and the "cache already at/over target" case (no-op).
