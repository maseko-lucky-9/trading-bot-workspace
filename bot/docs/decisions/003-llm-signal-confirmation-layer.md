# ADR 003 — LLM Signal Confirmation Layer (Option A)

**Date:** 2026-05-02
**Status:** Proposed

## Context

The trading-bot-workspace is a rule-based MT5 forex bot. Strategies (EMA
crossover, mean reversion, trend following, pairs trading) generate `Signal`
objects from OHLCV data. The pipeline is:

```
Strategy.generate_signal()
        ↓
[session / news / regime filters]
        ↓
RiskManager.size_position()
        ↓
Broker (paper | live)
```

Signals are correct on structure but blind to: (a) macro/news context that
technicals cannot encode (FOMC decisions, NFP releases, central bank tone
shifts), (b) cross-timeframe narrative disagreement that a rule cannot
express (e.g. H4 trend is bullish but the H1 candle sequence shows
distribution), and (c) sentiment divergence visible in news headlines but not
in price yet.

Two upstream repositories contain LLM agent frameworks relevant to these
gaps:

**TradingAgents** (`experiments/demo/TradingAgents`)  
Multi-agent LangGraph framework with four analyst roles (fundamentals,
sentiment, news, technical), a bull/bear researcher debate loop, a risk
committee, and a Portfolio Manager. Also provides `TradingMemoryLog` — a
persistent log that stores each trade decision and defers LLM reflection until
actual returns are known.

**AI-hedge-fund** (`apps/ai-hedge-fund`)  
LangGraph multi-agent system with 15 named investor personas (Buffett, Munger,
Damodaran, etc.), a FastAPI web service, and a React/Vite web UI with
Analyze/Backtest/History screens.

**The core impedance mismatch:** both frameworks are built for equity markets.
The bot trades forex (EURUSD, GBPUSD, USDJPY). Fundamentals in equities
(earnings, P/E, DCF) do not map to forex; macro fundamentals (central bank
rate expectations, economic calendar events, inter-market correlations) do.
The agent prompts and data tool wiring must be rewritten for forex before
either framework can contribute value.

Three integration options were evaluated:

| Option | Description | Effort | Risk |
|---|---|---|---|
| **A. LLM as signal filter** | Insert a `LLMConfirmationFilter` between `Strategy` and `RiskManager`. Rule-based signal is confirmed or vetoed by LLM agents before sizing. Existing pipeline untouched. | ~3 days | Low |
| B. LLM as parallel regime signal | TradingAgents' debate loop runs once per H1 candle and emits a `regime_bias` fed into `config.yaml`'s `strategy_regime_map`. LLM output gates which regimes strategies trade in. | ~5-7 days | Medium |
| C. Full LLM signal generation | Rule-based strategies replaced by LLM-generated `Signal` objects. All analyst roles rewritten for forex. | ~10-14 days | High |

## Decision

Implement **Option A**: add `core/filters/llm_confirmation.py` as a new filter
in the existing filter chain. The LLM layer sits between signal generation and
risk sizing. It does not modify the `Signal` object — it returns a boolean
`confirmed` flag. A vetoed signal is logged and discarded; a confirmed signal
passes to `RiskManager` unchanged.

### What is taken from TradingAgents

| Component | Source file(s) | Role in this integration | Modification required |
|---|---|---|---|
| **Technical Analyst** | `tradingagents/agents/analysts/market_analyst.py` | Re-analyzes the candle structure on the current timeframe using the MT5 bridge OHLCV | Replace yfinance tool calls with `MT5DataTool` wrapping `bridge_data/history/*.parquet`; rewrite system prompt for forex (no equity bias) |
| **News / Sentiment Analyst** | `tradingagents/agents/analysts/news_analyst.py`, `social_media_analyst.py` | Evaluates macro news context (economic calendar, central bank tone) at signal time | Replace financial news tools with `core/filters/news.py` calendar feed + an optional macro news API; strip stock-specific sentiment keywords |
| **Bull/bear debate loop** | `tradingagents/agents/researchers/bull_researcher.py`, `bear_researcher.py`, `tradingagents/graph/trading_graph.py` | Two LLM researchers argue for and against acting on the rule-based signal; a judge resolves | Scope the debate to the specific signal event (not a full ticker analysis run); cap `max_debate_rounds` at 1 to bound latency |
| **TradingMemoryLog** | `tradingagents/agents/utils/memory.py` | Stores each LLM confirmation decision and the trade outcome; feeds past context into future confirmations for the same pair | Point `memory_log_path` at `bot/logs/llm_memory.md`; reflection trigger wired to closed-trade events from `core/performance/tracker.py` |
| **LLM client factory** | `tradingagents/llm_clients/factory.py` + provider modules | Multi-provider support (OpenAI, Anthropic, Google, Azure, DeepSeek, xAI) | Import directly; no changes needed |

