# MT5 Autonomous Trading Bot — Unified Implementation Plan

**Author:** Agent 4 (ArchitectPlanner)
**Generated:** 2026-04-25
**Target executor:** Agent 5 (ImplementationExecutor) via Ralph loop
**Workspace:** `/Users/ltmas/trading-bot-workspace/bot/`
**Python:** 3.12.9 (`.venv`) — ARM64 native on macOS M5 Pro 24GB
**Mode default:** paper (live requires `bot.mode=live` AND `--confirm-live` CLI flag)

This document is the contract between the research/planning phase and the implementation phase. Every module in `core/`, `backtest/`, `autoresearch/`, and `knowledge/` is specified here. Stories US-001..US-010 in `bot/ralph/prd.json` consume this plan one at a time via the Ralph loop.

---

## 1. Architecture Overview

### 1.1 Process / Data Flow Diagram

```
                              macOS M5 Pro (host)
+------------------------------------------------------------------------------+
|                                                                              |
|   +-------------------+                                                      |
|   |  main.py (CLI)    |  --confirm-live | --mode paper|live | --resume       |
|   +---------+---------+                                                      |
|             |                                                                |
|             v                                                                |
|   +-------------------+      +---------------------+                         |
|   | CheckpointManager |<---->|     BotState        |  pickle + JSON summary  |
|   | (auto every 5min) |      | (positions,equity,  |  checkpoints/*.pkl      |
|   +-------------------+      |  research_iter,..)  |                         |
|                              +----------+----------+                         |
|                                         |                                    |
|             +---------------------------+----------------------+             |
|             v                                                  v             |
|   +-------------------+                              +-------------------+   |
|   | PerformanceTracker|<----- closed trades --------+|  Order Manager    |   |
|   |  Sharpe / DD / WR |                              |  (paper | live)   |   |
|   +-------------------+                              +---------+---------+   |
|             ^                                                  |             |
|             |  trade results                                   |             |
|             |                                                  v             |
|   +-------------------+   sized orders   +---------------------------------+ |
|   |   RiskManager     |<-----------------|       Strategy Pipeline         | |
|   |  ATR sizing,      |                  |  base -> ema_crossover ->       | |
|   |  Kelly, breakers  |                  |  bollinger -> rsi_filter        | |
|   +---------+---------+                  +-----------------+---------------+ |
|             |  position multiplier                         ^                 |
|             |                                              | features        |
|             |                                              |                 |
|             |                              +---------------+---------------+ |
|             |                              |        Data Pipeline          | |
|             |                              |  feed.py (live ticks)         | |
|             |                              |  history.py (OHLCV cache)     | |
|             |                              +---------------+---------------+ |
|             |                                              ^                 |
|             |                                              | HTTP            |
|             v                                              v                 |
|   +-------------------------------------------------------+----+             |
|   |          BridgeClient  (core/bridge/http_client.py)        |             |
|   |   GET /state /history /ping  |  POST /order               |             |
|   +-----------------------------+------------------------------+             |
|                                 |                                            |
|                            HTTP | 192.168.64.1:8080                          |
+---------------------------------|--------------------------------------------+
                                  |
+---------------------------------v--------------------------------------------+
|                          UTM Windows 11 ARM VM                                |
|   +----------------------------+        +-----------------------------+      |
|   |   FastAPI HTTP Bridge      |<------>|   MT5 Terminal + EA (MQL5)  |      |
|   |   core/bridge/http_server  |  POST  |   pushes ticks/account/H1   |      |
|   |   (already live & tested)  |  GET   |   polls /command            |      |
|   +----------------------------+        +-----------------------------+      |
+------------------------------------------------------------------------------+


+--- OFFLINE LAYERS (do not touch live trading state) ----------------------+
|                                                                          |
|  Backtest pipeline:                                                      |
|    backtest/engine.py --metric sharpe | --guard | --params <yaml>        |
|       reads bridge_data/history/{symbol}_{tf}.parquet                    |
|       loads strategy from core/strategy/*                                |
|       runs vectorised event simulation                                   |
|       prints "SHARPE <float>" + "GUARD PASS|FAIL ..." to stdout          |
|       exits 0 (guard pass) or 1 (guard fail)                             |
|                                                                          |
|  Autoresearch loop (autoresearch/loop.py):                               |
|    Phase 1 Review -> Phase 2 Ideate -> Phase 3 Modify (params.yaml)      |
|    -> Phase 4 Commit -> Phase 5 Verify (--metric) -> Phase 6 Guard       |
|    -> Phase 7 Decide (keep/rollback) -> Phase 8 Log (results.tsv)        |
|                                                                          |
|  KnowledgeBase (knowledge/base.py):                                      |
|    indexes research/knowledge-base.md by heading                         |
|    seeds parameter bounds for the autoresearch loop                      |
|                                                                          |
|  Ralph loop (bot/ralph/CLAUDE.md driver):                                |
|    fresh agent per iteration, sees only prd.json + progress.txt + git    |
|    implements one story, sets passes:true, appends learnings             |
+--------------------------------------------------------------------------+
```

### 1.2 Bounded Contexts

| Context | Modules | Owns |
|---|---|---|
| **Bridge** | `core/bridge/http_server.py`, `core/bridge/http_client.py` | All MT5 communication. No business logic. |
| **Data** | `core/data/feed.py`, `core/data/history.py` | OHLCV/tick acquisition + parquet cache. |
| **Strategy** | `core/strategy/*` | Pure signal generation. No I/O, no order placement. |
| **Risk** | `core/risk/manager.py` | Sizing + circuit breakers. Reads config + account state. |
| **Execution** | `core/execution/order_manager.py`, `paper_broker.py`, `live_broker.py` | Order routing. Paper/live polymorphism. |
| **Performance** | `core/performance/tracker.py` | Sharpe/DD/win rate from closed trades. |
| **State** | `core/checkpoint/state.py`, `core/checkpoint/manager.py` | Bot snapshots, recovery. |
| **Backtest** | `backtest/engine.py` | Offline simulation, autoresearch contract. |
| **Autoresearch** | `autoresearch/loop.py`, `autoresearch/params.yaml` | Self-improvement loop. |
| **Knowledge** | `knowledge/base.py` | Markdown KB indexing. |
| **Orchestration** | `main.py` | CLI wiring + lifecycle. |

