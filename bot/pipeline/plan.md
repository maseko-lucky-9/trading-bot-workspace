# Implementation Plan — Trending Strategy (FX GOAT)

**Run ID:** `20260429-trending-fxgoat`
**Source spec:** `pipeline/design-brief.md` (auto-approved)
**Branch strategy:** `feat/trending-strategy-fxgoat-v1` ← `fix/paper-broker-resilience-v0.1.1`

## Wave 1 — Code & config (T01–T08)

### T01 — Structure helper module
- **Files**: `core/strategy/structure.py` (new), `tests/strategy/test_structure.py` (new).
- **Description**: Pure-pandas swing-point fractal detector + trend classifier + BoS detector.
- **Steps** (atomic, ≤3):
  1. Write `detect_swings(df, left=2, right=2)` — labels each bar as `swing_high` / `swing_low` / `None`.
  2. Write `classify_trend(swings)` — last-4-swing HH/HL/LH/LL classifier.
  3. Write `last_break_of_structure(df, swings)` — most-recent-BoS event detector.
- **Acceptance criteria**:
  - On a deterministic uptrend fixture, `classify_trend` returns `"uptrend"`.
  - On a deterministic downtrend fixture, returns `"downtrend"`.
  - On choppy input, returns `"range"`.
  - `last_break_of_structure` returns the correct bar index on a synthetic frame with one obvious BoS.
- **Tests added**: 6+ unit tests. **Files touched: 2**.

### T02 — TrendFollowing strategy class (skeleton + standard mode)
- **Files**: `core/strategy/trend_following.py` (new), `tests/strategy/test_trend_following.py` (new).
- **Steps**:
  1. Class skeleton inheriting `Strategy`, `name = "trend_following"`, init params, `compute_indicators` shell.
  2. `generate_signal` — Standard mode logic (H4 resample → classify_trend → BoS → emit Signal with structural SL + 1:2 TP).
  3. Reversal short-circuit when H4 trend flips in last `n_bars`.
- **Acceptance criteria**:
  - Insufficient bars → `HOLD reason="insufficient_bars"`.
  - Bullish H4 + bullish BoS on M15 → `BUY` with `meta.htf_bias="uptrend"`, structural SL, TP at 2× R.
  - Reversal in progress → `HOLD reason="structural_reversal_in_progress"`.
- **Tests added**: 8+ unit tests. **Files touched: 2**.

### T03 — Premium mode (Fib confluence)
- **Files**: `core/strategy/trend_following.py` (edit, additive), `tests/strategy/test_trend_following.py` (edit, additive).
- **Steps**:
  1. Add `_premium_zone_check(df, swings)` returning `True` when last close is inside 0.618–0.786 retracement of the latest impulsive leg.
  2. Wire it into `generate_signal` when `mode == "premium"`.
  3. Confirm Standard mode behaviour is unchanged.
- **Acceptance criteria**:
  - Premium mode + price outside zone → `HOLD reason="not_in_premium_zone"`.
  - Premium mode + price inside zone + BoS + bias align → `BUY/SELL`.
  - Standard mode signals unchanged byte-for-byte after this task.
- **Tests added**: 4+ unit tests. **Files touched: 2**.

### T04 — RiskManager.preservation_factor (additive)
- **Files**: `core/risk/manager.py` (edit, append-only method), `tests/risk/test_preservation.py` (new).
- **Steps**:
  1. Append `preservation_factor(peak_equity, current_equity)` method.
  2. Tests for each tier (no-DD → 1.0, warn → 0.5, reduce → 0.25, halt → 0.0, edge: peak=0 → 1.0).
- **Acceptance criteria**:
  - Each threshold returns the correct multiplier.
  - Existing risk tests remain green (no behaviour change to `size_position`).
- **Tests added**: 5+ unit tests. **Files touched: 2**.

