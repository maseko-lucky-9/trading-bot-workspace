# Intake Validation — Trending Strategy Enhancement (FX GOAT Compendium)

**Run ID:** `20260429-trending-fxgoat`
**Date:** 2026-04-29
**Project root:** `/Users/ltmas/trading-bot-workspace/bot`
**Source framework:** `$V/wiki/investment-portfolio/framework/The FX GOAT Mastery Compendium.md`

## Problem Statement

The MT5 trading bot currently ships two strategies — `bollinger_mean_reversion` (the active OOS strategy on EURUSD M15) and `ema_crossover` (a basic trending fallback). The EMA crossover does not implement any of the FX GOAT principles (top-down structural alignment, premium confirmation zones, multi-timeframe confluence, asymmetric R:R targets, drawdown-aware sizing). The user wants the trending capability brought up to professional standard by applying the FX GOAT Mastery Compendium, **without touching the locked OOS paper-trading window or the existing two strategies that the bot currently relies on**.

## Scope

### In scope (code)

| # | Compendium item | Code mapping |
|---|---|---|
| C1 | Most effective technical strategies | New `core/strategy/trend_following.py` — top-down trend follower |
| C2 | Key indicators (structure, EMA, ATR, RSI as filter) | Reuses `core/strategy/indicators.py`; adds higher-timeframe trend filter |
| C3 | Entry points (Premium method: shift-of-structure on lower TF after pullback to demand zone) | Implemented as Break-of-Structure (BoS) confirmation in lower-TF logic |
| C4 | Stop-loss placement rules (below structural low / above structural high) | `meta["sl"]` placed at last swing low/high, not just ATR multiple |
| C5 | Capital-preservation during drawdown (reduce frequency + size) | New `RiskManager.preservation_factor()` method consulted by sizing |
| C6 | Multi-timeframe confirmation of direction | Dual-frame analysis: H4 (or higher TF) bias gate + M15 entry trigger |
| C7 | Trend reversal identification | Lower-high after series of higher-highs (and inverse) → no-trade signal |
| C8 | High-probability setup definition (1:2 minimum R:R) | `atr_tp_multiplier` ≥ 2.0 × `atr_sl_multiplier`, partial at 1:2 marker |
| C9 | Chart-trend interpretation (HH/HL bullish, LH/LL bearish) | `core/strategy/structure.py` — pure-pandas swing-point detector |
| C10 | High-volatility-day preparation (reduce risk 50% on news) | Hook into existing `filters.news_blackout` config; size halver |
| C11 | Beginner vs advanced execution (Standard = trendline; Premium = SoS+confluence) | Strategy supports `mode: "standard" \| "premium"` parameter |
| C12 | Backtest-driven validation (simple-strategy backtest) | New strategy must run through existing `backtest/engine.py` cleanly |
| C13 | Trading-performance audit fields | Strategy emits `meta["trade_thesis"]` for journaling export |

### In scope (config)

| # | Compendium item | Config mapping |
|---|---|---|
| F1 | New strategy must not affect the OOS window | New isolated artefact `autoresearch/params.trend.yaml` (read-only seed for future tuning); `autoresearch/params.yaml` is **not touched** |
| F2 | Strategy registration | `_load_strategy()` extended to recognise `strategy: "trend_following"` (additive — does not change default behaviour for `mean_reversion` / `ema_crossover`) |
| F3 | Filter hookup | `filters.regime.strategy_regime_map.trend_following: [0]` added (TREND regime only) |

### In scope (operator-only docs)

| # | Compendium item | Doc path |
|---|---|---|
| D1 | Daily trading routine | `docs/trading/daily-routine.md` |
| D2 | Weekly trading session planning + Saturday performance audit | `docs/trading/weekly-routine.md` |
| D3 | Morning global-news routine + daily market-prep checklist | `docs/trading/daily-prep-checklist.md` |
| D4 | Detailed trading journal template | `docs/trading/journal-template.md` |
| D5 | Step-by-step trade review process | `docs/trading/trade-review-process.md` |
| D6 | Three actionable steps to start trading today | `docs/trading/getting-started.md` |
| D7 | Beginner → advanced strategy transition + month-one growth plan | `docs/trading/growth-roadmap.md` |
| D8 | Long-term goal timeline + success milestones | `docs/trading/success-timeline.md` |
| D9 | Account-scaling strategies | `docs/trading/scaling-strategies.md` |
| D10 | Weekly trade-mistake reflection template + multi-month progress tracking | `docs/trading/weekly-reflection.md` |
| D11 | High-volatility-day preparation playbook | `docs/trading/volatility-playbook.md` |
| D12 | Simulated trade walkthrough | `docs/trading/simulated-trade-walkthrough.md` |
| D13 | Capital-preservation rules during drawdown | `docs/trading/drawdown-protocol.md` |
| D14 | Risk-management rule sheet (consolidated) | `docs/trading/risk-rules.md` |
| D15 | Index README for the docs/trading/ directory | `docs/trading/README.md` |

### Out of scope (explicit)

- `global-dividend-investment-portfolio-strategy.md`, `south-african-dividend-investment-strategy.md` — different framework, not invoked.
- Modifying `core/strategy/ema_crossover.py` or `core/strategy/mean_reversion.py` — locked.
- Mutating `autoresearch/params.yaml` — locked.
- Flipping `config.yaml: autoresearch.enabled` to `true` — locked.
- Auto-running optimisation for the new strategy — surfaced for human review only.
- Deploying / merging to `main` — out of pipeline scope; PR opened as **draft**.
- MCP-dependent tooling (subagents cannot use MCP per user CLAUDE.md). Pipeline phases use only built-in tools.

## Acceptance Criteria