---

## 2. Module Interface Contracts

Contracts only — Agent 5 implements bodies. Type hints use Python 3.12 syntax.

### 2.1 `core/bridge/http_client.py`

```
class BridgeDisconnected(Exception): ...

class BridgeClient:
    def __init__(self, base_url: str = "http://192.168.64.1:8080",
                 heartbeat_timeout: int = 10,
                 request_timeout: float = 4.0) -> None
    def connect(self) -> bool                          # GET /ping; True if ea_connected
    def is_connected(self) -> bool                     # cached heartbeat age < timeout
    def get_tick(self, symbol: str) -> dict            # {symbol,bid,ask,spread,time}
    def get_account(self) -> dict                      # equity,balance,margin,free_margin,...
    def get_state(self) -> dict                        # full /state payload
    def get_history(self, symbol: str, timeframe: int,
                    count: int) -> list[dict]          # [{time,open,high,low,close,volume}]
    def send_order(self, symbol: str, side: str, volume: float,
                   sl: float | None = None, tp: float | None = None,
                   ticket: int | None = None) -> dict  # {ok, queued, ...} POST /order
    def get_results(self) -> list[dict]                # GET /results, drains server log
    def start_heartbeat_loop(self, interval: int = 5) -> None  # background thread
    def close(self) -> None
```

Notes: bridge response within 4s mandated by MT5 WebRequest 5s hardcoded timeout (progress.txt). All HTTP calls retry once on `ConnectionError` then raise `BridgeDisconnected`.

### 2.2 `core/data/feed.py`

```
class LiveDataFeed:
    def __init__(self, client: BridgeClient, symbols: list[str]) -> None
    async def stream(self) -> AsyncIterator[Tick]      # yields Tick on bridge updates
    def latest(self, symbol: str) -> Tick              # last cached tick
    def average_spread(self, symbol: str, n: int = 20) -> float

@dataclass
class Tick:
    symbol: str
    bid: float
    ask: float
    spread: float
    time_utc: datetime
    volume: int
```

Polls `/state` at 1Hz when no async push is available; uses asyncio for I/O concurrency (Section 10).

### 2.3 `core/data/history.py`

```
class HistoricalDataFetcher:
    CACHE_DIR = Path("bridge_data/history")
    CACHE_TTL_BY_TF = {"M1": 300, "M5": 600, "M15": 900, "H1": 3600,
                       "H4": 14400, "D1": 86400}

    def __init__(self, client: BridgeClient) -> None
    def fetch(self, symbol: str, timeframe: str,
              count: int = 5000) -> pd.DataFrame
    def fetch_range(self, symbol: str, timeframe: str,
                    start: datetime, end: datetime) -> pd.DataFrame
    def _cache_path(self, symbol: str, timeframe: str) -> Path
    def _is_stale(self, path: Path, timeframe: str) -> bool
    def _gap_check(self, df: pd.DataFrame, timeframe: str) -> list[tuple[datetime, datetime]]
```

Returns DataFrame with columns `[time, open, high, low, close, volume]`, `time` UTC tz-aware. Drops NaN rows (never forward-fill — progress.txt rule). Stores parquet via pyarrow.

### 2.4 `core/strategy/base.py`

```
@dataclass
class Signal:
    side: Literal["BUY", "SELL", None]
    entry_price: float
    sl_price: float
    tp_price: float
    confidence: float       # 0..1, used by meta-labeling later
    rationale: str          # human-readable, logged

class StrategyBase(ABC):
    name: str
    required_warmup_bars: int

    def __init__(self, params: dict) -> None
    @abstractmethod
    def generate_signal(self, df: pd.DataFrame) -> Signal
    def on_bar_close(self, df: pd.DataFrame) -> Signal     # hook
    def validate_params(self) -> None                       # raises on bad ranges
```

No I/O, no order placement. Pure function of (df, params). All indicator math in pandas/numpy — no TA-Lib.

### 2.5 `core/strategy/ema_crossover.py`

```
class EMACrossoverStrategy(StrategyBase):
    name = "ema_crossover"

    def __init__(self,
                 fast_period: int = 9,
                 slow_period: int = 21,
                 trend_period: int = 50,        # higher-TF trend filter
                 atr_period: int = 14,
                 atr_sl_multiplier: float = 1.5,
                 atr_tp_multiplier: float = 3.0,
                 spread_filter_mult: float = 2.5) -> None

    def generate_signal(self, df: pd.DataFrame) -> Signal
    # BUY  when fast EMA crosses above slow EMA on the *previous* completed bar
    # SELL when fast EMA crosses below slow EMA on the *previous* completed bar
    # SL = entry - 1.5*ATR(14) for BUY; +1.5*ATR for SELL
    # TP = entry + 3.0*ATR(14) for BUY; -3.0*ATR for SELL
    # Filters: spread > 2.5*avg_spread -> Signal(side=None, ..., rationale="spread_filter")
    # Filters: trend_period EMA must agree (close > trend EMA for BUY, < for SELL)
```

Knowledge base anchor: Strategy 1.6 Jim Brown trend stack (50/100/240) reduced to 9/21/50 for H1 FX.

### 2.6 `core/strategy/mean_reversion.py`

```
class BollingerBandMeanReversion(StrategyBase):
    name = "bb_mean_reversion"

    def __init__(self,
                 bb_period: int = 20,
                 bb_std_dev: float = 2.0,
                 bb_exit_std: float = 1.0,
                 atr_period: int = 14,
                 atr_sl_multiplier: float = 1.5,
                 rsi_filter: bool = True,
                 rsi_period: int = 14,
                 rsi_overbought: int = 70,
                 rsi_oversold: int = 30) -> None

    def generate_signal(self, df: pd.DataFrame) -> Signal
    # SELL when close >= upper band AND RSI > rsi_overbought
    # BUY  when close <= lower band AND RSI < rsi_oversold
    # Exit (next bar) when close re-enters within ±bb_exit_std of SMA(bb_period)
    # SL = entry ± atr_sl_multiplier * ATR(14)
```

Knowledge base anchor: Strategy 1.4 + Section 4.1 Bollinger formulas. RSI overlay is the mandatory filter (Section 1.4 warns about transaction cost reversal on raw BB).

