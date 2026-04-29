# Final Review — Trending Strategy (FX GOAT) v1

**Run ID:** `20260429-trending-fxgoat`
**Branch:** `feat/trending-strategy-fxgoat-v1` ← `fix/paper-broker-resilience-v0.1.1`
**Date:** 2026-04-29
**Status:** PASS (711/711 tests green; all 9 AC satisfied)

---

## AC mapping → evidence

| AC | Spec | Evidence (file path · test · commit) |
|----|------|---|
| **AC-1** | New `core/strategy/trend_following.py` exists, inherits Strategy, emits valid Signal | `bot/core/strategy/trend_following.py` · `tests/strategy/test_trend_following.py` (13 tests) · `tests/test_main_load_strategy.py::test_load_strategy_trend_following*` · `tests/test_backtest_engine_trend.py` (4 cases) · commits `c8a58f7`, `ea300b6` |
| **AC-2** | `core/strategy/structure.py` correctly identifies swings + HH/HL/LH/LL | `bot/core/strategy/structure.py` · `tests/strategy/test_structure.py` (11 tests) · commit `c8a58f7` |
| **AC-3** | `_load_strategy()` recognises `trend_following` while leaving the existing two branches byte-identical | `bot/main.py:58-83` · `tests/test_main_load_strategy.py::test_load_strategy_mean_reversion_unchanged`, `::test_load_strategy_ema_crossover_default_branch_unchanged` · `tests/test_oos_locks.py::test_locked_ema_crossover_unchanged`, `::test_locked_mean_reversion_unchanged` · commit `ea300b6` |
| **AC-4** | `RiskManager.preservation_factor` tiered multiplier; existing `size_position` unaffected | `bot/core/risk/manager.py` (append) · `tests/risk/test_preservation.py` (10 tests) · `tests/test_risk_manager.py` (existing 13 tests, all green) · commit `848f4ec` |
| **AC-5** | Isolated `autoresearch/params.trend.yaml` exists, comment-headed, never loaded by autoresearch | `bot/autoresearch/params.trend.yaml` (54 lines, "human-review only" header) · `tests/test_oos_locks.py::test_params_trend_yaml_not_imported_under_autoresearch`, `::test_params_trend_yaml_not_imported_in_main_or_loop` · commit `c6211ca` |
| **AC-6** | All D1–D15 docs exist, frontmatter present, required sections, heading-parser test passes | `bot/docs/trading/*.md` (15 files) · `tests/docs/test_trading_docs.py` (61 tests covering existence, frontmatter, gap-fills G1-G7, code-delta callouts, H2 body length) · commit `221ee20` |
| **AC-7** | Full suite green at 598 + N | **711 passed in 62.45 s** (598 baseline + 113 new tests) · runs cleanly after every commit |
| **AC-8** | Locked files byte-identical | `tests/test_oos_locks.py::test_locked_params_yaml_unchanged`, `::test_locked_ema_crossover_unchanged`, `::test_locked_mean_reversion_unchanged`, `::test_autoresearch_enabled_is_false` · SHA-256 fixture at `tests/fixtures/oos_locks_snapshot.json` · commit `32ab0c4` |
| **AC-9** | Draft PR open from `feat/trending-strategy-fxgoat-v1` targeting `fix/paper-broker-resilience-v0.1.1` (per Q6 decision; will retarget `main` if PR #1 merges first) | Pending Phase 6.5 — push + `gh pr create --draft` |

---

## Net new test inventory

| Source | Tests added | Notes |
|---|---|---|
| `tests/strategy/test_structure.py` | 11 | swing detection / classify_trend / BoS event |
| `tests/strategy/test_trend_following.py` | 13 | insufficient bars / init / bullish / bearish / range / premium-zone / R:R math / mode plumbing / reversal short-circuit |
| `tests/risk/test_preservation.py` | 10 | each tier + zero/negative peak edge cases + non-regression on size_position |
| `tests/test_main_load_strategy.py` | 5 | mean_reversion / EMA fallback / trend_following standard / premium / defaults |
| `tests/test_backtest_engine_trend.py` | 4 | trend_following default / premium overrides / mean_reversion regression / unknown name → EMA |
| `tests/test_config_regime_map.py` | 3 | existing keys unchanged / trend_following present / autoresearch.enabled is false |
| `tests/test_oos_locks.py` | 6 | locked-file SHA × 3 / autoresearch.enabled / params.trend isolation × 2 |
| `tests/docs/test_trading_docs.py` | 61 | existence × 15 + frontmatter × 15 + gap-fills × 13 + delta callouts × 3 + H2 body × 15 |
| **Total net new** | **113** | |

Cumulative: **598 + 113 = 711 passed in 62.45 s**.

---

## Compendium ↔ Code delta — accepted simplifications

The following deviations from the FX GOAT compendium are deliberate v1 choices, documented in code (`core/strategy/trend_following.py` docstring) and in the operator docs (D14 `risk-rules.md`, D12 `simulated-trade-walkthrough.md`):

1. **Premium zone** = Fibonacci 0.618–0.786 retracement of the most recent impulsive leg, vs the compendium's broader "unmitigated supply/demand area + liquidity sweep + candlestick trigger" definition.
2. **Final exit** = single-leg close at fixed `tp = entry + 2 × risk`, vs the compendium's three-step partial-fill at 1:2 + BE-trail + HTF-target.
3. **No volatility-alignment detection** (coiling vs ranging, FX GOAT §2 Premium Indicators).
4. **No 24-hour cooling-off enforcement in code** — operator-side checklist only (drawdown-protocol.md).

All four deviations are surfaced in the operator docs with explicit "Current bot behaviour" callouts so the docs do not promise behaviour the code lacks. Future-roadmap deltas (Adam Grimes pullback model, MACD divergence filter, Naked Forex pin-bar detector, etc.) are recorded in the canonical plan file and `pipeline/build-summary.md` for follow-up PRs.

---

## OOS-window safety check

- `autoresearch/params.yaml` SHA-256 unchanged: `7818d4d5374d9fb489d81961845155cd69f5eb47c4a72de5b034eb89d976d285`
- `core/strategy/ema_crossover.py` SHA-256 unchanged: `26e8fcc440626dba3a238237265ba6e24c2a9e2fa035fbf8449f5d34e43e605d`
- `core/strategy/mean_reversion.py` SHA-256 unchanged: `5ea85b1314c85013ce97fb502e2ee21b0464de019532e0fe08d645c665cd7a49`
- `config.yaml: autoresearch.enabled` = `false` (locked)
- `bot.mode: paper`, `bot.instruments: [EURUSD]`, `bot.timeframe: M15` (locked)
- New strategy ships dormant: `params.yaml: strategy: mean_reversion` is unchanged. `trend_following` is reachable only by an explicit operator action (write a separate params overlay or edit `params.yaml` after the OOS window closes).

The PR introduces no live behaviour change to the running bot.

---

## Self-review verdict

**PASS.** All 9 acceptance criteria mapped to concrete file/test/commit evidence. 113 net new tests, 0 failures, 0 deferred. Branch is clean and ready for Phase 6.5 (push + draft PR).