1. **AC-1** — A new strategy class `core/strategy/trend_following.py` exists, inherits `Strategy`, and produces valid `Signal` objects for synthetic and bridge OHLCV frames.
2. **AC-2** — A new structure helper `core/strategy/structure.py` correctly identifies swing highs/lows and HH/HL/LH/LL labels on known fixtures.
3. **AC-3** — `_load_strategy()` in `main.py` recognises `strategy: "trend_following"` while keeping the existing two branches byte-identical in observable behaviour.
4. **AC-4** — `core/risk/manager.py` exposes `preservation_factor(peak_equity, current_equity)` returning 1.0 when not in drawdown, 0.5 at `trailing_dd_warn`, 0.25 at `trailing_dd_reduce`, 0.0 at `trailing_dd_halt`. Existing `size_position()` is **not** required to consume it (additive only).
5. **AC-5** — A new isolated config artefact `autoresearch/params.trend.yaml` exists with the new strategy's seed parameters, clearly commented as "human-review only — not loaded by autoresearch loop".
6. **AC-6** — Every doc D1–D15 exists under `bot/docs/trading/`, has YAML frontmatter (`title`, `last_updated`), and contains required sections enumerated by the compendium. Each is verified by a smoke test that parses required headings.
7. **AC-7** — The full test suite (`cd bot && .venv/bin/python -m pytest -q`) passes after every landed task. Baseline is **598 passed**; final count must be `598 + N` where N is the number of net-new tests added.
8. **AC-8** — `autoresearch/params.yaml` is byte-identical before vs after the run; `config.yaml: autoresearch.enabled` byte-identical (`false`); `core/strategy/ema_crossover.py` and `core/strategy/mean_reversion.py` byte-identical.
9. **AC-9** — A draft PR is opened from `feat/trending-strategy-fxgoat-v1` (branched off `fix/paper-broker-resilience-v0.1.1`) targeting `main`, with `build-summary.md` rendered as the PR description.

## Constraints

1. **Branch hygiene**: `feat/trending-strategy-fxgoat-v1` MUST be created from `fix/paper-broker-resilience-v0.1.1`. Do not branch from `main`. Do not rebase mid-pipeline.
2. **Test cadence**: After every landed task, run `cd /Users/ltmas/trading-bot-workspace/bot && .venv/bin/python -m pytest -q`; suite stays green.
3. **Commit signature**: Each task lands on its own commit with Red→Green→Refactor signature.
4. **No autoresearch param mutation**: Never write to `autoresearch/params.yaml`. Never set `autoresearch.enabled: true` programmatically.
5. **Docs are testable**: Every operator-only doc gets a smoke test in `tests/docs/test_trading_docs.py`.

## Scope decomposition check

- Multiple independent subsystems? No — single subsystem (strategy layer + adjacent docs).
- Cross-cutting concerns spanning >3 bounded contexts? No — strategy + risk + docs (3, on the boundary).
- Estimated task count? ~15–18 tasks — at the upper edge.

**Decision:** `scope_warning: true` (recorded in `state.json`); proceed as a single pipeline run with a **two-wave plan**:
- **Wave 1 (code + config)** — tasks T01–T08.
- **Wave 2 (operator docs)** — tasks T09–T15.

## Ambiguities resolved (most-defensible interpretation)

1. **Strategy name** — `trend_following` (Pythonic, matches existing snake_case convention, distinct from `mean_reversion`).
2. **"Premium" mode** — encoded as a `mode` parameter (`"standard" | "premium"`). Standard = simple BoS; Premium = BoS + premium-zone confluence (Fibonacci 0.618–0.786 retracement of the impulsive leg).
3. **Higher timeframe** — bot timeframe is locked at M15. Higher-TF bias is derived from **resampling the M15 frame to H4** in-memory (stateless, no extra bridge calls).
4. **Premium zone definition** — Fibonacci 0.618–0.786 retracement of the most recent impulsive structural leg. Documented as a deliberate simplification in the strategy docstring.
5. **Backtest-engine compatibility** — new strategy plugs in via the same `--strategy trend_following` flag.

## Risks & dependencies

1. **PR #1 dependency** — base off `fix/paper-broker-resilience-v0.1.1`. If PR #1 force-pushes during the run, work branch needs a rebase (operator-supervised).
2. **MCP unavailability for subagents** — pipeline uses built-in tools only.
3. **OOS lock** — Phase 3 governance check explicitly diffs `autoresearch/params.yaml` and the `autoresearch.enabled` field at task-end; AC-8 verified at integration check.
4. **Bridge-history coupling** — new strategy resamples M15 → H4 in-memory; needs ≥ 200 M15 bars (~50 H4 bars). Existing fetch is 200 bars at `main.py:261`.
5. **Synthetic data fallback** — tests use deterministic fixtures, not synthetic data, for assertion correctness.

## Success metrics (verification)

- Full pytest run: `598 + N` passing, 0 failing.
- `git diff --name-only fix/paper-broker-resilience-v0.1.1..feat/trending-strategy-fxgoat-v1` lists only **new** files plus surgical edits to `main.py:_load_strategy` and `core/risk/manager.py` (additive method).
- `git diff fix/paper-broker-resilience-v0.1.1..HEAD -- core/strategy/ema_crossover.py core/strategy/mean_reversion.py autoresearch/params.yaml` returns empty.
- `grep -E '^\s*enabled:\s*true' config.yaml | head -1` returns nothing under the `autoresearch:` block.
- All 15 docs under `bot/docs/trading/` exist and pass the smoke test.

## Gate decision

**PASS — proceed to Phase 0.5 (Design Gate).**

No pre-existing spec is referenced; Phase 0.5 must run inline-Socratic and write `pipeline/design-brief.md`. In auto-mode the orchestrator drafts the brief from the most-defensible interpretations above, marks it auto-approved with reasoning, and proceeds.