### 2.7 `core/risk/manager.py`

```
class CircuitBreakerStatus(Enum):
    OK = "ok"
    WARN = "warn"            # 10% trailing DD
    REDUCE = "reduce"        # 15% trailing DD
    HALT = "halt"            # 20% trailing DD or daily loss breach

class RiskManager:
    def __init__(self, config: dict) -> None              # reads config.yaml.risk
    def calculate_size(self, symbol: str, entry: float, sl: float,
                       equity: float, win_rate: float | None = None,
                       avg_win: float | None = None,
                       avg_loss: float | None = None) -> float
    def kelly_fraction(self, win_rate: float, avg_win: float,
                       avg_loss: float) -> float            # quarter-Kelly capped
    def check_daily_loss(self, account: dict,
                         day_start_equity: float) -> bool
    def check_drawdown(self, equity: float,
                       peak_equity: float) -> CircuitBreakerStatus
    def get_position_adjustment(self, status: CircuitBreakerStatus) -> float
    # OK->1.0, WARN->0.7, REDUCE->0.5, HALT->0.0
    def should_reject_order(self, status: CircuitBreakerStatus,
                            account: dict,
                            day_start_equity: float) -> tuple[bool, str]
```

Position sizing (Section 6) and three-layer breakers (Section 6).

### 2.8 `core/execution/order_manager.py`

```
class OrderManager:
    def __init__(self, broker: BrokerProtocol,
                 risk: RiskManager,
                 tracker: PerformanceTracker) -> None
    def place(self, signal: Signal, account: dict, peak_equity: float,
              day_start_equity: float) -> dict | None
    # 1) breaker check  2) size calc  3) broker.place_order
    # 4) tracker.record_open  5) returns broker result or None on rejection
    def close(self, ticket: int) -> dict
    def close_all(self) -> list[dict]                   # used by kill_switch

class BrokerProtocol(Protocol):
    def place_order(self, symbol: str, side: str, volume: float,
                    sl: float | None, tp: float | None) -> dict: ...
    def close_position(self, ticket: int) -> dict: ...
    def get_positions(self) -> list[dict]: ...
    def get_account(self) -> dict: ...

# Concrete brokers (separate files, both implement BrokerProtocol):
#   core/execution/paper_broker.py  -> PaperBroker  (US-002)
#   core/execution/live_broker.py   -> LiveBroker   (US-010)
```

OrderManager is broker-agnostic. PaperBroker/LiveBroker swap is a single line in `main.py` based on `bot.mode`.

### 2.9 `core/performance/tracker.py`

```
@dataclass
class TradeRecord:
    ticket: int
    symbol: str
    side: str
    volume: float
    entry_price: float
    exit_price: float | None
    sl: float
    tp: float
    open_time: datetime
    close_time: datetime | None
    pnl: float
    is_closed: bool

class PerformanceTracker:
    def __init__(self, annualization: int = 252) -> None  # 252 trading days
    def record_open(self, trade: TradeRecord) -> None
    def record_close(self, ticket: int, exit_price: float,
                     close_time: datetime, pnl: float) -> None
    def sharpe_ratio(self) -> float | None                # None if < 30 closed trades
    def max_drawdown(self) -> float                       # 0..1
    def win_rate(self) -> float                            # 0..1
    def profit_factor(self) -> float                       # gross_profit/gross_loss
    def calmar_ratio(self) -> float | None
    def to_dict(self) -> dict
    def save(self, path: Path) -> None
    def load(self, path: Path) -> None                    # restores from JSON
```

Sharpe annualization for H1 = `sqrt(252*24)` (autoresearch-findings.md Section 3.1). Daily-frequency closed trades use `sqrt(252)`. `to_dict()` is JSON-serialisable.

### 2.10 `core/checkpoint/state.py` and `core/checkpoint/manager.py`

```
# state.py
@dataclass
class BotState:
    timestamp: datetime
    mode: Literal["paper", "live"]
    positions: list[dict]
    equity: float
    balance: float
    daily_pnl: float
    peak_equity: float
    day_start_equity: float
    best_params: dict
    research_iteration: int
    perf_summary: dict          # PerformanceTracker.to_dict()
    schema_version: int = 1

# manager.py
class CheckpointManager:
    CHECKPOINT_DIR = Path("/Users/ltmas/trading-bot-workspace/checkpoints")
    def __init__(self) -> None
    def save(self, state: BotState, label: str = "auto") -> Path
    # writes {timestamp}_{label}.pkl AND {timestamp}_{label}.json (summary, no secrets)
    def load_latest(self) -> BotState | None
    def load(self, path: Path) -> BotState
    def rotate(self, keep: int = 10) -> int               # returns number deleted
    def start_auto_checkpoint(self, get_state_fn,
                              interval_seconds: int = 300) -> None
    def stop_auto_checkpoint(self) -> None
```

### 2.11 `backtest/engine.py` — see Section 3 for full spec

### 2.12 `autoresearch/loop.py`

```
class AutoresearchLoop:
    def __init__(self, config: dict, kb: KnowledgeBase) -> None
    def run(self, iterations: int = 50,
            unlimited: bool = False, resume: bool = False) -> dict
    # phases (autoresearch-findings.md Section 1):
    def phase_review(self) -> dict                # read results.tsv tail, git log
    def phase_ideate(self, state: dict) -> ParamProposal   # coordinate descent
    def phase_modify(self, proposal: ParamProposal) -> Path # writes params.yaml
    def phase_commit(self, description: str) -> str         # returns commit sha
    def phase_verify(self) -> float                          # parses SHARPE <float>
    def phase_guard(self) -> tuple[bool, str]                # exit code 0/1
    def phase_decide(self, sharpe: float, guard_ok: bool) -> str  # keep|discard|crash
    def phase_log(self, iteration: int, sha: str, sharpe: float,
                  delta: float, guard: str, status: str,
                  param_changed: str, description: str) -> None

@dataclass
class ParamProposal:
    param_name: str
    old_value: float
    new_value: float
    direction: str            # "+" | "-"
    description: str
```

Files written:
- `bot/autoresearch/params.yaml` (overlay)
- `bot/autoresearch/results.tsv` (append-only TSV)
- `bot/autoresearch/best_params.yaml` (committed on improvement)
- `bot/autoresearch/search_state.json` (resumable)

