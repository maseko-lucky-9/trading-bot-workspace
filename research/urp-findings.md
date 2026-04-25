# Universal Research Pipeline Findings
## MT5 Autonomous Trading Bot for Apple Silicon

**Objective**: Design a full autonomous trading bot for MetaTrader 5 (MT5), optimized for Apple Silicon (M5 Pro, 24GB RAM), trained on domain knowledge from an Obsidian vault and iteratively self-improved using structured research and error-correction frameworks.

**Collection Date**: 2026-04-24  
**Sources**: Exa, WebSearch, Firecrawl  
**Entries Collected**: 28  
**KRQs Addressed**: 5/5

---

## Executive Summary

Building an autonomous MT5 trading bot on Apple Silicon requires solving the Windows-only MT5 Python library constraint. Three viable bridge architectures emerged: Docker+Wine+QEMU (silicon-metatrader5), ZeroMQ socket bridges (MQL5 EA ↔ Python), and file-based JSON IPC. For backtesting, VectorBT dominates speed benchmarks but RaptorBT (Rust+Python) shows 5800x improvements on Apple Silicon. Self-improving architectures combine Bayesian optimization (Optuna/TPE) with RL (PPO) or evolutionary algorithms, using Sharpe ratio as the mechanical metric. Risk management must implement fractional Kelly (25-50%), tiered drawdown limits (10%→30% reduction, 20%→pause), and CVaR constraints.

---

## KRQ1: MT5-macOS Bridge Methods

### Claim Validation
**C1: "MetaTrader5 Python library only works on Windows"** → **CORROBORATED**  
The official `metatrader5` PyPI package provides only `win_amd64` wheels. No macOS distribution exists.

**C2: "ZMQ bridges can reliably connect Python on macOS to MT5 on Windows VM"** → **CORROBORATED**  
Multiple production implementations (MT5BridgeAPI, mql-zmq, NovaQuantLab architecture) confirm microsecond-latency ZMQ bridges with heartbeat protocols.

### Bridge Architectures (Ranked by Production Readiness)

| Method | Latency | Complexity | Maintenance | Recommended For |
|--------|---------|------------|-------------|-----------------|
| **1. ZeroMQ Bridge** | μs-level | High | Medium | Production trading, low latency |
| **2. silicon-metatrader5** | ~100ms | Medium | Low | Rapid prototyping, research |
| **3. File-based JSON IPC** | ~1s | Low | Low | Swing/position trading |
| **4. REST API (cloud)** | ~200ms | Low | Low | Multi-device, no local MT5 |

### Recommended Architecture: ZeroMQ Bridge

```
macOS M5 Pro                          UTM Windows 11 ARM VM
┌─────────────────────┐              ┌────────────────────────┐
│ Python Trading Bot  │◄───TCP───►│ MT5 Terminal           │
│   └── pyzmq client  │   5555/6   │   └── MQL5 EA (ZMQ)    │
│                     │              │       └── mql-zmq DLL │
└─────────────────────┘              └────────────────────────┘
```

**Implementation Notes**:
- Dual socket pattern: PUB/SUB for market data, REQ/REP for order execution
- Heartbeat every 5s with 500ms timeout
- Curve25519 encryption for security (MT5BridgeAPI pattern)
- Allow DLL imports required in MT5 settings

**Alternative for Simpler Setup**: `mt5-mac-data-bridge` uses file-based JSON IPC — no sockets, no Wine, no Docker. EA writes `{SYMBOL}_price.json`, Python reads and writes `commands.json`. Suitable for swing trading where 1s latency is acceptable.

---

## KRQ2: Backtesting Frameworks for Apple Silicon

### Framework Comparison (Apple Silicon Benchmarks)

| Framework | Speed (1K bars) | Speed (50K bars) | RAM (5yr) | ARM Native | Live Trading |
|-----------|-----------------|------------------|-----------|------------|--------------|
| **RaptorBT** | 0.25ms | 1.7ms | Low | Yes (Rust) | No |
| **VectorBT** | 1460ms* | 43ms | Moderate | Yes (Conda) | No |
| **Zipline-reloaded** | — | 4.2s | 1.8GB | Partial | No |
| **Backtrader** | — | 15.6s | 600MB | Yes | Yes |
| **NautilusTrader** | — | — | Low | Yes (Rust) | Yes |

*VectorBT first run includes Numba JIT compilation; subsequent runs faster

### Recommendation: VectorBT + RaptorBT Hybrid

- **Research/Optimization**: VectorBT for vectorized parameter sweeps (1000 combos in <1min)
- **Production Backtest**: RaptorBT for final validation (25x faster on 50K bars)
- **Live Trading**: NautilusTrader if unified backtest+live needed; else custom Python