### T05 — Wire trend_following into _load_strategy
- **Files**: `main.py` (surgical edit to `_load_strategy`), `tests/test_main_load_strategy.py` (new or edit).
- **Steps**:
  1. Add a third branch in `_load_strategy` for `params.get("strategy") == "trend_following"`.
  2. Test that existing two branches return unchanged class + parameters.
  3. Test that `trend_following` branch returns a `TrendFollowing` instance with the right config.
- **Acceptance criteria**:
  - `_load_strategy({"strategy": "mean_reversion", ...})` byte-identical behaviour.
  - `_load_strategy({"strategy": "ema_crossover"})` unchanged.
  - `_load_strategy({"strategy": "trend_following", ...})` returns `TrendFollowing`.
- **Tests added**: 3+ tests. **Files touched: 2**.

### T06 — Isolated trend params artefact
- **Files**: `autoresearch/params.trend.yaml` (new).
- **Steps**:
  1. Write the seed YAML with comment header.
- **Acceptance criteria**:
  - File exists with header comment "human-review only — NOT loaded by autoresearch loop".
  - YAML parses cleanly.
- **Tests added**: 0 (covered by T08). **Files touched: 1**.

### T07 — Filter regime hookup (additive)
- **Files**: `config.yaml` (surgical: append one map line), `tests/test_config_regime_map.py` (new).
- **Steps**:
  1. Append `trend_following: [0]` to `filters.regime.strategy_regime_map`.
  2. Test that existing keys (`ema_crossover`, `mean_reversion`) are unchanged.
  3. Test new key value.
- **Acceptance criteria**:
  - Existing keys unchanged.
  - New key present with value `[0]`.
  - `autoresearch.enabled` field is **still** `false` (asserted).
- **Tests added**: 3 tests. **Files touched: 2**.

### T08 — Lock & isolation regression suite
- **Files**: `tests/test_oos_locks.py` (new).
- **Steps**:
  1. Test `autoresearch/params.yaml` content matches the snapshot from this run start.
  2. Test `config.yaml: autoresearch.enabled` is `false`.
  3. Test `params.trend.yaml` not imported anywhere under `autoresearch/`.
  4. Test `core/strategy/ema_crossover.py` and `core/strategy/mean_reversion.py` SHA-256 unchanged.
- **Acceptance criteria**:
  - All four locks asserted; tests fail loud if any lock is broken.
- **Tests added**: 4 tests. **Files touched: 1**.

## Wave 2 — Operator docs (T09–T15)

Each Wave-2 task lands one or more docs **plus** the corresponding heading-parser test in `tests/docs/test_trading_docs.py`.

### T09 — Daily routines (D1, D3)
- **Files**: `docs/trading/daily-routine.md`, `docs/trading/daily-prep-checklist.md`, `tests/docs/test_trading_docs.py` (new).
- **Files touched: 3**.

### T10 — Weekly routines (D2, D10)
- **Files**: `docs/trading/weekly-routine.md`, `docs/trading/weekly-reflection.md`, edit `tests/docs/test_trading_docs.py`.
- **Files touched: 3**.

### T11 — Trade execution & journal (D4, D5, D12)
- **Files**: `docs/trading/journal-template.md`, `docs/trading/trade-review-process.md`, `docs/trading/simulated-trade-walkthrough.md`, edit test file.
- **Files touched: 4**.

### T12 — Risk & drawdown (D11, D13, D14)
- **Files**: `docs/trading/volatility-playbook.md`, `docs/trading/drawdown-protocol.md`, `docs/trading/risk-rules.md`, edit test file.
- **Files touched: 4**.

### T13 — Onboarding & growth (D6, D7)
- **Files**: `docs/trading/getting-started.md`, `docs/trading/growth-roadmap.md`, edit test file.
- **Files touched: 3**.

### T14 — Long-term & scaling (D8, D9)
- **Files**: `docs/trading/success-timeline.md`, `docs/trading/scaling-strategies.md`, edit test file.
- **Files touched: 3**.

### T15 — Index README (D15)
- **Files**: `docs/trading/README.md`, edit test file.
- **Files touched: 2**.