Calls subprocess `python backtest/engine.py --metric sharpe` and `--guard` (autoresearch-findings.md Section 3.2). Timeout = 3× median backtest duration.

### 2.13 `knowledge/base.py`

```
class KnowledgeBase:
    SOURCE = Path("research/knowledge-base.md")
    def __init__(self, source: Path = SOURCE) -> None
    def load(self) -> None                          # parses markdown by heading
    def query(self, topic: str) -> str              # exact-match section text
    def search(self, keyword: str) -> list[tuple[str, str]]  # (heading, text)
    def get_strategy_params(self, strategy_name: str) -> dict
    # e.g. "ema_crossover" -> {"fast_period":9,"slow_period":21,...}
    def get_risk_rules(self) -> dict
    # parses Section 2 of KB into structured dict
    def get_indicator_formula(self, name: str) -> str
    # name in {"ema","sma","rsi","macd","atr","bollinger","stochastic"}
```

### 2.14 `main.py`

```
# CLI entry point
def main() -> int
# argparse: --mode paper|live --confirm-live --resume --iterations N --no-autoresearch

# Lifecycle:
# 1. Parse args + load config.yaml
# 2. If mode==live: require --confirm-live AND config.bot.mode=="live"; else raise
# 3. Build BridgeClient -> connect -> start heartbeat loop
# 4. Build HistoricalDataFetcher, RiskManager, PerformanceTracker
# 5. Build broker (PaperBroker or LiveBroker)
# 6. Build OrderManager, Strategy pipeline
# 7. CheckpointManager.load_latest() -> if exists, restore BotState
# 8. Start auto-checkpoint thread (300s)
# 9. Main loop: on bar close -> generate_signal -> place order -> track perf
# 10. On SIGINT/SIGTERM: graceful shutdown, final checkpoint, kill_switch if live
```

---

## 3. Backtest Engine Spec — `backtest/engine.py` (CRITICAL)

This file does not exist yet. Most critical spec in the plan. Agent 5 will be blocked on US-007 (autoresearch) until this is built.

### 3.1 CLI Contract

```bash
# Single-run modes
python backtest/engine.py --metric sharpe
python backtest/engine.py --guard
python backtest/engine.py --metric sharpe --guard          # dual mode (preferred)

# Parameter overlay
python backtest/engine.py --params autoresearch/params.yaml

# Optional knobs
python backtest/engine.py --symbol EURUSD --timeframe H1 --bars 8760
python backtest/engine.py --start 2023-01-01 --end 2025-12-31
python backtest/engine.py --strategy ema_crossover|bb_mean_reversion
python backtest/engine.py --transaction-cost-pips 2.0
python backtest/engine.py --output json|tsv|stdout
```

### 3.2 stdout Contract (machine-parseable)

```
SHARPE 1.2340
GUARD PASS drawdown=3.21% win_rate=51.4% bars=8760 trades=142
```

or:

```
SHARPE 0.8120
GUARD FAIL drawdown=6.43% exceeds 5.0% threshold
```

Exit code: `0` if guard passes (or if only `--metric` was requested and run succeeded), `1` if guard fails or run errored. Crash = stderr trace, exit code 2.

### 3.3 Guard Thresholds (from `config.yaml`)

| Metric | Threshold | Direction |
|---|---|---|
| Sharpe | > 1.5 | higher better |
| Max drawdown | < 5% | lower better |
| Win rate | > 45% | higher better |

All three must hold for `GUARD PASS`. Read from `autoresearch.target_sharpe`, `autoresearch.max_drawdown_guard`, `autoresearch.min_win_rate_guard`.

### 3.4 Data Source

- Primary: parquet files at `bridge_data/history/{symbol}_{timeframe}.parquet` (produced by `core/data/history.py`).
- Schema: `[time, open, high, low, close, volume]`, UTC tz-aware time.
- Symbols: EURUSD, GBPUSD (config.yaml). Timeframe: H1.
- Range: best-available 5 years of H1 data (≈30,000 bars/symbol). If unavailable, fall back to 1 year (≈6,000 bars) and emit a warning.

### 3.5 Minimum Bars for Statistical Validity

Per knowledge-base.md Section 4.3 (Chan):

| Confidence target | Backtest Sharpe ≥ | Min bars (daily equiv.) | H1 equivalent |
|---|---|---|---|
| True Sharpe > 0 | 1.0 | 681 | ≈ 16,344 |
| True Sharpe > 0 | 2.0 | 174 | ≈ 4,176 |
| True Sharpe > 1 (target) | 1.5 | 2,739 | ≈ 65,736 |

Engine prints a warning to stderr if `bars < 4,176`. Below `bars < 1,000` engine refuses to run and exits 2 (insufficient data).

### 3.6 Internal Architecture

```
backtest/engine.py (CLI)
   |
   v
backtest/runner.py            # event loop, vectorised where possible
   |--> loads config.yaml + optional params.yaml overlay
   |--> loads parquet via core.data.history
   |--> instantiates strategy from core.strategy.*
   |--> applies transaction costs (default 2 pips EURUSD, 3 pips GBPUSD)
   |--> simulates fills with PaperBroker semantics (immediate fill at ask/bid)
   |--> computes equity curve, peak, drawdown
   |--> uses PerformanceTracker for Sharpe/DD/win_rate
   v
backtest/report.py            # formatters: stdout, JSON, TSV
```

VectorBT used for the actual return-vector computation when available (ARM-native via Conda); otherwise pure pandas implementation. Numba JIT acceleration optional.

### 3.7 Determinism

- Fixed seed for any random draw (bootstrapping, slippage simulation).
- Idempotent: same params + same data → identical Sharpe to 4 decimal places.
- Run twice in CI; assert byte-identical TSV.

### 3.8 Performance Target

Per autoresearch-findings.md Section 6.2: must complete one symbol × 1 year H1 in **< 60 seconds** for the autoresearch loop to be practical (50 iterations ≤ 50 minutes wall time).

If pure pandas is too slow, drop to VectorBT (10–100× speedup). RaptorBT is optional second tier.

---

## 4. Strategy Implementation Priority

Order is fixed by knowledge-base evidence and dependency chain:

### 4.1 Phase A — EMA Crossover (US-004, first to implement)