### What is taken from AI-hedge-fund

| Component | Source file(s) | Role in this integration | Modification required |
|---|---|---|---|
| **Technicals agent** | `src/agents/technicals.py` | Secondary technical signal — cross-checks RSI, MACD, Bollinger structures against the rule-based strategy's reasoning | Replace `FinancialDatasets` API calls with MT5 bridge data; keep indicator computation logic |
| **Sentiment agent** | `src/agents/sentiment.py` | Provides a DXY / risk-on-risk-off context signal (USD strength index, VIX proxy) | Swap equity sentiment data source for forex-relevant macro sentiment feed; retain LangGraph node structure |
| **FastAPI + React web UI** | `server/`, `web/` | Deferred to a follow-up ADR. The existing `dashboard/` (ADR 0020) covers immediate monitoring needs. The AI-hedge-fund web UI would add LLM run history and streaming confirmation logs. | Not included in this implementation. Track as `ADR-004` candidate. |

### What is explicitly stripped

| Component | Reason |
|---|---|
| 15 named investor personas (Buffett, Munger, Damodaran, etc.) | Equity-specific; their investment frameworks (margin of safety, DCF, scuttlebutt) have no meaningful analogue in forex speculation |
| Fundamentals analyst (both repos) | No earnings, balance sheets, or P/E ratios in forex; macro fundamentals require entirely new tooling and are out of scope for Option A |
| Valuation agent (`src/agents/valuation.py`) | Equity-only |
| `FinancialDatasets` API integration | Not available for forex instruments |
| `backtrader` integration (TradingAgents) | The bot has its own backtester with k-fold CV and DSR; a second backtest engine would cause confusion |
| LangGraph `==0.2.56` pin (AI-hedge-fund) | TradingAgents requires `>=0.4.8`; the bot has no existing LangGraph dependency so the higher version is adopted |

### Integration point

The confirmation filter is inserted into `main.py`'s filter chain after the
existing session/news/regime filters and before `RiskManager.size_position()`:

```
Strategy.generate_signal()
        ↓
SessionFilter
        ↓
NewsBlackoutFilter
        ↓
RegimeFilter
        ↓
LLMConfirmationFilter   ← NEW (this ADR)
        │
        ├── confirmed → RiskManager → Broker
        └── vetoed    → log + discard
```

The filter is **opt-in via config**:

```yaml
filters:
  llm_confirmation:
    enabled: false          # enable after paper-trade baseline established
    provider: "openai"
    deep_model: "gpt-5.4"
    quick_model: "gpt-5.4-mini"
    max_debate_rounds: 1
    timeout_seconds: 30     # hard timeout; veto on breach
    memory_log_path: "logs/llm_memory.md"
```

Defaulting to `enabled: false` ensures the existing paper-trading baseline
(Sharpe, DSR, win rate) is not contaminated before LLM quality is validated.

### Latency contract

M15 candle interval = 900 seconds. LLM confirmation budget = 30 seconds
(two analyst calls + one debate round). This leaves 870 seconds of headroom.
The filter runs synchronously in the bot's main loop; `asyncio.to_thread` is
used for LLM calls to avoid blocking the bridge heartbeat check. If the
timeout fires, the signal is **vetoed by default** (conservative: do nothing
if the decision support system is unavailable).

### Data tool

A new `MT5DataTool` wraps the existing `core/data/historical_client.py` as a
LangChain `@tool`. It exposes:
- `get_ohlcv(symbol, timeframe, bars)` — reads from `bridge_data/history/*.parquet`
- `get_current_price(symbol)` — reads from `bridge_data/price.json`

This is the single data-layer substitution required. No yfinance, no
FinancialDatasets calls.

## Consequences

### Positive

