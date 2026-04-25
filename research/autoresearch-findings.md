# Autoresearch Loop — Trading Bot Mapping
**Agent:** AutoresearchAnalyser (Agent 2)  
**Generated:** 2026-04-25  
**Source skill:** `/Users/ltmas/Repo/experiments/demo/AI_Skills/autoresearch/`  
**Target bot:** `/Users/ltmas/trading-bot-workspace/bot/`  
**Target metrics:** Sharpe > 1.5 | Max drawdown < 5% | Win rate > 45%

---

## 1. Autoresearch Loop — Trading Bot Mapping

The autoresearch skill defines an 8-phase self-improvement loop sourced from `SKILL.md` and `references/autonomous-loop-protocol.md`. Each phase maps directly to a trading-bot equivalent as follows.

| Phase | Original Framework (code/ML) | Trading Bot Equivalent | Concrete Implementation |
|-------|------------------------------|------------------------|------------------------|
| **1 — Review** | Read in-scope files, git log, last 10-20 results log entries. Identify what worked, what failed, what's untried. | Read current parameter state from `config.yaml`, last 20 rows of `autoresearch/results.tsv`, git log of parameter commits. Identify which parameter changes improved Sharpe without breaching drawdown guard. | Files: `bot/config.yaml`, `bot/autoresearch/results.tsv`. Command: `git log --oneline -20` in `bot/` |
| **2 — Ideate** | Pick next change. Priority: fix crashes → exploit successes → explore new → combine near-misses → simplify → radical. | Pick next parameter to mutate. Priority: if last change improved Sharpe, try adjacent value in same direction (hill-climb). If 5 consecutive discards, try a different parameter entirely or cross-parameter combination (e.g., EMA fast + RSI oversold). | Search strategy: hill-climbing with random restart after 5 consecutive discards. See Section 4 for full algorithm. |
| **3 — Modify** | Make ONE focused change to in-scope files. Write description before changing. | Change exactly ONE parameter in `bot/config.yaml` (or a dedicated `bot/autoresearch/params.yaml` overlay). Write description: "ema_fast_period 9→11". Never change two parameters simultaneously. | File: `bot/autoresearch/params.yaml` (overlay on `config.yaml`). One key-value change per iteration. |
| **4 — Commit** | `git add <files> && git commit -m "experiment: <description>"` before running verification. | `git add bot/autoresearch/params.yaml && git commit -m "experiment: ema_fast_period 9→11"`. Commit BEFORE backtest so rollback is clean via `git reset --hard HEAD~1`. | Working dir: `bot/`. Commit format: `"experiment: {param_name} {old_val}→{new_val}"` |
| **5 — Verify** | Run mechanical metric command. Parse output for a single float. Kill after 2x normal time. | Run backtest engine. Parse Sharpe ratio as float from stdout. Timeout at 3× median backtest duration (tracked in results.tsv). | Command: `python backtest/engine.py --metric sharpe` (from `config.yaml: autoresearch.metric_command`). Parse: `grep "^SHARPE" output | awk '{print $2}'` |
| **6 — Guard** | Run guard command (pass/fail only). Must ALWAYS pass to protect existing behavior. | Run guard command that checks BOTH max drawdown < 5% AND win rate > 45%. Returns exit code 0 if both constraints satisfied, exit code 1 otherwise. Guard is separate from metric to preserve the distinction between "is it better?" (Sharpe) and "is it still safe?" (drawdown + win rate). | Command: `python backtest/engine.py --guard` (from `config.yaml: autoresearch.guard_command`). Returns `PASS` (exit 0) or `FAIL: drawdown=X.XX% exceeds 5%` (exit 1). |
| **7 — Decide** | Keep if metric improved AND guard passed. Revert otherwise. Rework up to 2× if guard fails but metric improved. | Keep if Sharpe improved AND drawdown < 5% AND win rate > 45%. Revert via `git reset --hard HEAD~1` if Sharpe same/worse OR guard fails. On guard fail + Sharpe improvement: try tightening the offending parameter (e.g., reduce risk_per_trade) before discarding — max 2 rework attempts. | Decision: `IF sharpe > best_sharpe AND guard_exit==0: keep`. Else: `git reset --hard HEAD~1`. Log outcome to `results.tsv`. |
| **8 — Log** | Append TSV row: iteration, commit, metric, delta, guard, status, description. Print summary every 10 iterations. | Append to `bot/autoresearch/results.tsv`: iteration, commit, sharpe, delta, guard (pass/fail), status (keep/discard/crash), description. Print progress every 10 iterations: `"Iteration 20: Sharpe 1.42 → 1.61, 7 keeps / 12 discards"`. | File: `bot/autoresearch/results.tsv`. Format defined in Section 3. |