**VectorBT Installation on Apple Silicon (2026)**:
```bash
# Requires Conda for Numba ARM wheels
brew install miniforge
conda create -n trading python=3.11
conda activate trading
conda install numba
pip install vectorbt
```

### Claim Validation
**C3: "VectorBT provides faster backtesting than Backtrader on Apple Silicon"** → **CORROBORATED**  
Benchmarks show VectorBT 167x faster than Backtrader on 100M tick datasets. However, RaptorBT (Rust) is 25-5800x faster than VectorBT depending on dataset size.

---

## KRQ3: Self-Improving Trading Bot Architectures

### Architecture Patterns

#### 1. Autoresearch-Style Constraint-Metric-Iteration Loop
From the autoresearch repo analysis:
```
┌─── Constraint: Sharpe > 1.5, MaxDD < 5%, WinRate > 45%
│
├─► REVIEW   → Read current state, last 20 commits
├─► IDEATE   → Pick next parameter change
├─► MODIFY   → One atomic change to strategy/risk module
├─► COMMIT   → git commit before verification (for rollback)
├─► VERIFY   → python backtest/engine.py --metric sharpe
├─► GUARD    → python backtest/engine.py --guard (DD + win rate)
├─► DECIDE   → Improved + guard passed? KEEP : ROLLBACK
├─► LOG      → Append TSV: iteration, commit, metric, delta
└─► LOOP     → Repeat until Sharpe > 1.5 or N iterations
```

#### 2. Bayesian Optimization (Optuna/TPE)
- Builds Gaussian Process surrogate of objective function
- Acquisition function (EI, UCB) balances exploration/exploitation
- Efficient for <50 parameters, continuous spaces
- Implementation: `optuna.create_study(direction='maximize')` with Sharpe ratio objective

#### 3. Population-Based Training (DeepMind PBT)
- Population of agents train in parallel
- Poor performers inherit weights/hyperparameters from better performers
- Asynchronous exploit/explore every T training steps
- Fitness metric: Sharpe ratio or cumulative PnL

#### 4. Reinforcement Learning Self-Improvement
- Agent: observes market state → takes action (buy/sell/hold) → receives reward
- PPO (Stable-Baselines3) recommended for continuous learning
- Reward design: risk-adjusted returns, drawdown penalties, Sharpe optimization
- Self-improving via: online learning, periodic retraining, adaptive rewards

### Recommended Architecture: Autoresearch + Bayesian Hybrid

```python
# Phase 1: Bayesian coarse search (Optuna, 100 trials)
study = optuna.create_study(direction='maximize', sampler=TPESampler())
study.optimize(objective_sharpe, n_trials=100)
best_params = study.best_params

# Phase 2: Autoresearch fine-tuning (iterative)
for iteration in range(50):
    param_delta = sample_neighborhood(best_params)
    sharpe = run_backtest(param_delta)
    if sharpe > current_best and passes_guard():
        git_commit(f"experiment: {param_delta}")
        current_best = sharpe
        best_params = param_delta
    else:
        git_rollback()
    log_result(iteration, sharpe)
```

---

## KRQ4: Risk Management Frameworks and Production Safeguards

### Three-Layer Risk Framework

| Layer | Control | Threshold | Action |
|-------|---------|-----------|--------|
| **Trade** | Max risk per trade | 1% of equity | Reject order if exceeded |
| **Strategy** | Strategy drawdown | 15% from peak | Reduce position size 50% |
| **Portfolio** | Portfolio drawdown | 20% from peak | Halt all trading |

### Position Sizing: Fractional Kelly

```
Kelly % = (WinRate × AvgWin - LossRate × AvgLoss) / AvgWin

Production: Use 25-50% of Kelly recommendation
- Full Kelly: 20-50% drawdowns (unacceptable)
- Half Kelly: ~15% max drawdown
- Quarter Kelly: ~8% max drawdown (recommended start)

Recalculate: Daily for active trading, weekly for swing
Update Kelly estimate: Every 3-6 months with rolling lookback
```

### Circuit Breaker Implementation

```python
class CircuitBreaker:
    def __init__(self):
        self.daily_loss_limit = 0.02      # 2% daily max loss
        self.trailing_dd_limit = 0.10     # 10% from peak
        self.strategy_dd_limit = 0.15     # 15% strategy-level
        self.portfolio_dd_limit = 0.20    # 20% hard stop
    
    def check(self, current_dd, daily_pnl):
        if daily_pnl < -self.daily_loss_limit:
            return "HALT_DAILY"
        if current_dd > self.trailing_dd_limit:
            return "REDUCE_50%"
        if current_dd > self.strategy_dd_limit:
            return "REDUCE_80%"
        if current_dd > self.portfolio_dd_limit:
            return "HALT_ALL"
        return "CONTINUE"
```

