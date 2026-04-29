# Design Brief — Trending Strategy Enhancement (FX GOAT)

**Run ID:** `20260429-trending-fxgoat`
**Auto-approval status:** AUTO-APPROVED (auto mode active; reasoning logged below).

## Goal

Add a professional-grade, top-down trend-following strategy to the MT5 trading bot that operationalises the FX GOAT Mastery Compendium principles, **without disturbing the locked OOS paper-trading window or the two existing strategies the bot currently relies on.**

## Chosen Approach

**Approach: "Additive new strategy + helper module + opt-in risk extension + operator playbooks."**

Three layered components on the work branch `feat/trending-strategy-fxgoat-v1` (branched off `fix/paper-broker-resilience-v0.1.1`):

1. **Strategy layer** — new `core/strategy/trend_following.py` implementing top-down (M15 → H4 resample) bias detection, Break-of-Structure (BoS) entry trigger, structural stop placement (last swing high/low), and asymmetric R:R targets (≥ 1:2). Two modes: `standard` (BoS only) and `premium` (BoS + Fibonacci 0.618–0.786 retracement confluence on the last impulsive leg).
2. **Risk extension** — additive `RiskManager.preservation_factor()` returning a 0.0–1.0 multiplier based on current drawdown vs `trailing_dd_warn / reduce / halt` thresholds. The new strategy may consume it via signal `meta`; existing `size_position()` is unchanged.
3. **Operator playbooks** — 15 markdown docs under `bot/docs/trading/`, each smoke-tested for required headings.

## Trade-offs accepted

| Decision | Trade-off accepted |
|---|---|
| Resample M15 → H4 in-memory rather than fetching a separate H4 frame | Saves bridge calls, keeps the strategy stateless; loses some H4 fidelity (edge bars clipped). |
| Fibonacci 0.618–0.786 zone as proxy for "Premium institutional zone" | Simpler to test; documented as a deliberate simplification. |
| New strategy is **not** wired into `autoresearch.enabled = true` | Honours the OOS lock; tuning is later, manual, after operator review. |
| Structural SL via swing low/high, ATR fallback when no recent swing exists | More faithful to compendium; falls back gracefully. |
| Operator docs are testable (heading parser) but not behaviourally enforced | Prevents drift from compendium structure without validating prose quality. |
| Strategy default is `mode: "standard"` | Lowest-surprise default; opt-in `premium` after demo validation. |
| No new pip dependencies | Keeps `.venv` lock untouched. |

## Components

### Component 1 — Structure helper (`core/strategy/structure.py`)
- `detect_swings(df, left=2, right=2) -> pd.DataFrame` — labels bars as `swing_high`, `swing_low`, or `None` (fractal definition).
- `classify_trend(swings) -> Literal["uptrend", "downtrend", "range"]` — from most recent four swings.
- `last_break_of_structure(df, swings) -> dict | None` — most recent BoS event `{direction, bar_index, level}`.

### Component 2 — Trend-following strategy (`core/strategy/trend_following.py`)
- Inherits `Strategy`. Name = `"trend_following"`.
- `generate_signal(df)` pipeline:
  1. Resample to H4 → `classify_trend` → higher-TF bias.
  2. M15 frame → `detect_swings` → `last_break_of_structure`.
  3. Confluence:
     - **Standard mode**: BoS on M15 must align with H4 bias.
     - **Premium mode**: BoS aligned with H4 bias AND last close inside the 0.618–0.786 Fib retracement of the prior impulsive leg.
  4. Entry on confluence; SL = last opposing swing ± ATR/4 buffer; TP = entry ± `tp_r_multiple × |entry-SL|` (default `tp_r_multiple = 2.0`).
- Reversal short-circuit (C7): if `classify_trend` flips on the H4 frame within last `n_bars` (default 10), emit `HOLD` with `reason = "structural_reversal_in_progress"`.
- Emits `meta`: `htf_bias`, `bos_direction`, `bos_level`, `swing_sl`, `atr`, `mode`, `trade_thesis`, `entry_price`, `sl`, `tp`.

### Component 3 — Risk preservation (`core/risk/manager.py` — additive)
- New method `preservation_factor(peak_equity, current_equity) -> float`.
- Reads `trailing_dd_warn / reduce / halt` from config.
- Returns `1.0` (no DD), `0.5` (≥ warn), `0.25` (≥ reduce), `0.0` (≥ halt).
- Existing `size_position()` is **not** modified.

### Component 4 — Strategy loader (`main.py:_load_strategy`)
- Add a third branch for `params.get("strategy") == "trend_following"`. Existing two branches kept verbatim.
- New branch reads `tp_r_multiple`, `mode`, `swing_left`, `swing_right`, `htf_resample_rule` (default `"4H"`).

### Component 5 — Isolated config artefact (`autoresearch/params.trend.yaml`)
- Read-only seed file. Header comment: "human-review only; NOT loaded by autoresearch loop. Operator must explicitly migrate values to `autoresearch/params.yaml` after DSR re-evaluation."

### Component 6 — Operator docs (`bot/docs/trading/*.md`)
- 15 docs (D1–D15 from intake). YAML frontmatter `{title, last_updated}` + compendium-mandated headings.
- Smoke test `tests/docs/test_trading_docs.py` parses headings.

## Open questions

None. All ambiguities resolved in `pipeline/intake-validation.md` § "Ambiguities resolved".

## Auto-approval reasoning

Auto-mode is active. The chosen approach satisfies every hard constraint:
- Existing files (`ema_crossover.py`, `mean_reversion.py`, `autoresearch/params.yaml`) are not modified — verified via AC-8 at integration time.
- New work is purely additive (one new strategy class, one new helper, one additive risk method, one new config artefact, 15 new docs, one new test file, one surgical edit to `main.py:_load_strategy`).
- Test suite remains green (AC-7) after every task.
- Branch is created from `fix/paper-broker-resilience-v0.1.1` (Constraint 1).
- Final PR is **draft** against `main`; merge is human-only (AC-9).

**Auto-approved; proceeding to Phase 1 (Plan).**