---

## 2. Parameter Space Definition

Parameters sourced from the task specification, expanded with values from `knowledge-base.md` strategies. The bot currently targets EMA crossover + Bollinger Band mean-reversion + RSI confirmation — the three main strategy families in the knowledge base that map to `config.yaml` instruments (EURUSD, GBPUSD, H1 timeframe).

### 2.1 EMA Crossover Strategy (Strategy 1.6 — MT4 Trend-Pullback)

| Parameter | Initial Value | Min | Max | Step | Strategy |
|-----------|--------------|-----|-----|------|----------|
| `ema_fast_period` | 9 | 5 | 20 | 1 | EMA Crossover |
| `ema_slow_period` | 21 | 15 | 50 | 1 | EMA Crossover |
| `ema_trend_period` | 50 | 40 | 100 | 5 | EMA Crossover (trend filter) |
| `atr_sl_multiplier` | 1.5 | 1.0 | 3.0 | 0.1 | EMA Crossover + all strategies |
| `risk_per_trade_pct` | 1.0 | 0.5 | 2.0 | 0.1 | All strategies (position sizing) |

**Rationale:** Jim Brown (1.6) uses 50/100/240 MA stack; fast/slow EMA crossover is the signal trigger. ATR stop at 1.5× is mid-range for FX. Chan confirms 1–2% risk per trade is industry standard.

### 2.2 Bollinger Band Mean-Reversion Strategy (Strategy 1.4)

| Parameter | Initial Value | Min | Max | Step | Strategy |
|-----------|--------------|-----|-----|------|----------|
| `bb_period` | 20 | 10 | 30 | 1 | Bollinger Band |
| `bb_std_dev` | 2.0 | 1.5 | 3.0 | 0.1 | Bollinger Band |
| `bb_exit_std` | 1.0 | 0.5 | 1.5 | 0.1 | Bollinger Band (exit threshold) |

**Rationale:** Murphy/Chan standard is 20-period, ±2 std entry, ±1 std exit. Knowledge base warns that 5-min Bollinger on ES collapses from +3 Sharpe to -3 with 1bp transaction cost — H1 FX is more appropriate for mean-reversion.

### 2.3 RSI Confirmation Filter (Strategy 1.4 + 1.9)

| Parameter | Initial Value | Min | Max | Step | Strategy |
|-----------|--------------|-----|-----|------|----------|
| `rsi_period` | 14 | 7 | 21 | 1 | RSI Filter |
| `rsi_overbought` | 70 | 65 | 80 | 1 | RSI Filter |
| `rsi_oversold` | 30 | 20 | 35 | 1 | RSI Filter |

**Rationale:** Murphy standard (14, 70/30). RSI is a confirmation filter, not the primary signal. Naked Forex warns RSI lags price; keep search range modest.

### 2.4 Risk Management Parameters

| Parameter | Initial Value | Min | Max | Step | Strategy |
|-----------|--------------|-----|-----|------|----------|
| `kelly_fraction` | 0.25 | 0.10 | 0.50 | 0.05 | All (position sizing) |
| `daily_loss_limit_pct` | 2.0 | 1.0 | 3.0 | 0.5 | All (circuit breaker) |
| `atr_period` | 14 | 10 | 20 | 1 | All (stop placement) |

**Rationale:** Quarter-Kelly (0.25) is conservative and matches `config.yaml`. Chan: full Kelly leads to large drawdowns. ATR period 14 is standard per Kaufman/TSAM.

### Full Parameter Summary (17 parameters total)

Note: Knowledge base (Chan, Section 4.3) warns against >5 free parameters in a single backtest due to data snooping risk. The autoresearch loop must **vary one parameter at a time** and use walk-forward validation on the best-performing checkpoints to avoid overfitting.

---

## 3. Metric Command Specification

### 3.1 Primary Metric — Sharpe Ratio

```bash
# Returns Sharpe as parseable float on stdout
python backtest/engine.py --metric sharpe

# Expected stdout format:
# SHARPE 1.742
# (engine writes exactly one line with "SHARPE <float>")

# Parse command (used by loop controller):
python backtest/engine.py --metric sharpe | grep "^SHARPE" | awk '{printf "%.4f\n", $2}'
```