**Why first:** Strategy 1.6 in KB (Jim Brown) is the simplest to verify and provides the cleanest baseline. Trend-following on H1 FX has the largest documented sample of profitable parameter sets. Knowledge-base Section 4.1 has explicit EMA formulas. Default params (9, 21, 50) widely documented.

**Verification target:** any positive Sharpe on EURUSD H1 5y. The autoresearch loop will optimise from there.

### 4.2 Phase B — Bollinger Band Mean-Reversion (added strategy, post US-007)

**Why second:** Strategy 1.4 in KB. Provides regime diversification — performs well when EMA crossover is in a chop. Critical caveat from KB Section 1.4: 5-min ES dies under transaction costs (Sharpe +3 → -3). H1 FX is the safer timeframe.

**Verification target:** positive Sharpe in low-ER regime (Efficiency Ratio < 0.3 per KB Section 6).

### 4.3 Phase C — RSI Filter Overlay (composite, post Phase B)

**Why third:** RSI lags price (Naked Forex warning, KB 1.4) so it cannot be a primary signal. Used as confirmation filter on top of either strategy:
- EMA + RSI: only take EMA BUY when RSI < 50 (entering oversold), only SELL when RSI > 50.
- BB + RSI: mandatory filter as specified in `mean_reversion.py`.

**Verification target:** RSI overlay must improve win-rate without halving trade count. If trades drop > 50%, RSI thresholds are too tight.

### 4.4 Phase D — Future / Out-of-Scope for Initial 10 Stories

- Triple-barrier ML labelling (KB 1.2)
- Meta-labeling (KB 1.3)
- Pairs trading / cointegration (KB 1.1)
- RL agent (Stable-Baselines3, in `requirements.txt` but not wired)

These are stretch goals once US-001..US-010 are green.

---

## 5. Data Flow JSON Schemas

All inter-module messages are JSON-serialisable dicts. Schemas pinned here are authoritative.

### 5.1 Tick Message (bridge → bot)

```json
{
  "symbol": "EURUSD",
  "bid": 1.08431,
  "ask": 1.08433,
  "spread": 2.0,
  "time": 1745571600,
  "volume": 0,
  "h1_open": 1.08410,
  "h1_high": 1.08450,
  "h1_low": 1.08395,
  "h1_close": 1.08431
}
```

`time` is unix epoch seconds. `spread` in pips (use 0.0001 pip size for 5-digit, 0.01 for JPY pairs). H1 OHLC fields are optional; only populated when EA pushes a completed bar.

### 5.2 Feature Vector (data → strategy)

```json
{
  "symbol": "EURUSD",
  "timeframe": "H1",
  "bar_time": "2026-04-25T13:00:00Z",
  "ohlcv": {
    "open": 1.08410, "high": 1.08465, "low": 1.08395,
    "close": 1.08431, "volume": 1234
  },
  "indicators": {
    "ema_fast": 1.08420,
    "ema_slow": 1.08395,
    "ema_trend": 1.08350,
    "atr_14": 0.00085,
    "rsi_14": 58.3,
    "bb_upper": 1.08510, "bb_mid": 1.08400, "bb_lower": 1.08290,
    "spread_avg_20": 1.8
  },
  "regime": {
    "efficiency_ratio": 0.42,
    "session": "european"
  }
}
```

### 5.3 Trade Signal (strategy → order manager)

```json
{
  "strategy": "ema_crossover",
  "symbol": "EURUSD",
  "side": "BUY",
  "entry_price": 1.08433,
  "sl_price": 1.08305,
  "tp_price": 1.08689,
  "confidence": 0.62,
  "rationale": "fast EMA crossed above slow EMA on prior bar; trend EMA agrees",
  "timestamp": "2026-04-25T13:00:01Z"
}
```

### 5.4 Order Command (bot → bridge POST `/order`)

```json
{
  "action": "OPEN",
  "symbol": "EURUSD",
  "side": "BUY",
  "volume": 0.10,
  "sl": 1.08305,
  "tp": 1.08689,
  "magic": 70425001,
  "comment": "ema_crossover|conf=0.62"
}
```

`action ∈ {"OPEN","CLOSE","MODIFY","NONE"}`. `magic` is bot identifier. For close: `{"action":"CLOSE","ticket":12345678}`.

### 5.5 Trade Result (bridge → bot via `/results`)

```json
{
  "action": "OPEN",
  "success": true,
  "ticket": 12345678,
  "retcode": 10009,
  "comment": "Request completed",
  "error": null,
  "fill_price": 1.08434,
  "fill_time": 1745571602
}
```

Common `retcode`s (progress.txt + ralph error matrix): `10009` ok, `10006` rejected, `10014` invalid volume, `10019` insufficient margin.

---

## 6. Risk Manager Design

### 6.1 ATR Position Sizing (primary formula)

```
risk_dollar = equity * max_risk_per_trade        # default 1% of equity
sl_distance_pips = abs(entry - sl) / pip_size    # pip_size 0.0001 for non-JPY
units = risk_dollar / (sl_distance_pips * dollar_per_pip)
lots  = units / contract_size                     # 100,000 standard lot
lots  = round_to(lots, step=0.01)                 # MT5 lot step
lots  = min(lots, max_lots_per_symbol)            # safety clamp
```

`dollar_per_pip` for EURUSD standard lot = USD 10. Source: knowledge-base.md Section 2.1, prd.json US-005 notes.

### 6.2 25% Fractional Kelly Multiplier

```
kelly_pct = (win_rate * avg_win - (1-win_rate) * avg_loss) / avg_win
# Clamp to [0, 1] before scaling
kelly_pct = max(0.0, min(1.0, kelly_pct))
final_lots = atr_lots * config.risk.kelly_fraction * kelly_pct
# kelly_fraction = 0.25 in config.yaml -> quarter-Kelly
```

Re-estimate `win_rate`, `avg_win`, `avg_loss` from `PerformanceTracker` daily for active trading. If `< 30 closed trades`: fall back to `kelly_pct = 0.5` placeholder (do not multiply by zero).

### 6.3 Three-Layer Circuit Breakers

Sourced from urp-findings.md Section "KRQ4" + autoresearch-findings.md guard thresholds + config.yaml.