- **Zero disruption to the rule-based pipeline.** `Strategy`, `RiskManager`,
  and both brokers are untouched. The LLM layer is purely additive and
  removable by setting `enabled: false`.
- **Addresses genuine blind spots.** News blackout filter (`enabled: false`
  in current config) is a known gap; the LLM news analyst provides a soft
  equivalent before the calendar feed is populated.
- **TradingMemoryLog bridges the autoresearch gap.** The Optuna autoresearch
  loop is disabled pending 200+ closed paper trades. LLM reflection via
  `TradingMemoryLog` provides qualitative signal quality feedback in the
  interim.
- **Multi-provider LLM support from day one.** TradingAgents' `llm_clients/`
  factory supports OpenAI, Anthropic, Google, Azure, DeepSeek, xAI — operator
  can switch providers without touching the filter logic.
- **Measurable outcome.** Enable/disable via config flag; A/B comparison
  of Sharpe / win rate with and without confirmation is straightforward.

### Negative

- **New dependency: LangGraph `>=0.4.8` + LLM provider SDK.** Adds ~50 MB
  to the venv. The bot has no prior LangGraph usage; first-time wiring will
  surface version-specific quirks in the `ToolNode` / `AgentState` API.
- **LLM call cost.** Two analyst calls + one debate round per confirmed
  signal. At M15 frequency with reasonable signal rate, cost is O($0.01–0.05)
  per confirmed signal depending on provider/model. Track in `logs/llm_costs.jsonl`.
- **Prompt quality is load-bearing.** If the forex-adapted prompts are
  under-specified, the confirmation layer adds noise rather than signal.
  Treat the prompts as first-class artefacts: version-control them, log
  every LLM rationale, and review weekly alongside the trade journal.
- **Veto default on timeout is conservative.** A flaky network or slow LLM
  provider will miss valid trade setups. Monitor veto rate in the dashboard.

### Deferred

- **AI-hedge-fund FastAPI + React web UI** (ADR-004 candidate): replace the
  current `dashboard/` polling UI with SSE streaming and LLM run history.
  Requires AI-hedge-fund's `server/` layer adapted to forward `LLMConfirmationFilter`
  results via the existing SSE schema.
- **Option B (LLM as parallel regime signal)**: graduate to this only if the
  Option A A/B comparison shows a statistically significant Sharpe improvement
  after ≥100 confirmed-vs-vetoed pairs in the paper trade log.
- **Fundamentals / macro analyst**: out of scope for Option A. If an economic
  calendar feed is added to `data/news_calendar.csv` (enabling the existing
  `NewsBlackoutFilter`), a macro analyst agent becomes a natural extension.

## Files to create / modify

| File | Action |
|---|---|
| `core/filters/llm_confirmation.py` | New — `LLMConfirmationFilter` class |
| `core/data/mt5_data_tool.py` | New — `MT5DataTool` LangChain tool |
| `core/agents/technical_analyst.py` | New — forex-adapted from TradingAgents `market_analyst.py` |
| `core/agents/news_sentiment_analyst.py` | New — merged forex-adapted from TradingAgents `news_analyst.py` + `social_media_analyst.py` |
| `core/agents/debate.py` | New — bull/bear debate node adapted from TradingAgents `trading_graph.py` |
| `core/agents/llm_memory.py` | New — thin wrapper around TradingAgents `TradingMemoryLog` |
| `config.yaml` | Edit — append `filters.llm_confirmation` block (disabled by default) |
| `main.py` | Edit — insert `LLMConfirmationFilter` into filter chain after `RegimeFilter` |
| `requirements.txt` | Edit — add `langgraph>=0.4.8`, `langchain-core>=0.3.81`, and chosen provider SDK |
| `tests/filters/test_llm_confirmation.py` | New — unit tests with mocked LLM calls |
| `tests/agents/test_technical_analyst.py` | New — prompt fixture + mock tool tests |

## Related

- ADR 001 — HTTP Bridge (the `MT5DataTool` reads from bridge artefacts, not the live HTTP endpoint)
- ADR 0020 — Bot Dashboard (veto rate and LLM cost metrics to be added as a new tile)
- `research/knowledge-base.md` — existing distilled strategy knowledge; feed as system-prompt context to analyst agents
- `autoresearch/loop.py` — Optuna loop remains independent; `TradingMemoryLog` does not interact with Optuna study state