Direction: **higher is better**. Target: > 1.5.

Calculation: `annualized_sharpe = sqrt(N_T) * mean(excess_returns) / std(excess_returns)` where `N_T = 252 * 24` for H1 FX (24 trading hours/day × 252 trading days). Source: knowledge-base.md Section 4.4.

### 3.2 Guard Command — Drawdown + Win Rate

```bash
# Returns exit code 0 (PASS) or exit code 1 (FAIL) + reason on stdout
python backtest/engine.py --guard

# Expected stdout format on pass:
# GUARD PASS drawdown=3.21% win_rate=51.4%

# Expected stdout format on fail:
# GUARD FAIL drawdown=6.43% exceeds 5.0% threshold
# GUARD FAIL win_rate=41.2% below 45.0% threshold

# Guard thresholds sourced from config.yaml:
# autoresearch.max_drawdown_guard: 0.05
# autoresearch.min_win_rate_guard: 0.45
```

Guard is pass/fail only. Exit code is the decision gate — the loop controller reads exit code, not stdout text. Stdout is for logging only.

### 3.3 Results TSV Schema

File: `bot/autoresearch/results.tsv`

```
# metric_direction: higher_is_better
# target: sharpe > 1.5 | drawdown < 5% | win_rate > 45%
iteration	commit	sharpe	delta	guard	status	param_changed	description
0	a1b2c3d	1.12	0.00	pass	baseline	—	initial state — default config.yaml params
1	b2c3d4e	1.18	+0.06	pass	keep	ema_fast_period	ema_fast_period 9→11
2	-	1.09	-0.09	-	discard	ema_fast_period	ema_fast_period 11→13 (worse)
3	-	0.00	0.00	-	crash	bb_period	bb_period 20→10 (backtest engine OOM)
4	c3d4e5f	1.23	+0.05	pass	keep	rsi_oversold	rsi_oversold 30→28
5	-	1.31	+0.08	fail	discard	risk_per_trade_pct	risk_per_trade_pct 1.0→1.5 (drawdown 7.2%)
```

Additional column `param_changed` beyond the original autoresearch schema — essential for trading bot to track which parameter drove each result and detect parameter interaction patterns.

### 3.4 Parsing Logic (loop controller pseudocode)

```python
import subprocess, re

def run_metric() -> float:
    result = subprocess.run(
        ["python", "backtest/engine.py", "--metric", "sharpe"],
        capture_output=True, text=True, timeout=300
    )
    if result.returncode != 0:
        raise RuntimeError(f"Backtest crashed: {result.stderr}")
    match = re.search(r"^SHARPE\s+([\d.]+)", result.stdout, re.MULTILINE)
    if not match:
        raise ValueError(f"Could not parse Sharpe from output: {result.stdout}")
    return float(match.group(1))

def run_guard() -> bool:
    result = subprocess.run(
        ["python", "backtest/engine.py", "--guard"],
        capture_output=True, text=True, timeout=300
    )
    return result.returncode == 0  # 0 = PASS, 1 = FAIL
```

---

## 4. Iteration Engine Design

### 4.1 Parameter Selection Strategy

The loop uses **coordinate descent with hill-climbing**, not random search or Bayesian optimization. Rationale: the parameter space has 17 dimensions but the loop modifies one at a time, making coordinate descent natural. Bayesian is overkill at this parameter count and would require a surrogate model that adds complexity without clear benefit at <200 iterations.

**Selection algorithm:**

```
1. Maintain a "current best params" dict (starts from config.yaml defaults)
2. Maintain a "parameter cursor" index cycling through all 17 parameters
3. Each iteration:
   a. Select parameter at cursor position
   b. Propose new value: best_val + step_size (alternating +/- directions)
   c. Clamp to [min, max]
   d. If proposed value already tried for this parameter: skip to next parameter
4. After 5 consecutive discards on one parameter: advance cursor to next parameter
5. After full cycle with no improvement: enter exploration mode (random parameter + random value in range)
6. After goal achieved (Sharpe > 1.5 with guard passing): switch to fine-tuning mode (step_size / 2)
```

**Direction alternation:** For each parameter, try +step first, then -step. Track which direction is currently active per parameter in results.tsv (encoded in description field).

### 4.2 Keep / Rollback Decision