| Layer | Trigger | Threshold (config key) | Action |
|---|---|---|---|
| **Trade** | Single-trade risk | `max_risk_per_trade` = 0.01 | Reject if order risks > 1% of equity |
| **Strategy (warn)** | Trailing DD | `trailing_dd_warn` = 0.10 | Position multiplier 0.7 |
| **Strategy (reduce)** | Trailing DD | `trailing_dd_reduce` = 0.15 | Position multiplier 0.5 |
| **Portfolio (halt)** | Trailing DD | `trailing_dd_halt` = 0.20 | Multiplier 0.0; halt all opens |
| **Daily loss** | Realised + unrealised today | `daily_loss_limit` = 0.02 | Halt opens until next session |

Multiplier composition: `final_lots = base_lots * kelly_factor * dd_multiplier`.

### 6.4 Daily Loss Limit Enforcement

- Day starts at 17:00 New York time (FX market close, KB Section 3.1).
- `day_start_equity` snapshot is captured at session reset.
- `daily_pnl = current_equity - day_start_equity` (includes unrealised).
- If `daily_pnl <= -daily_loss_limit * day_start_equity` → reject all new opens until next day. Existing positions continue per their own SL/TP (do not auto-close).
- Persist `day_start_equity` in BotState to survive restarts.

### 6.5 Required Risk Manager Tests

- 1% per-trade rule with various SL distances → assert lots ≤ ceiling.
- Kelly negative win-rate scenario → assert kelly_pct clamped to 0.
- Each DD threshold → assert correct multiplier returned.
- Daily loss limit at 1.99% → allow; at 2.01% → halt.
- Lot step rounding (0.012 → 0.01; 0.018 → 0.02).

---

## 7. Requirements Update

Current `requirements.txt` is a baseline. Additions needed for the full plan:

### 7.1 Already Present (verify versions)

| Package | Status |
|---|---|
| pyyaml >= 6.0 | OK |
| pandas >= 2.0 | OK (ARM wheel) |
| numpy >= 1.26 | OK (ARM wheel) |
| vectorbt >= 0.27.2 | **ARM/Conda-sensitive** — requires Miniforge for Numba |
| scikit-learn >= 1.4 | OK |
| optuna >= 3.6 | OK |
| stable-baselines3 >= 2.3 | optional, defer until Phase D |
| ta-lib-easy | drop — KB requires pandas/numpy only (US-004 acceptance) |
| python-dotenv >= 1.0 | OK |
| loguru >= 0.7 | OK |

### 7.2 Add for Plan Completion

| Package | Purpose | ARM/Conda-sensitive? |
|---|---|---|
| `fastapi >= 0.110` | bridge server (already used; pin) | No |
| `uvicorn[standard] >= 0.29` | bridge server (already used; pin) | No |
| `requests >= 2.31` | BridgeClient HTTP | No |
| `pydantic >= 2.6` | bridge schemas (in use) | No |
| `pyarrow >= 15.0` | parquet cache (US-003) | **Yes** — install ARM wheel |
| `pytest >= 8.0` | test runner | No |
| `pytest-asyncio >= 0.23` | async tests for feed.py | No |
| `responses >= 0.25` | HTTP mocking (US-001 tests) | No |
| `freezegun >= 1.4` | deterministic time in tests | No |
| `tenacity >= 8.2` | retry policy on bridge calls | No |
| `numba >= 0.59` | required transitively by vectorbt | **Yes — Conda only** |
| `tabulate >= 0.9` | TSV/markdown reports | No |
| `rich >= 13.0` | optional CLI progress | No |

### 7.3 Conda Setup Note

Per urp-findings.md KRQ2 + KRQ5: VectorBT + Numba on Apple Silicon mandate Miniforge. Document the install path explicitly in `README.md`:

```bash
brew install miniforge
conda create -n trading python=3.12
conda activate trading
conda install -c conda-forge numba pyarrow
pip install -r requirements.txt
```

The existing `.venv` works for everything except VectorBT/Numba/PyArrow. Two options:

1. **Hybrid:** keep `.venv` for runtime + bridge; use a separate `conda` env only for backtest engine. `backtest/engine.py` is a CLI invoked by subprocess so the env switch is transparent.
2. **Single Conda env:** migrate everything to Miniforge env. Cleaner but requires re-pinning all dev tooling.

Recommendation: **Option 1** for the first 10 stories. Switch to Option 2 only if subprocess overhead degrades autoresearch wall time.

---

## 8. Paper → Live Promotion Checklist

Live trading is gated by a dual interlock (config flag AND CLI flag) per US-010 and the global hard rule. Promotion is only allowed after **all** of the following hold:

### 8.1 Code & Test Gates

- [ ] All 10 Ralph stories US-001..US-010 are `passes: true` in `prd.json`.
- [ ] Full pytest suite green (`python -m pytest` from `bot/` root, ≥ 90% coverage on `core/risk/` and `core/execution/`).
- [ ] `python backtest/engine.py --guard` exits 0 on EURUSD H1 5y data.
- [ ] `python backtest/engine.py --guard` exits 0 on GBPUSD H1 5y data.
- [ ] Determinism check: backtest run twice produces byte-identical metric output.

### 8.2 Statistical Gates

- [ ] Backtest sample size ≥ 4,176 H1 bars (≈ 6 months) — knowledge-base.md Section 4.3.
- [ ] Sharpe ≥ 1.5 on out-of-sample window (last 25% of available data).
- [ ] Max drawdown < 5% on full history.
- [ ] Win rate > 45% on ≥ 30 closed trades.
- [ ] Walk-forward validation: rolling 6-month windows all show Sharpe > 1.0.
- [ ] Transaction costs included: 2 pip spread on EURUSD, 3 pip on GBPUSD (autoresearch-findings.md Section 6.3).

### 8.3 Operational Gates

- [ ] Bridge `/ping` returns `ea_connected: true` for ≥ 60 minutes continuously.
- [ ] Heartbeat age < 10 s consistently in monitoring window.
- [ ] PaperBroker has run for ≥ 5 trading days with positive P&L.
- [ ] CheckpointManager has produced ≥ 1 successful auto-restore (kill bot, restart, verify state).
- [ ] `kill_switch()` tested in paper mode end-to-end.

### 8.4 Configuration Gates

