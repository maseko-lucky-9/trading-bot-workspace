---
title: Backtesting How-To
last_updated: 2026-04-30
source: FX GOAT Mastery Compendium
---

# Backtesting How-To

Operator walkthrough of the bot's backtest engine (`backtest/engine.py`). The FX GOAT compendium (§6 *Month One Growth Plan*, Week 3) calls for re-backtesting after integrating Lesson 5 (Premium); this doc gives you the exact CLI invocations to do that against the bot's M15 cache without needing the bridge.

## Three goals of backtesting

Walter Peters' *Naked Forex* (Ch 3 *Back-Testing Your System*) names the three goals every backtest should serve:

1. **Validate the edge.** Does the strategy produce a positive expectancy on data the rules have never been tuned against? A backtest that only proves the in-sample fit is decorative; an out-of-sample (CV or held-out) backtest with a positive Sharpe is the minimum bar.
2. **Build conviction in execution.** The strategy will lose. Knowing *how often* and *how deeply* it loses on historical data is what lets you sit through real losing streaks without panic-tweaking. The drawdown number from the engine is your psychological budget.
3. **Refine rules where data shows persistent bias.** If trades cluster at one session, one regime, or one time of day with worse-than-average outcome, the rule that doesn't filter that out needs a fresh look. The backtest is what surfaces the pattern; the journal is what records the fix.

Lose any of these three and you've turned the backtest into theatre.

## Where the data lives

The engine reads OHLCV bars from `bridge_data/history/{SYMBOL}_{TIMEFRAME}.parquet`. Coverage as of the last yfinance backfill (2026-04-29):

- `EURUSD_M15.parquet` — ~3683 bars (~38 days). The primary OOS surface.
- `EURUSD_H1.parquet` — ~500 bars
- `EURUSD_D1.parquet` — ~500 bars
- `EURUSD_M5.parquet` — ~10000 bars

If a parquet has at least `max(100, bars/2)` rows, the engine uses it directly. Otherwise it falls through to a live bridge fetch, and from there to a deterministic synthetic random-walk fallback (which is **refused** without `--allow-synthetic` — see the safeguard section below).

To extend coverage, use `scripts/backfill_yfinance.py --symbols EURUSD --tf M15` (the script merges new bars into the existing parquet without overwriting older data).

## Engine CLI essentials

The engine entry point is `python backtest/engine.py`. Key flags:

| Flag | Meaning |
|---|---|
| `--params PATH` | YAML overlay specifying the strategy + its kwargs. When omitted, the engine uses `config.yaml`'s defaults — typically the locked `mean_reversion`. |
| `--symbol EURUSD` | Symbol to load from cache. EUR/USD is the only locked instrument right now. |
| `--timeframe M15` | M15 / H1 / H4 / D1 / M5. Must match a cached parquet. |
| `--bars N` | Number of trailing bars to backtest on. Engine emits `WARN bars=N below 4176 statistical minimum` if N is small. |
| `--metric sharpe` | Print only the Sharpe to stdout (machine-parseable, used by the autoresearch loop). Alternatives: `sortino`, `calmar`. |
| `--guard` | Run the guard rail check (drawdown ≤ 5 %, win-rate ≥ 30 %, Sharpe ≥ 1.5). Prints `GUARD PASS …` or `GUARD FAIL …` and exits 0 or 1. |
| `--cv kfold:N --embargo M` | Purged k-fold cross-validation with M-bar embargo. Replaces the deprecated `--wf-train-pct`. |
| `--min-trades N` | Minimum average trades per fold (CV only); folds below this return Sharpe = 0 instead of misleading numbers. |
| `--allow-synthetic` | Permits the synthetic random-walk fallback when no real data is available. Safeguard — off by default. |

stdout contract:
```
SHARPE 1.2340                    # --metric run
GUARD PASS drawdown=3.21% win_rate=51.4% bars=8760 trades=142
GUARD FAIL drawdown=6.43% exceeds 5.0% threshold
```

Exit codes: `0` success or guard pass; `1` guard fail; `2` insufficient data, crash, or synthetic-data refused.

## Worked example — locked mean_reversion

The currently-active OOS strategy:

```
cd /Users/ltmas/trading-bot-workspace/bot
.venv/bin/python backtest/engine.py \
  --params autoresearch/params.yaml \
  --symbol EURUSD --timeframe M15 --bars 3683 --guard
```