```
IF sharpe_new > sharpe_best AND guard_exit == 0:
    STATUS = "keep"
    update current_best_params
    # commit stays, git history advances

ELIF sharpe_new > sharpe_best AND guard_exit != 0:
    # Metric improved but guard failed (drawdown or win rate breached)
    git reset --hard HEAD~1
    # Rework attempt: reduce risk_per_trade_pct by 0.1 as compensation
    # (guard failures are almost always caused by position sizing, not the
    #  strategy parameter itself — this is the standard rework heuristic)
    FOR attempt IN 1..2:
        apply rework (reduce risk_per_trade by 0.1)
        commit, re-run verify + guard
        IF both pass: STATUS = "keep (reworked)"; BREAK
        git reset --hard HEAD~1
    IF still failing: STATUS = "discard (guard failed)"

ELIF sharpe_new <= sharpe_best:
    STATUS = "discard"
    git reset --hard HEAD~1

ELIF crashed:
    # max 3 fix attempts for syntax/import errors
    # for OOM: revert, skip this parameter direction
    STATUS = "crash"
    git reset --hard HEAD~1
```

### 4.3 Checkpoint Strategy

State saved per iteration (in addition to git commit):

| State item | Where saved | When updated |
|------------|-------------|--------------|
| Current best params | `bot/autoresearch/best_params.yaml` | On every "keep" |
| Current best Sharpe | `bot/autoresearch/best_params.yaml` (inline) | On every "keep" |
| Full results log | `bot/autoresearch/results.tsv` | After every iteration |
| Baseline metrics | Row 0 of `results.tsv` | Once at setup |
| Parameter search state | `bot/autoresearch/search_state.json` | After every iteration |

`search_state.json` format:
```json
{
  "iteration": 42,
  "cursor_index": 3,
  "best_sharpe": 1.61,
  "consecutive_discards": 2,
  "param_directions": {
    "ema_fast_period": "+",
    "ema_slow_period": "-",
    ...
  },
  "tried_values": {
    "ema_fast_period": [9, 10, 11, 12],
    ...
  }
}
```

This state enables resuming an interrupted loop without losing exploration history.

### 4.4 Convergence Criteria

The loop stops (or transitions to fine-tuning) when any of the following are true:

| Criterion | Condition | Action |
|-----------|-----------|--------|
| **Goal achieved** | Sharpe > 1.5 AND guard passing for 3 consecutive keeps | Switch to fine-tuning mode (step_size / 2), or stop if bounded |
| **Parameter space exhausted** | All 17 parameters tried in both directions with no improvement | Stop and report "no further improvement found" |
| **Iteration budget** | `current_iteration >= config.autoresearch.iterations` (default: 50) | Stop and print final summary |
| **Stuck** | 15 consecutive discards across all parameters | Enter radical exploration: randomize 3 parameters simultaneously (one-time experiment), then revert if no improvement |
| **Sharpe plateau** | Best Sharpe unchanged for 20 iterations | Log warning, try combining the top-2 individual parameter improvements into one commit |

Note: The knowledge base (Chan, Section 4.3) confirms a Sharpe ≥ 1.5 with 2,739 data points gives statistical confidence that true Sharpe > 1.0. The loop should log how many bars were used in each backtest to track statistical validity.

---

## 5. Integration Points

### 5.1 Bot Modules the Loop Calls

```
autoresearch loop controller
    ├── reads:   bot/config.yaml              (base configuration)
    ├── writes:  bot/autoresearch/params.yaml (parameter overlay per iteration)
    ├── calls:   backtest/engine.py --metric sharpe   (Phase 5: Verify)
    ├── calls:   backtest/engine.py --guard           (Phase 6: Guard)
    ├── writes:  bot/autoresearch/results.tsv         (Phase 8: Log)
    ├── writes:  bot/autoresearch/best_params.yaml    (on keep)
    ├── writes:  bot/autoresearch/search_state.json   (after every iteration)
    └── uses:    git (commit/reset) in bot/ working directory
```

The loop does NOT call:
- `core/bridge/mt5_client.py` — live trading bridge, never touched during autoresearch
- `core/bridge/http_server.py` — HTTP server for MT5 EA, orthogonal to backtesting

### 5.2 Backtest Engine Interface

The backtest engine (`backtest/engine.py`) must implement these CLI flags to satisfy the loop contract:

```bash
# Metric mode: run full backtest, print "SHARPE <float>" to stdout, exit 0
python backtest/engine.py --metric sharpe

# Guard mode: run full backtest, check drawdown + win_rate constraints
# exit 0 if both pass, exit 1 if either fails
# print "GUARD PASS ..." or "GUARD FAIL ..." to stdout
python backtest/engine.py --guard

# Dual mode: run once, output both (avoids running backtest twice per iteration)
python backtest/engine.py --metric sharpe --guard
# stdout:
#   SHARPE 1.742
#   GUARD PASS drawdown=3.21% win_rate=51.4%
# exit code: 0 if guard passes, 1 if guard fails (metric still extracted from stdout)
```

The engine loads parameters from `bot/autoresearch/params.yaml` if it exists, otherwise falls back to `bot/config.yaml`. This overlay pattern means the engine does not need to be modified per iteration — only the overlay file changes.

Parameter overlay format (`bot/autoresearch/params.yaml`):
```yaml
# Autoresearch parameter overlay — written by loop controller
# Overrides matching keys in config.yaml for backtest only
strategy:
  ema_fast_period: 11
  ema_slow_period: 21
  bb_period: 20
  bb_std_dev: 2.0
  rsi_period: 14
  rsi_overbought: 70
  rsi_oversold: 30
  atr_sl_multiplier: 1.5
risk:
  max_risk_per_trade: 0.01
  kelly_fraction: 0.25
  atr_period: 14
```

### 5.3 Results TSV Write Protocol

```python
import csv, datetime, subprocess
from pathlib import Path

RESULTS_FILE = Path("bot/autoresearch/results.tsv")

def log_iteration(iteration, commit, sharpe, best_sharpe, guard_passed,
                  status, param_changed, description):
    delta = sharpe - best_sharpe
    guard_str = "pass" if guard_passed else "fail"
    if status in ("discard", "crash") and not guard_passed:
        guard_str = "-"

    row = [
        iteration,
        commit[:7] if commit else "-",
        f"{sharpe:.4f}",
        f"{delta:+.4f}",
        guard_str,
        status,
        param_changed,
        description,
    ]

    with open(RESULTS_FILE, "a", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(row)
```

File is NOT committed to git (add `autoresearch/results.tsv` and `autoresearch/search_state.json` to `.gitignore`). Only `best_params.yaml` is committed when a new best is found, via a separate "checkpoint:" commit distinct from "experiment:" commits.

### 5.4 Loop Entry Point

```bash
# Run bounded loop (50 iterations from config.yaml default)
cd /Users/ltmas/trading-bot-workspace/bot
python autoresearch/loop.py

# Run with explicit iteration limit
python autoresearch/loop.py --iterations 100

# Run until goal achieved (unlimited)
python autoresearch/loop.py --unlimited

# Resume interrupted loop (reads search_state.json)
python autoresearch/loop.py --resume
```

The loop controller will live at `bot/autoresearch/loop.py` (to be implemented in Phase 3 of the project).

---

## 6. Key Design Decisions and Constraints

### 6.1 Why Coordinate Descent over Bayesian Optimization

Bayesian optimization (e.g., Optuna, scikit-optimize) would be the standard choice for hyperparameter search with expensive evaluations. For this bot, coordinate descent is preferred because:

1. **Interpretability**: Each iteration changes one named parameter. The trader can read `results.tsv` and understand why Sharpe improved. Bayesian suggests opaque multi-parameter moves.
2. **Domain constraints**: Parameters have non-independent relationships (e.g., `ema_fast` must stay < `ema_slow`). Coordinate descent enforces these naturally. Bayesian requires explicit constraint encoding.
3. **Overfitting risk**: Chan warns >5 free parameters increases overfitting significantly. The loop should track "effective degrees of freedom used" and stop optimizing before the Deflated Sharpe Ratio penalty becomes too large.

### 6.2 Backtest Speed Requirement

Per core-principles.md principle #4 ("Verification Must Be Fast"), the backtest must complete in under 60 seconds per iteration for the loop to be practical. At 50 iterations: 50 min total for a bounded run. If backtest exceeds 60s, reduce the data window used during autoresearch (e.g., 1 year of H1 data instead of 5 years), then validate the winning params on the full historical window separately.

### 6.3 Transaction Cost Warning

Knowledge base (Chan, Section 1.4) warns that a 1bp transaction cost can flip Sharpe from +3 to -3 on high-frequency mean-reversion. The backtest engine must include realistic spread costs (config: EURUSD spread ~2 pips, GBPUSD ~3 pips on H1) in all Sharpe calculations. Autoresearch on pre-cost Sharpe is meaningless for live trading.

---

*End of autoresearch-findings.md*