- [ ] `config.yaml: bot.mode = "live"` (manual edit, version-controlled).
- [ ] `--confirm-live` CLI flag present at every invocation.
- [ ] Account equity ≥ 1,000 USD on live broker (US-010 pre-flight).
- [ ] Daily loss limit set to a reviewed value (`daily_loss_limit` confirmed in config).
- [ ] No open positions inherited from a prior crash (US-010 pre-flight).

### 8.5 Human Gates

- [ ] Plan reviewed and signed off by operator (Thulani).
- [ ] One-line problem statement in `bot/README.md` (per global CLAUDE.md project goals).
- [ ] ADR `bot/docs/decisions/001-live-mode-go-live.md` written and committed.

If any single item fails, `LiveBroker` constructor must raise `LiveModeNotEnabled` with the failing item enumerated.

---

## 9. Implementation Sequence (Story-by-Story)

Each row maps a Ralph story to concrete files, the verification command, and a complexity estimate (S = ≤ 1 hr, M = 1–4 hr, L = ≥ 4 hr).

| Story | Title | Files to create | Files to modify | Verification | Complexity |
|---|---|---|---|---|---|
| **US-001** | MT5 HTTP Bridge Client | `core/bridge/http_client.py`, `core/bridge/test_client.py`, `core/bridge/__init__.py` | none | `python -m pytest core/bridge/test_client.py` | M |
| **US-002** | Paper Trading Mode | `core/execution/paper_broker.py`, `core/execution/test_paper_broker.py`, `core/execution/__init__.py` | none | `python -m pytest core/execution/test_paper_broker.py` | M |
| **US-003** | Historical Data Fetcher | `core/data/history.py` (named `fetcher.py` per prd), `core/data/test_fetcher.py`, `core/data/__init__.py`, `core/data/feed.py` (stub) | `bot/core/bridge/http_server.py` (add `/history` endpoint if missing) | `python -m pytest core/data/test_fetcher.py` | M |
| **US-004** | EMA Crossover Strategy | `core/strategy/base.py`, `core/strategy/ema_crossover.py`, `core/strategy/test_ema_crossover.py`, `core/strategy/__init__.py` | none | `python -m pytest core/strategy/test_ema_crossover.py` | M |
| **US-005** | Risk Manager | `core/risk/manager.py`, `core/risk/test_manager.py`, `core/risk/__init__.py` | `config.yaml` (add daily_loss_limit window if missing) | `python -m pytest core/risk/test_manager.py` | M |
| **US-006** | Performance Tracker | `core/performance/tracker.py`, `core/performance/test_tracker.py`, `core/performance/__init__.py` | none | `python -m pytest core/performance/test_tracker.py` | S |
| **US-007** | Autoresearch Loop (8-phase) | `autoresearch/engine.py`, `autoresearch/loop.py` (CLI), `autoresearch/test_engine.py`, `autoresearch/__init__.py`, **`backtest/engine.py`**, **`backtest/runner.py`**, **`backtest/report.py`**, `backtest/test_engine.py` | `requirements.txt` (pin pyarrow, numba note), `.gitignore` (add `autoresearch/results.tsv`, `autoresearch/search_state.json`, `bridge_data/history/`) | `python -m pytest autoresearch/test_engine.py` AND `python backtest/engine.py --metric sharpe --guard` | **L** |
| **US-008** | Knowledge Base Integration | `knowledge/base.py` (named `kb.py` per prd), `knowledge/test_kb.py`, `knowledge/__init__.py` | `autoresearch/engine.py` (wire KB for parameter bounds) | `python -m pytest knowledge/test_kb.py` | S |
| **US-009** | Checkpoint and Recovery | `core/checkpoint/state.py`, `core/checkpoint/manager.py`, `core/checkpoint/test_manager.py`, `core/checkpoint/__init__.py` | none | `python -m pytest core/checkpoint/test_manager.py` | M |
| **US-010** | Live Trading Mode | `core/execution/live_broker.py`, `core/execution/test_live_broker.py`, `main.py` (full wiring) | `config.yaml` (no value change; verify `bot.mode` toggle works), `bot/README.md` (live-mode warnings + ADR link) | `python -m pytest core/execution/test_live_broker.py` AND **dual-interlock dry-run** | L |

### 9.1 Cross-Cutting Tasks (do as part of US-007 or earlier)

- Create `bot/conftest.py` with shared pytest fixtures (mock bridge, sample OHLCV DataFrames, frozen clock).
- Create `bot/.gitignore` if absent: `bridge_data/history/`, `autoresearch/results.tsv`, `autoresearch/search_state.json`, `checkpoints/`, `__pycache__/`, `.pytest_cache/`, `*.pyc`, `.coverage`.
- Add `bot/pyproject.toml` (or `setup.cfg`) so `python -m pytest` from `bot/` resolves imports as `core.bridge.client`, not `bridge.client` (progress.txt rule).

### 9.2 Backtest Engine is the Critical Path

US-007 is L because it bundles three deliverables: `backtest/engine.py` (Section 3), `backtest/runner.py`, and the autoresearch engine itself. Agent 5 should split US-007 into two commits — first the backtest CLI alone (verifiable independently), then the autoresearch engine that calls it.

---

## 10. M5 Pro Resource Budget

Hardware: M5 Pro, 12 P-cores + 4 E-cores (assumed similar to M4 Pro), 24 GB unified memory.

### 10.1 Memory Allocation

| Process | Allocation | Notes |
|---|---|---|
| macOS + headroom | 2.0 GB | system services |
| UTM Windows VM (MT5 + EA) | 6.0 GB | MT5 ≈ 1 GB; VM overhead ≈ 5 GB |
| Python bot main process (`main.py`) | 2.0 GB | bridge client, strategy, risk, exec |
| Performance tracker + checkpoints | 0.3 GB | in-memory trade list + pickling |
| Historical data cache (RAM) | 1.5 GB | EURUSD + GBPUSD, multiple TFs, parquet on disk + DataFrame copies |
| Backtest engine subprocess (one run) | 3.0 GB | VectorBT working set on 5y H1 |
| Autoresearch loop controller | 0.5 GB | metadata, state JSON, subprocess lifecycle |
| FastAPI bridge server | 0.3 GB | uvicorn + pydantic models |
| Reserved buffer (OOM headroom) | 8.4 GB | 35% headroom — protects KV cache pattern from urp-findings KRQ5 |
| **Total committed** | **15.6 GB** | — |
| **Total available** | **24.0 GB** | — |

