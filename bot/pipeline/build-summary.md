# Build Summary — Trending Strategy (FX GOAT) v1

**Run ID:** `20260429-trending-fxgoat`
**Branch:** `feat/trending-strategy-fxgoat-v1` ← `fix/paper-broker-resilience-v0.1.1`
**Date:** 2026-04-29
**Status:** SUCCESS — 711/711 tests pass; all AC mapped; 7 commits ready to push.

## Summary

- Adds the FX GOAT trend-following strategy (`core/strategy/trend_following.py`) and its market-structure helpers (`core/strategy/structure.py`), plus a tiered drawdown-aware sizing helper (`RiskManager.preservation_factor`).
- Wires the new strategy into both `main._load_strategy` and `backtest.engine._build_strategy`. Existing `mean_reversion` and `ema_crossover` branches are byte-identical in behaviour.
- Ships an isolated `autoresearch/params.trend.yaml` seed (human-review only — never read by the autoresearch loop). Adds `trend_following: [0]` to `filters.regime.strategy_regime_map`.
- Codifies an OOS-window lock & isolation regression suite (`tests/test_oos_locks.py` + SHA-256 fixture).
- Lands 15 operator docs under `bot/docs/trading/` derived from the FX GOAT Mastery Compendium, with a 61-test heading/frontmatter/gap-fill/code-delta-callout parser to keep them honest.
- Strategy ships **dormant**: `params.yaml: strategy: mean_reversion` is locked and unchanged. The PR introduces zero live behaviour change.

## Commits (oldest first)

| SHA | Title |
|---|---|
| `c8a58f7` | feat(strategy): trend_following + market-structure helpers (FX GOAT v1, Wave 1 T01-T03) |
| `848f4ec` | feat(risk): preservation_factor for drawdown-aware sizing (T04) |
| `ea300b6` | feat(strategy): wire trend_following into _load_strategy and backtest engine (T05) |
| `c6211ca` | feat(autoresearch): isolated params.trend.yaml seed (T06) |
| `990e64b` | feat(filters): wire trend_following into regime map; assert OOS lock (T07) |
| `32ab0c4` | test(oos): lock & isolation regression suite (T08) |
| `221ee20` | docs(trading): Wave-2 operator playbook — 15 docs derived from FX GOAT (T09-T15) |

## Acceptance criteria

All 9 ACs satisfied. Per-AC evidence with file paths, test names, and commit SHAs is in [`pipeline/final-review.md`](./final-review.md).

## Compendium ↔ Code delta (accepted v1 simplifications)

| Concept (FX GOAT §) | Book definition | Code v1 |
|---|---|---|
| Premium zone (§2/§3) | Unmitigated supply/demand + liquidity sweeps + candlestick triggers | Fibonacci 0.618–0.786 retracement proxy |
| Final exit (§3 Phase 4) | Partial at 1:2 + BE-trail + HTF target | Single-leg close at fixed 1:2 |
| Volatility alignment (§2) | Coiling vs ranging filter | Not implemented |
| Cooling-off after loss (§4) | 24-hour rule | Operator checklist only — no runtime enforcement |

All four deviations are surfaced in the operator docs with explicit "Current bot behaviour" callouts so the docs never promise behaviour the code lacks.

## Future roadmap (recorded for follow-up PRs)

- Multi-leg `OrderManager` to support partial fills, BE-trails, and HTF targets
- Liquidity-sweep + demand/supply zone detection (replaces the Fib proxy)
- MACD-divergence pullback filter (Adam Grimes — *Art and Science of Technical Analysis* Ch 3)
- Pin-bar / engulfing candle confirmation trigger (Walter Peters — *Naked Forex* Ch 6, 8)
- `risk.cooling_off_hours: 24` runtime config flag
- Integration of Mark Douglas — *Trading in the Zone* probability mindset into a future `docs/trading/psychology.md`

## Test plan

- [x] Full pytest suite green: **711 passed in 62.45 s** (598 baseline + 113 new tests; ran cleanly after every commit, not just at the end)
- [x] OOS-lock canaries (`tests/test_oos_locks.py`) confirm `params.yaml`, `ema_crossover.py`, `mean_reversion.py`, and `autoresearch.enabled: false` are byte-identical
- [x] Doc-parser canaries (`tests/docs/test_trading_docs.py`) confirm every required gap-fill phrase (G1–G7) and every code-vs-compendium delta callout is present
- [x] Backtest CLI selection: `_build_strategy({"strategy": "trend_following", ...})` returns a `TrendFollowing` instance with all kwargs surfaced; existing branches unchanged
- [ ] **Operator action before activation** (out of PR scope):
  - [ ] Subscribe EUR/USD in MT5 MarketWatch on the UTM VM (per the resilience PR's contract)
  - [ ] Verify `curl http://192.168.64.1:8080/state | jq .tick.symbol` returns `"EURUSD"`
  - [ ] When the OOS window closes and DSR ≥ 1.0, write a separate params overlay with `strategy: trend_following` (do NOT mutate `params.yaml` before that gate)
  - [ ] Run an out-of-sample backtest: `python backtest/engine.py --params autoresearch/params.trend.yaml --symbol EURUSD --timeframe M15 --bars 5000 --guard`

## PR target

Targets `fix/paper-broker-resilience-v0.1.1` (PR #1) per plan decision Q6. If PR #1 merges first, GitHub will retarget this PR to `main` automatically.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