Expected behaviour: this invocation reads the locked params (`mean_reversion`, `bb_period=14`, `bb_std=2.25`, `rsi_period=7`, `atr_multiplier=2.25`) and runs them on the full cached 3683-bar window. On the most recent 2026-04-29 cache snapshot the locked configuration **failed** the guard (Sharpe -3.08, win_rate 45.8 %), confirming the regime shift away from the original DSR=0.98 yfinance window — but the file is preserved so the OOS-window v3 paper-trading data accumulates against an exact, comparable baseline.

## Worked example — trend_following standard

To backtest the v1.1 trending strategy without touching the locked params:

```
cd /Users/ltmas/trading-bot-workspace/bot
.venv/bin/python backtest/engine.py \
  --params autoresearch/params.trend.yaml \
  --symbol EURUSD --timeframe M15 --bars 3683 --guard
```

Expected (post-2026-04-29 v1.1 calibration): `GUARD PASS drawdown=2.76 % win_rate=55 % bars=3683 trades=20`. This is the standard-mode v1.1 result that motivated the merge of PR #4. The `--params autoresearch/params.trend.yaml` form keeps the trending overlay separate from the OOS-locked main params; the engine merges the overlay into the in-process config without writing back to disk.

## Worked example — trend_following premium

Premium mode adds the Fib zone + pin-bar AND-gate. Same data, additional filtering:

```
cat > /tmp/trend-premium.yaml <<'EOF'
strategy: trend_following
mode: premium
sl_atr_buffer: 1.0
tp_r_multiple: 1.5
EOF

cd /Users/ltmas/trading-bot-workspace/bot
.venv/bin/python backtest/engine.py \
  --params /tmp/trend-premium.yaml \
  --symbol EURUSD --timeframe M15 --bars 3683 --guard
```

Expected on the 38-day window: 0 trades — the Fib + pin AND-gate is intentionally restrictive, and 38 days is too short a sample for the AND-gate to surface qualifying setups. This is the documented Premium-mode behaviour; the marubozu unit test in `tests/strategy/test_trend_following.py` is what proves the gate fires correctly when triggered. Run Premium against a longer window once the cache is extended.

## Reading guard output

`GUARD PASS` means **all three** thresholds clear simultaneously: drawdown ≤ 5 %, win-rate ≥ strategy-specific minimum (30 % for `ema_crossover`-family, 50 % for `mean_reversion`-family), Sharpe ≥ 1.5. The output line includes drawdown / win_rate / trade-count for evidence; copy it verbatim into the journal entry.

`GUARD FAIL` enumerates which thresholds tripped, separated by `;`. Read all of them — a configuration that fails on Sharpe alone but passes drawdown and win-rate is a different kind of broken from one that fails on win-rate (suggesting noise / chop) or drawdown (suggesting tail risk). The autoresearch loop's coordinate-descent treats each FAIL component as a different kind of feedback.

## Cross-validation flag

For statistical robustness on the small 38-day window, use 5-fold purged k-fold with a 96-bar embargo:

```
.venv/bin/python backtest/engine.py \
  --params autoresearch/params.trend.yaml \
  --symbol EURUSD --timeframe M15 --bars 3683 \
  --cv kfold:5 --embargo 96 --metric sharpe
```

The 96-bar embargo (= 1 trading day on M15) eliminates lookahead bias by purging bars adjacent to each test fold. CV results swing more than full-window results — that swing is itself the diagnostic. A strategy whose CV Sharpe varies wildly with small data shifts is fragile; one with stable CV Sharpe across data slices has a real edge.

Always combine `--cv` with `--min-trades 5` (or higher); a fold with fewer than five trades is statistically meaningless and the engine returns Sharpe=0 to suppress the false signal.

## Synthetic-data refusal

If neither the cache nor the live bridge can supply real OHLCV, the engine **refuses to run** by default and exits with code 2. This is by design — the original 2026-04-25 outage briefly produced backtest results against the random-walk fallback that masqueraded as real-edge claims. The refusal stops that class of error at the gate.

If you genuinely want to test the engine's plumbing on synthetic data (e.g. while developing a new strategy class without a populated cache), pass `--allow-synthetic` explicitly. The engine prints the source label `synthetic` in its output so the data origin is unambiguous. **Never** quote a synthetic-data Sharpe in a journal entry, ADR, or PR description.