Under autoresearch loops the backtest engine will spike past 3 GB on combined symbol runs. The 8.4 GB buffer absorbs that.

### 10.2 CPU Allocation

| Workload | Cores | Pool type | Notes |
|---|---|---|---|
| macOS + UI | 1 P-core (shared) | OS scheduler | — |
| UTM Windows VM | 2 P-cores | UTM config | enough for MT5 + EA |
| Bridge FastAPI server | 1 P-core (event loop) | uvicorn worker | I/O-bound, asyncio |
| Bot main loop | 1 P-core | asyncio | data feed + strategy on bar close |
| Heartbeat thread | 0.1 P-core | thread | 5 s polling |
| Auto-checkpoint thread | 0.1 P-core | thread | 5-min cadence |
| Backtest engine | 4 P-cores | `ProcessPoolExecutor` | VectorBT/Numba parallelism |
| Autoresearch coordinator | 1 P-core | subprocess driver | spawns one backtest at a time |
| E-cores | 4 E-cores | OS background | log writes, parquet flushes |

Pattern matrix (urp-findings.md KRQ5):
- Bridge + feed = `asyncio` (I/O-bound).
- Backtest engine internal = `ProcessPoolExecutor(max_workers=4)` for parameter sweeps.
- Strategy indicator math = single-process pandas/numpy with Numba JIT where hot.

### 10.3 Disk

| Path | Size budget | Cadence |
|---|---|---|
| `bridge_data/history/*.parquet` | 200 MB | refreshed per TTL |
| `bridge_data/price.json`, `account.json` | 10 KB | every tick |
| `checkpoints/*.pkl` | 50 MB total (10 × 5 MB) | 5 min auto + on shutdown |
| `autoresearch/results.tsv` | 5 MB | append per iteration |
| `logs/live_orders.jsonl` | 100 MB rotating | per order |
| `logs/bot.log` (loguru) | 200 MB rotating | continuous |

### 10.4 Network

- Bridge HTTP: ≈ 1 KB/tick × 1 tick/sec/symbol × 2 symbols = 2 KB/s sustained.
- `/command` polling from EA: 100 ms cadence, < 100 B/req → < 1 KB/s.
- No external network calls during live trading (bridge is loopback-VM).

---

## Appendix A — File Tree After Plan Execution

```
bot/
  config.yaml
  requirements.txt
  pyproject.toml                   (NEW — package metadata + pytest config)
  conftest.py                      (NEW — shared fixtures)
  main.py                          (NEW — US-010)
  README.md                        (UPDATED — problem statement + live mode warnings)
  .gitignore                       (NEW or updated)

  core/
    __init__.py
    bridge/
      http_server.py               (existing)
      http_client.py               (NEW — US-001)
      mt5_client.py                (legacy — do not extend)
      test_client.py               (NEW — US-001)
      CLAUDE.md                    (UPDATED with bridge quirks)
    data/
      __init__.py                  (NEW)
      feed.py                      (NEW — US-003 stub, full impl in main loop)
      history.py / fetcher.py      (NEW — US-003)
      test_fetcher.py              (NEW — US-003)
    strategy/
      __init__.py                  (NEW)
      base.py                      (NEW — US-004)
      ema_crossover.py             (NEW — US-004)
      mean_reversion.py            (NEW — Phase B, post US-007)
      test_ema_crossover.py        (NEW — US-004)
    risk/
      __init__.py                  (NEW)
      manager.py                   (NEW — US-005)
      test_manager.py              (NEW — US-005)
      CLAUDE.md                    (NEW — risk rules)
    execution/
      __init__.py                  (NEW)
      order_manager.py             (NEW — US-002+)
      paper_broker.py              (NEW — US-002)
      live_broker.py               (NEW — US-010)
      test_paper_broker.py         (NEW — US-002)
      test_live_broker.py          (NEW — US-010)
    performance/
      __init__.py                  (NEW)
      tracker.py                   (NEW — US-006)
      test_tracker.py              (NEW — US-006)
    checkpoint/
      __init__.py                  (NEW)
      state.py                     (NEW — US-009)
      manager.py                   (NEW — US-009)
      test_manager.py              (NEW — US-009)

  backtest/
    __init__.py                    (NEW)
    engine.py                      (NEW — US-007 critical, see Section 3)
    runner.py                      (NEW — US-007)
    report.py                      (NEW — US-007)
    test_engine.py                 (NEW — US-007)

  autoresearch/
    __init__.py                    (NEW)
    engine.py                      (NEW — US-007)
    loop.py                        (NEW — US-007 CLI entry)
    params.yaml                    (NEW — overlay, written per iteration)
    best_params.yaml               (NEW — committed on improvement)
    results.tsv                    (NEW — git-ignored, append-only)
    search_state.json              (NEW — git-ignored, resumable state)
    test_engine.py                 (NEW — US-007)

  knowledge/
    __init__.py                    (NEW)
    base.py / kb.py                (NEW — US-008)
    test_kb.py                     (NEW — US-008)

  ralph/
    prd.json                       (existing)
    progress.txt                   (existing — appended each iteration)
    CLAUDE.md                      (existing — Ralph driver)

  bridge_data/
    history/                       (parquet cache, git-ignored)
    price.json, account.json       (existing)

checkpoints/                       (sibling to bot/, git-ignored)
plan/
  implementation-plan.md           (this file)
  status-agent4.json
research/
  urp-findings.md, knowledge-base.md, autoresearch-findings.md, ralph-findings.md
```

---

## Appendix B — Open Questions for the Operator

1. **Conda vs hybrid env:** confirm Option 1 (hybrid `.venv` + Conda for backtest) before US-007 starts; otherwise prepare for env migration.
2. **Symbol list for live mode:** EURUSD + GBPUSD only, or extend to USDJPY before US-010? (Affects pip-size handling in RiskManager.)
3. **Daily loss limit window:** uses NY 17:00 close per KB. Confirm — operator may prefer UTC-aligned days for paper testing.
4. **Live broker hardware:** UTM VM uptime SLA? If VM is shut down nightly the bot must detect and pause cleanly (covered by heartbeat, but human policy needed).

---

*End of implementation-plan.md*