### Production Safeguards Checklist

1. **Drawdown limits** (daily, trailing, max) — non-negotiable
2. **Position size scaling** — reduce at 10% DD, pause at 20%
3. **Heartbeat monitoring** — kill trading if bridge connection lost
4. **Order deduplication** — prevent duplicate order submission
5. **CVaR constraint** — optimize for worst 5% scenarios, not just EV
6. **Kill switch** — manual override to halt all trading instantly
7. **Paper trading mode** — mandatory before live deployment
8. **Parameter uncertainty** — assume 30% error in model estimates

---

## KRQ5: Apple Silicon Memory and CPU Optimization

### Memory Budget (24GB Unified Memory)

| Component | Allocation | Notes |
|-----------|------------|-------|
| macOS + headroom | 2 GB | System overhead |
| UTM Windows VM | 6-8 GB | MT5 + MQL5 EA |
| Python bot (data + strategy) | 8-10 GB | Pandas DataFrames, NumPy arrays |
| Backtest loop | 4-6 GB | VectorBT/RaptorBT working set |
| ML models (if used) | <60% of remaining | MLX/PyTorch MPS |

**Rule**: Keep model weights under 60% of available unified memory for KV cache + runtime headroom.

### Parallelism Patterns

```python
# Pattern 1: Asyncio for I/O-bound (data feed, order submission)
async def data_feed():
    while True:
        tick = await bridge.recv_tick()  # non-blocking
        await process_tick(tick)

# Pattern 2: Multiprocessing for CPU-bound (backtesting)
from multiprocessing import Pool

def run_backtest(params):
    return VectorBT.run(params)

with Pool(processes=6) as pool:  # Use P-cores
    results = pool.map(run_backtest, param_grid)

# Pattern 3: Concurrent.futures for mixed workloads
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor

# IO-bound
with ThreadPoolExecutor(max_workers=4) as io_pool:
    prices = list(io_pool.map(fetch_prices, symbols))

# CPU-bound
with ProcessPoolExecutor(max_workers=6) as cpu_pool:
    backtests = list(cpu_pool.map(run_backtest, params))
```

### Apple Silicon Specific Optimizations

1. **NumPy/Pandas**: Use ARM-native wheels (Conda/Miniforge)
2. **PyTorch**: MPS backend for GPU acceleration (`device='mps'`)
3. **MLX**: Native Apple Silicon ML framework (2026 mature)
4. **Numba**: JIT compilation for hot loops (requires Conda)
5. **Memory-mapped files**: For large historical data (avoid loading all to RAM)

---

## Claims Register (Final)

| ID | Claim | Verdict | Evidence |
|----|-------|---------|----------|
| C1 | MetaTrader5 Python library only works on Windows | CORROBORATED | PyPI shows win_amd64 only; MQL5 docs confirm |
| C2 | ZMQ bridges reliably connect Python on macOS to MT5 | CORROBORATED | MT5BridgeAPI, mql-zmq, NovaQuantLab implementations |
| C3 | VectorBT faster than Backtrader on Apple Silicon | CORROBORATED | 167x faster on 100M ticks; RaptorBT even faster |

---

## Recommended Implementation Path

1. **Environment**: UTM + Windows 11 ARM + MT5 terminal
2. **Bridge**: ZeroMQ (PUB/SUB + REQ/REP) with mql-zmq DLL
3. **Backtesting**: VectorBT for optimization, RaptorBT for validation
4. **Self-improvement**: Autoresearch loop with Sharpe ratio metric
5. **Risk**: Fractional Kelly (25%) + three-layer circuit breakers
6. **Parallelism**: Asyncio for data, multiprocessing for backtests

---

## Sources

- [silicon-metatrader5](https://github.com/bahadirumutiscimen/silicon-metatrader5)
- [MT5BridgeAPI](https://github.com/ding9736/MT5BridgeAPI)
- [mql-zmq](https://github.com/dingmaotu/mql-zmq)
- [mt5-mac-data-bridge](https://github.com/callmeartan/mt5-mac-data-bridge)
- [RaptorBT](https://github.com/Alphabench/raptorbt)
- [VectorBT](https://github.com/polakowo/vectorbt)
- [NovaQuantLab MT5 Integration Guide](https://novaquantlab.com/metatrader-5-python-integration-2026-best-practices-for-algorithmic-trading/)
- [Drawdown Management in Algorithmic Trading](https://arrowalgo.com/drawdown-management-algorithmic-trading/)
- [Kelly Criterion for Risk Management](https://medium.com/@tmapendembe_28659/risk-management-using-kelly-criterion-2eddcf52f50b)