## Phase 4.5 — Integration check
- Run `cd /Users/ltmas/trading-bot-workspace/bot && .venv/bin/python -m pytest -q`.
- Expect `598 + N` passing where `N = sum of tests_added per task`.
- Verify all 4 OOS locks (T08) pass.

## Phase 5 — Final review
Self-review against AC-1 through AC-9.

## Phase 6 — Deploy
Skip (no Terraform / k8s / Dockerfile changes detected).

## Phase 6.5 — Branch completion
Open **draft** PR `feat/trending-strategy-fxgoat-v1` → `main` with build-summary as PR body.

## Self-review

- **self_review_pass**: `true`
- **self_review_notes**: ""
- **Spec coverage**: AC-1..AC-9 each map to ≥1 task.
- **Placeholder scan**: no `TBD`, `TODO`, `implement later`, `similar to Task N`, `add appropriate` strings.
- **File boundary map**: every file named with full path.
- **Granularity check**: max steps per task = 3; max files per task = 4.
- **Scope check**: single subsystem, builds and tests as one independent unit.

## Plan-Review (Phase 1.5 rolled in)

**Verdict: APPROVE.**

- Coverage: 9/9 acceptance criteria addressed.
- Placeholders: 0 found.
- Granularity: all tasks ≤ 3 steps, ≤ 4 files.
- Risk: locked-file regressions detected by T08 lock tests; auto-research isolation enforced by T06 + T08.
- Dependencies: T03 depends on T02; T05 depends on T02 + T03; T08 depends on T01..T07; Wave 2 (T09..T15) depends on T08 passing. Otherwise tasks are independent within their wave.

## Context map (Phase 2 rolled in)

**Primary files (read this session):**
- `/Users/ltmas/trading-bot-workspace/bot/main.py` (lines 58–72: `_load_strategy`)
- `/Users/ltmas/trading-bot-workspace/bot/core/strategy/base.py` (full)
- `/Users/ltmas/trading-bot-workspace/bot/core/strategy/ema_crossover.py` (full — locked, must not edit)
- `/Users/ltmas/trading-bot-workspace/bot/core/strategy/mean_reversion.py` (full — locked, must not edit)
- `/Users/ltmas/trading-bot-workspace/bot/core/strategy/indicators.py` (full)
- `/Users/ltmas/trading-bot-workspace/bot/core/risk/manager.py` (lines 1–80; preservation_factor appends here)
- `/Users/ltmas/trading-bot-workspace/bot/core/data/history.py` (full — confirms 200-bar fetch and synthetic fallback)
- `/Users/ltmas/trading-bot-workspace/bot/config.yaml` (full — confirms regime map structure)
- `/Users/ltmas/trading-bot-workspace/bot/autoresearch/params.yaml` (full — locked snapshot)

**Patterns established by existing code:**
- Strategy classes: subclass `Strategy`, set `name`, implement `compute_indicators` + `generate_signal`. Indicator helpers live in `core/strategy/indicators.py`.
- Signal `meta` dict carries `sl`, `tp`, `entry_price`, plus strategy-specific fields. Order manager reads `meta.get("sl", 0.0)` / `meta.get("tp", 0.0)` (`main.py:268-271`).
- Tests live under `tests/strategy/`, `tests/risk/`, etc., mirroring `core/` layout.

**Unknown dependencies:** none. All required imports already in tree (pandas, numpy, yaml, dataclasses).

## Pre-flight (Phase 3 rolled in)

- **Governance**: PASS — additive changes; existing locked files untouched (verified by T08).
- **Secrets**: none required.
- **Permissions**: file system writes within `/Users/ltmas/trading-bot-workspace/bot/`.
- **Network**: none.
- **Pip deps**: none added.
- **Migrations**: none.
- **Infra**: none — Phase 6 will skip.

**Verdict: PASS** (no WARN, no FAIL). Proceed to Phase 4.
