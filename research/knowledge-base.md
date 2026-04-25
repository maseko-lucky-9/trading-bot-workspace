# Trading Bot Knowledge Base
**Generated:** 2026-04-25  
**Agent:** KnowledgeIngestor (Agent 1)  
**Books Processed:** 11  
**Source:** `/Users/ltmas/Documents/Obsidian Vault/wiki/ingested-books/`

---

## 1. Strategies Catalogue

### 1.1 Pairs Trading / Statistical Arbitrage (Mean-Reversion)

**Name:** Cointegration-Based Pairs Trading  
**Source:** Quantitative Trading (E.P. Chan)

**Signal Logic:**
- Entry: Enter long spread when z-score drops below -2 (buy GLD, short GDX). Enter short spread when z-score rises above +2.
- Exit: Close position when z-score reverts to within ±1 standard deviation of the mean.
- Z-score formula: `zscore = (spread - spreadMean) / spreadStd`
- Spread = Price_A - hedgeRatio * Price_B (hedge ratio from OLS regression on training set)

**Timeframe:** Daily  
**Instruments:** Cointegrated pairs (e.g., GLD/GDX — spot gold vs gold miner ETF)  
**Key Parameters:**
- Entry threshold: z-score >= 2 or <= -2 (standard), or 1 std dev for more active trading
- Exit threshold: z-score within ±1 of mean
- Lookback for mean/std: Training set (e.g., 252 days)
- Minimum backtest Sharpe ratio ≥ 1.5 with 2,739+ data points for >1.0 true Sharpe confidence

**Validation Notes:**
- Sharpe on training set ~2.3, test set ~1.5 for GLD/GDX example (Chan, 2021)
- Confirmed via ADF/cointegration test on training set before deployment
- Adding transaction costs essential: 5 bps/trade can flip a 4.47 Sharpe to -3.19

---

### 1.2 Triple-Barrier Method (ML-Ready Labeling Strategy)

**Name:** Triple-Barrier Labeling with Dynamic Thresholds  
**Source:** Advances in Financial Machine Learning (Lopez de Prado)

**Signal Logic:**
- Set three barriers: upper (profit-taking), lower (stop-loss), vertical (expiration)
- Barriers defined as multiples of rolling volatility: `trgt = ewm_std(returns, span=100) * multiplier`
- Label = 1 if upper barrier touched first; -1 if lower barrier touched first; 0 or sign(return) if vertical barrier touched first
- Configuration `ptSl=[1,1]` (symmetric), `ptSl=[1,2]` (asymmetric), `ptSl=[0,2]` (stop-only)

**Timeframe:** Any (event-driven bars preferred over time bars)  
**Instruments:** Any liquid instrument  
**Key Parameters:**
- `ptSl`: [profit_multiplier, stop_multiplier] applied to target volatility
- `t1`: expiration timestamp (holding period limit)
- `minRet`: minimum target return to generate a triple-barrier search
- `numDays`: typical 1-5 days for daily strategies

**Special Notes:**
- Path-dependent labeling: accounts for stop-outs that time-based methods ignore
- Recommended for all ML strategy training sets to avoid unrealistic backtests

---

### 1.3 Meta-Labeling (Secondary ML Filter)

**Name:** Meta-Labeling Position Sizing Layer  
**Source:** Advances in Financial Machine Learning (Lopez de Prado)

**Signal Logic:**
1. Primary model generates directional signal (side: long/short) — any model or rule
2. Secondary ML model predicts probability of the primary model's bet being correct
3. Size of position proportional to secondary model's predicted probability
4. Labels: 0 (false positive from primary) or 1 (true positive from primary)

**Use Case:** Combine white-box fundamental/technical model with ML overlay  
**Key Parameters:**
- Primary model: any rule-based or ML directional model (high recall, lower precision acceptable)
- Secondary model: binary classifier (Random Forest, Gradient Boosting) trained on meta-labels
- F1-score objective: secondary model filters false positives, increasing overall F1
- Asymmetric barriers allowed since side is known: `ptSl[0]` for profit, `ptSl[1]` for stop

---

### 1.4 Bollinger Band Mean-Reversion

**Name:** Bollinger Band Reversion  
**Source:** Technical Analysis of the Financial Markets (Murphy); Quantitative Trading (Chan)

**Signal Logic:**
- Entry Short: Price touches or exceeds upper band (+2 standard deviations above 20-period MA)
- Entry Long: Price touches or falls below lower band (-2 standard deviations below 20-period MA)
- Exit: Price reverts to within ±1 standard deviation of the moving average
- Bands: Upper = SMA(20) + 2*StdDev(20); Lower = SMA(20) - 2*StdDev(20)

**Timeframe:** 5-minute to daily (warning: 5-min ES without transaction costs gives Sharpe ~3, but with 1bp transaction cost Sharpe = -3)  
**Instruments:** Mean-reverting instruments; ES futures, FX pairs in consolidation  
**Key Parameters:**
- MA period: 20 (standard), 20 weeks / 20 months for longer timeframes
- Standard deviations: 2 for entry, 1 for exit
- Wide bands signal trend exhaustion; narrow bands signal potential trend initiation

---

### 1.5 Momentum / Short-Term Reversal

**Name:** Cross-Sectional Daily Reversal  
**Source:** Quantitative Trading (E.P. Chan)

**Signal Logic:**
- Buy stocks with worst prior-day returns; short stocks with best prior-day returns
- Weight proportional to negative distance from equal-weighted market return
- Update positions at market open (not close — avoids adverse selection)

**Timeframe:** Daily (update at open)  
**Instruments:** S&P 500 large-cap, S&P 400 mid-cap, S&P 600 small-cap (small-cap generates most returns)  
**Key Parameters:**
- 5 bps one-way transaction cost dramatically reduces performance on large-cap universe
- Backtest Sharpe ~4.47 (pre-costs, small-cap) vs ~0.25 (S&P 500 large-cap)

---

### 1.6 MT4 Trend-Pullback Method

**Name:** MACD Platinum / QQE Filter Pullback Method  
**Source:** Forex Trading – Jim Brown

**Signal Logic:**
- **Trend Identification:** Three MAs stacked: 50 EMA + 100 EMA + 240 LMA
- **Buy Entry:** MAs stacked upward + blue dot forms on MACD Platinum below zero level, confirmed by blue QMP Filter dot on candle close
- **Sell Entry:** MAs stacked downward + red dot on MACD Platinum above zero level, confirmed by red QMP Filter dot
- **Method 1 (Low Risk):** Stop at recent fractal high/low or opposite side of closest MA; manage each trade independently
- **Method 2 (High Risk):** No hard stop; add to position in Fibonacci sequence (1, 3, 5, 8...) if signal persists on same side of zero

**Timeframe:** 4H preferred; 1H for active traders; 1D for position traders  
**Instruments:** All FX majors; cross pairs; also metals, indices  
**Key Parameters:**
- MACD Platinum settings: 12, 26, 9
- QQE Adv settings: 1, 8, 3
- Higher-timeframe filter: input in minutes (e.g., 240 for 4H filter when trading 1H)
- Exit triggers: MACD Platinum crosses zero level (first warning), opposite-color dot (confirmation)
- Risk per trade: 1–2% of account balance (Method 1)

---

### 1.7 Naked Trading Price Action Setups

**Name:** Zone + Catalyst Reversal Trades  
**Source:** Naked Forex (Nekritin & Peters)

**Core Concept:** Price returns to historically significant support/resistance zones, then shows a "catalyst" candlestick pattern to trigger entry. No indicators required.

**Available Catalysts (Setups):**
1. **Kangaroo Tail** — Long wick/shadow rejecting a zone; body small; wick at least 2-3x body length; tail points away from zone
2. **Big Shadow** — Large candle engulfs prior candle; forms at zone boundary
3. **Wammie / Moolahs** — Double-bottom (wammie) or double-top (moolah) at a zone; second test weaker than first
4. **Last Kiss** — Price breaks through zone, retraces to "kiss" the broken zone from the other side, then resumes breakout direction
5. **Big Belt** — Large momentum candle at zone boundary (continuation setup)

**Zone Identification Rules:**
- Zones are AREAS, not precise price points ("beer belly" concept)
- Start with higher timeframe chart (one TF up) using line chart to find bends
- Zones get stronger with more historical touches
- Daily chart zones: ~100-200 pips apart; weekly: ~500 pips apart
- Only mark current TF zones + one-TF-higher zones (avoid minor zones)
- Zone age increases importance: zones from 5-15 years ago remain valid
- Entry ONLY when price reaches a zone AND a catalyst pattern forms

**Timeframe:** Daily preferred; 4H also valid  
**Instruments:** All FX pairs  
**Key Parameters:**
- Stop loss: beyond opposite side of catalyst candle
- Profit target: next zone boundary
- Do NOT trade without confirmed catalyst (price at zone alone is insufficient)

---

### 1.8 London/New York Session Breakout

**Name:** Session Overlap Breakout  
**Source:** Day Trading and Swing Trading the Currency Market (Lien)

**Signal Logic:**
- Pre-position during the final hour of Asian session (low volatility consolidation)
- Enter breakout of Asian session range at London open (2 AM–3 AM EST)
- Or trade on economic release breakout (especially NFP for largest move)

**Session Characteristics (Table 5.1, avg pip ranges 2002-2004):**
- **Asian (7 PM–4 AM EST):** EUR/USD 51, GBP/USD 78, USD/JPY 65; USD/JPY most volatile
- **European (2 AM–12 PM EST):** EUR/USD 79, GBP/USD 112; GBP/JPY and GBP/CHF most volatile (~150 pips)
- **U.S. (8 AM–5 PM EST):** EUR/USD 78, GBP/USD 94, GBP/JPY 129

**Key Data Events by Impact (EUR/USD, 20-min reaction):**
- Nonfarm Payrolls: highest magnitude, lasting reaction
- GDP: medium magnitude, mostly knee-jerk (fades within 60 min)
- CPI/PPI: medium; Trade Balance: lower
- Rule: On NFP day, full position; on GDP day, ~50% position; minor data: normal size

**Instruments:** EUR/USD, GBP/USD, USD/JPY  
**Timeframe:** 15M–1H for session breakout; 4H for position

---

### 1.9 Swing Trading with Market Structure

**Name:** Trend-Continuation Pullback (Quintessential Pattern)  
**Source:** The Art and Science of Technical Analysis (Grimes)

**Signal Logic:**
- Identify second-order or third-order trend via pivot highs/lows
- Uptrend: upswings longer than downswings, higher highs and higher lows
- Entry: Wait for pullback to prior pivot high (now support) or midpoint of prior up-swing
- Confirmation: Prior resistance holding as support (non-random price action = imbalance of buyers/sellers)
- Stop: Below most recent second-order pivot low
- Target: Prior high or measured move equal to the prior leg

**Key Concept:** Every edge in technical trading comes from an imbalance of buying and selling pressure, not the pattern itself. The pattern is only a manifestation of the underlying imbalance.

**Time Frame Relationship Rule:** For stops/targets, scale by sqrt(TF_ratio). Example: If 5-min chart uses $0.25 stops, 30-min chart needs $0.25 × sqrt(30/5) = $0.61 stops.

**Timeframe:** Any; 3-day to 2-week swings recommended to step outside HFT noise  
**Instruments:** Stocks, FX, futures

---

### 1.10 Classic Chart Pattern Breakouts

**Name:** Chart Pattern Breakout Trading  
**Source:** Technical Analysis of the Financial Markets (Murphy)

**Reversal Patterns:**
- **Head & Shoulders:** Three peaks, middle highest. Breakout below neckline. Target = head height measured down from neckline.
- **Double Top/Bottom:** Two tests of same level. Top confirmed on neckline breach. Min 1-month between peaks for validity.
- **Triple Top/Bottom:** Three tests; more confirmation than double.

**Continuation Patterns:**
- **Symmetrical Triangle:** Converging trendlines; breakout direction = prior trend. Volume should expand on breakout.
- **Ascending Triangle:** Flat top + rising bottom; bullish bias even without clear prior trend.
- **Descending Triangle:** Flat bottom + declining top; bearish.
- **Rectangle/Channel:** Horizontal bounds; trade in direction of prior trend on breakout.

**Volume Rule:** Breakouts on high volume are more reliable. Low-volume breakouts are suspect.

**Timeframe:** Daily, weekly charts preferred for pattern formation  
**Instruments:** All markets

---

## 2. Risk Frameworks

### 2.1 Position Sizing Formulas

#### Kelly Formula (Gaussian Returns)
**Source:** Quantitative Trading (Chan)

Full Kelly: `f* = (μ - r) / σ²`  
Where μ = expected return, r = risk-free rate, σ = standard deviation of returns

Practical Kelly (simplified for win/loss framing):  
`f* = p - (1-p)/b`  
Where p = probability of winning, b = win/loss ratio

**Important:** Use half-Kelly or quarter-Kelly in practice. Full Kelly is too aggressive and leads to large drawdowns. Kelly maximizes long-term geometric growth but requires precise estimation of p and b.

#### ATR-Based Position Sizing
**Source:** TSAM (Kaufman); Naked Forex

`Units = (Account_Risk_$ ) / (ATR_n * dollar_per_pip)`  
Where:
- `Account_Risk_$` = account equity × risk_per_trade_percentage (typically 1-2%)
- `ATR_n` = Average True Range over n periods (typically 14-20)
- Stop = 1x to 2x ATR from entry

ATR variable box formula (Kaufman): Box_size = ATR(50) * factor  
Factor range 0.75–1.5 tested; higher factor = fewer trades, larger risk per trade

#### Fixed Fractional (Standard Risk Per Trade)
**Source:** Jim Brown (Forex Trading); Naked Forex; TSAM

- Risk 1–2% of account per trade (industry standard maximum)
- Formula: `Lot_size = (Account × 0.01) / (Stop_pips × pip_value)`
- Never risk more than can be absorbed on a single loss without impairing future trading
- If unable to sleep with open trades, position is too large

#### Volatility Targeting
**Source:** TSAM (Kaufman)

- Annualized volatility target: typically 12% for conservative, up to 18% for hedge funds
- Daily std dev × sqrt(252) = annualized volatility
- Adjustment: `New_position_size = position × (target_vol / current_vol)`
- For every 10% portfolio drawdown from peak: cut position sizes 20%

---

### 2.2 Drawdown Rules

**Source:** TSAM (Kaufman); Quantitative Trading (Chan)

- Maximum acceptable drawdown for stand-alone strategy: >10% or >4 months duration signals Sharpe < 1
- Calmar Ratio = Annualized Return / Maximum Drawdown (higher is better)
- Sortino Ratio = Return / Downside_volatility (uses only negative return deviation)
- MAR Ratio = CAGR / Max Drawdown (leverage-independent measure)
- Portfolio drawdown trigger: 10% drawdown from peak → reduce position sizes 20%; every 6.67% recovery → add back 10%
- Hard rule: Never add to a losing position beyond plan (Method 2 in Jim Brown requires strict discipline)

---

### 2.3 Max Risk Per Trade Guidelines

**Source:** Multiple books

| Source | Guideline |
|--------|-----------|
| Jim Brown | 1–2% of account per individual trade |
| Naked Forex | Determined by stop distance from zone to catalyst candle |
| TSAM | Equalize risk across markets by position_size = 1% / (L × BPV) |
| Chan | Sharpe < 1 → not suitable as standalone; drawdown >10% or >4 months → review |
| Trading in the Zone | Losses are unavoidable and must be pre-accepted before trade entry |

---

### 2.4 Stop Loss Placement Methods

**Source:** Technical Analysis (Murphy); Naked Forex; Jim Brown; Art & Science (Grimes)

1. **Beyond recent fractal** (Jim Brown): Stop just above/below most recent swing high/low
2. **ATR-based stop**: 1–2× ATR below entry (trend following) or tighter for reversals
3. **Beyond zone boundary** (Naked Forex): Stop on opposite side of the zone catalyst formed at
4. **Pivot-based stop** (Grimes): Stop below second-order pivot low (uptrend) or above second-order pivot high (downtrend)
5. **MA-based stop** (Jim Brown): Stop on wrong side of relevant EMA (50 EMA, 100 EMA, 240 LMA)
6. **Volatility stop** (TSAM): Stop = entry ± k × ATR(n); k typically 1.5–3.0
7. **Never move stop in adverse direction**: Moving stops further away is a primary source of trading losses (Trading in the Zone)

**Key Principle (Grimes):** Stop distance on higher TF scales by sqrt(TF_ratio). A 30-min stop should be ~2.45× a 5-min stop for equivalent probability of getting stopped out by noise.

---

## 3. Market Mechanics

### 3.1 Session Times and Characteristics

**Source:** Day Trading and Swing Trading the Currency Market (Lien)

| Session | Time (EST) | Characteristics |
|---------|-----------|-----------------|
| Sydney | 5 PM Sun–2 AM Mon | Lowest volume; sets early direction |
| Tokyo | 7 PM–4 AM | USD/JPY most active; carry trade flows; Japanese exporters repatriate |
| Singapore/HK | 9 PM–4 AM | Supplements Tokyo; runs stop levels |
| London (Frankfurt) | 2 AM–12 PM | Highest volume; GBP/CHF and GBP/JPY most volatile; major trends start here |
| New York | 8 AM–5 PM | Most transactions 8 AM–12 PM (Europe still open); NFP reaction day |
| London-NY Overlap | 8 AM–12 PM | Highest liquidity of any session; tightest spreads |

**Session Volatility Rankings (daily pip range):**
- European: GBP/JPY ~150 pips; EUR/USD ~79 pips
- US: GBP/CHF ~129 pips; USD/JPY ~107 pips  
- Asian: USD/JPY ~65 pips; EUR/USD ~51 pips (lowest major)

**Key Intraday Pattern:** FX market closes daily at 5 PM New York time. This closing price is significant because it represents the battle between bulls and bears for the day; European and American traders both influenced it.

---

### 3.2 Spread Behaviour Patterns

**Source:** Day Trading and Swing Trading (Lien); Forex Trading for Beginners

- **OTC structure**: No exchange or clearing fees → lower transaction costs than equities
- **Spread definition**: Bid-ask difference measured in pips (EUR/USD example: 1.1051/1.1053 = 2 pip spread)
- **Pip value**: 1/10,000 of a dollar (0.0001) for most pairs; 1/100 for JPY pairs
- **Tighter spreads**: During major session overlaps (London-NY) and high-liquidity pairs (EUR/USD, USD/JPY)
- **Wider spreads**: Overnight/thin sessions; exotic pairs; during major news events immediately before release
- **ESMA leverage limits**: 30:1 for majors, 20:1 for other FX, 10:1 for commodities, 2:1 for crypto
- **Impact on systems**: A 1 basis point transaction cost on a 5-min ES Bollinger Band strategy flips Sharpe from +3 to -3

---

### 3.3 Liquidity Patterns

**Source:** Day Trading and Swing Trading (Lien); TSAM (Kaufman)

- FX daily volume: ~$3.2 trillion (2007 BIS data; ~20× combined NYSE + NASDAQ)
- GBP/JPY and GBP/CHF: Highest intraday volatility due to double currency conversion (GBP/USD × USD/JPY)
- EUR/USD: Most liquid pair; best bid-ask spreads; most suitable for low-friction systems
- USD/JPY: Heavily influenced by Japanese institutional investors, BoJ intervention, carry trade unwinding
- Risk-tolerant session picks (Asian): USD/JPY, GBP/CHF (~90 pip average)
- Risk-averse session picks (US): USD/JPY, EUR/USD, USD/CAD (~78-94 pips)
- Spot price for FX closes at 5 PM EST; interbank-only trading after that until Sydney reopens

**Key Liquidity Void Warning (TSAM):** V-top/V-bottom reversals create liquidity voids at extremes — many sellers, no buyers. High volatility + high volume accompanies these reversals.

---

### 3.4 Volatility Patterns

**Source:** Technical Analysis (Murphy); TSAM (Kaufman); ML for Algo Trading (Stefan Jansen)

- **Bollinger Band expansion** = volatility increasing; expansion after contraction often signals new trend
- **Bollinger Band contraction** = volatility decreasing; often precedes explosive breakout
- **ATR-based volatility cycles**: Markets alternate between high and low volatility (trending vs ranging)
- **Session volatility cycle**: European open creates day's highest-volume window; typically sets daily range
- **NFP day**: EUR/USD 20-min move historically highest of any data release (magnitude ~2-3× GDP release)
- **VIX relationship**: Rising equity volatility correlates with USD safe-haven bid; FX pairs involving JPY and CHF amplify
- **Efficiency Ratio (TSAM Kaufman)**: ER = net_change / sum(absolute_changes); near 1.0 = strong trend (low noise); near 0 = choppy/mean-reverting
- **Stationarity**: Raw price series non-stationary; returns stationary; fractional differentiation preserves memory while achieving stationarity

---

## 4. Quantitative Methods

### 4.1 Technical Indicators and Formulas

#### Moving Averages
**Source:** Technical Analysis of the Financial Markets (Murphy); Jim Brown

- **SMA(n)**: Sum(close_i, n) / n — equal weight; lag = n/2 bars
- **EMA(n)**: close_t × (2/(n+1)) + EMA_(t-1) × (1 - 2/(n+1)) — exponential weight; faster response
- **LMA**: Linear Weighted Moving Average — most recent gets highest weight
- Popular commodity MA periods: 4, 9, 18 days
- Popular stock periods: 50, 200 days
- Jim Brown system: 50 EMA + 100 EMA + 240 LMA for trend stacking

**Trend signals:**
- Price above 200-day MA = uptrend context; below = downtrend context
- MA crossover: shorter crosses above longer → buy signal; below → sell signal
- 4-week rule: buy on 4-week high breakout; sell on 4-week low breakdown (Kaufman)

#### RSI (Relative Strength Index)
**Source:** Technical Analysis (Murphy)

`RSI = 100 - (100 / (1 + RS))`  
`RS = Average_Gain(14) / Average_Loss(14)`

- Standard period: 14
- Overbought: RSI > 70; Oversold: RSI < 30
- Divergence: Price makes new high but RSI doesn't → bearish divergence (and vice versa)
- Limitation: Lags price; naked trading enters earlier than RSI-triggered entries (Naked Forex)

#### MACD (Moving Average Convergence/Divergence)
**Source:** Technical Analysis (Murphy); Jim Brown

`MACD Line = EMA(12) - EMA(26)`  
`Signal Line = EMA(9) of MACD Line`  
`Histogram = MACD Line - Signal Line`

- Buy signal: MACD crosses above signal line; especially when below zero
- Sell signal: MACD crosses below signal line; especially when above zero
- **MACD Platinum (Jim Brown)**: Modified MACD with ShowMarkers=true for dot visualization; blue dots = buy setup; red dots = sell setup; dots below zero level = buy context; above = sell context
- Divergence (MACD vs price): powerful confirmation for counter-trend entries
- Theory: MACD oscillates around zero level like a rubber band; tends to revert to zero

#### Bollinger Bands
**Source:** Technical Analysis (Murphy); Quantitative Trading (Chan)

`Upper = SMA(20) + 2 × StdDev(20)`  
`Lower = SMA(20) - 2 × StdDev(20)`

- 95% of price data falls within ±2 standard deviations of mean
- Bands expand with rising volatility; contract with falling volatility
- Overbought = price at upper band; oversold = price at lower band
- Bandwidth narrowing (squeeze) = volatility compression → explosive breakout pending
- Can apply to weekly (20 weeks) and monthly (20 months) charts

#### Stochastics (K%D)
**Source:** Technical Analysis (Murphy)

`%K = 100 × (Close - Lowest_Low(14)) / (Highest_High(14) - Lowest_Low(14))`  
`%D = SMA(3) of %K`

- Overbought: >80; Oversold: <20
- Buy signal: %K crosses above %D in oversold territory
- Sell signal: %K crosses below %D in overbought territory

#### ATR (Average True Range)
**Source:** TSAM (Kaufman); Jim Brown; Naked Forex

`True Range = max(High-Low, |High-Close_prev|, |Low-Close_prev|)`  
`ATR(n) = SMA(TrueRange, n)` or `EWM(TrueRange, span=n)`

- Standard period: 14 or 20 bars
- Primary use: stop placement, position sizing, volatility measurement
- Box size for point-and-figure: ATR(50) × factor

#### Efficiency Ratio (ER)
**Source:** TSAM (Kaufman)

`ER = |Price_t - Price_(t-n)| / Sum(|Price_i - Price_(i-1)|, i=t-n to t)`

- ER near 1.0 = strong trending market (low noise)
- ER near 0 = choppy/random market
- Used to adapt MA calculation periods (KAMA — Kaufman Adaptive Moving Average)

---

### 4.2 Feature Engineering Techniques

**Source:** Machine Learning for Algorithmic Trading (Jansen); Advances in Financial Machine Learning (de Prado)

#### Fractional Differentiation (de Prado)
**Purpose:** Achieve stationarity while preserving maximum memory in time series

- Integer differentiation (d=1): stationary but destroys memory/predictive content
- d=0 (raw price): has memory but non-stationary → ML models fail
- Fractional d (e.g., 0.3-0.5): stationary + memory preserved
- Method: Apply binomial weights to past prices; truncate when weight < threshold (e.g., 1e-4)
- Test: Find minimum d such that ADF test rejects unit root hypothesis

#### Return-Based Features
- Log returns: `r_t = log(P_t / P_(t-1))` — additive, more normal distribution
- Excess returns: `r_t - r_f` (subtract risk-free rate only if financing cost exists)
- Rolling Sharpe: `sqrt(252) × mean(returns(n)) / std(returns(n))`
- Realized volatility: `sqrt(sum(r_i^2, n) × 252/n)` (annualized)
- Autocorrelation features: lag-1, lag-5 returns

#### Volume / Microstructure Features (de Prado Chapter 19)
- Dollar bars (sample every $X traded): more homoscedastic than time bars
- Volume bars: sample every N contracts; normalizes for intraday volume patterns
- Tick bars: sample every N ticks; captures fast markets better
- Quote imbalance: (bid_size - ask_size) / (bid_size + ask_size)
- Trade imbalance: (buy_volume - sell_volume) / total_volume
- Roll model spread estimate: `2 × sqrt(-cov(delta_price_t, delta_price_(t-1)))`

#### Momentum and Trend Features
- Rate of change: `ROC(n) = (P_t - P_(t-n)) / P_(t-n)`
- RSI: 14-period standard
- Slope of linear regression over n bars
- Hurst exponent: H > 0.5 indicates trending; H < 0.5 indicates mean-reversion; H = 0.5 = random walk

#### Entropy Features (de Prado Chapter 18)
- Shannon entropy: `H(X) = -sum(p(x) × log(p(x)))`
- Lempel-Ziv estimator: measures encoding complexity of price series
- Application: Detect regime changes; high entropy = unpredictable/random market

---

### 4.3 Backtesting Validation Methods

#### Purged K-Fold Cross-Validation
**Source:** Advances in Financial Machine Learning (de Prado, Chapter 7)

**Problem with standard K-fold CV in finance:**
- Test observations may overlap in time with training observations (look-ahead contamination)
- Adjacent samples are correlated → data leakage → inflated Sharpe ratios

**Solution — Purged K-Fold:**
1. Split data into k folds by time (keep time order)
2. For each test fold, purge from training set all observations whose outcomes overlap with the test set
3. Apply embargo: remove additional training observations adjacent to test fold boundaries
4. Train on remaining samples; evaluate on test fold

**Implementation:** `sklearn.model_selection.PurgedKFold` (custom or mlfinlab)

#### Combinatorial Purged Cross-Validation (CPCV)
**Source:** Advances in Financial Machine Learning (de Prado, Chapter 12)

- Generates many train/test combinations across the full timeline
- Produces a distribution of Sharpe ratios (not a single estimate)
- Better addresses backtest overfitting by showing how Sharpe varies across path combinations
- Replaces single walk-forward with probabilistic assessment of strategy robustness

#### Walk-Forward (Historical) Backtesting
**Source:** Quantitative Trading (Chan); ML for Algo Trading (Jansen)

- Divide data: training (in-sample) → test (out-of-sample) → optional walk-forward
- Minimum sample size for backtest confidence:
  - Sharpe ≥ 1, 95% confidence that true Sharpe > 0: need 681 daily data points (~2.71 years)
  - Sharpe ≥ 2: need only 174 data points (~0.69 years)
  - Confidence that true Sharpe > 1: need Sharpe ≥ 1.5 and 2,739 data points (~10.87 years)
- Deflated Sharpe Ratio: accounts for number of strategy tweaks performed; more tweaks → larger deflation

#### Backtesting on Synthetic Data
**Source:** Advances in Financial Machine Learning (de Prado, Chapter 13)

- Simulate price paths with known statistical properties
- Test strategy on synthetic data before committing to historical data
- Avoids fitting to historical accidents; reveals whether strategy relies on structural properties

#### Common Backtesting Pitfalls
**Source:** Quantitative Trading (Chan); TSAM (Kaufman); ML for Algo Trading (Jansen)

1. **Look-ahead bias**: Using future data to make past decisions (e.g., labeling with day's low before market close)
2. **Survivorship bias**: Only testing on securities that still exist (excludes delisted/bankrupt companies)
3. **Data snooping bias**: Repeated optimization on same dataset inflates backtest Sharpe
4. **Transaction costs omission**: A strategy with 4.47 pre-cost Sharpe can become -3.19 after 5bps/trade
5. **Parameter overfitting**: More than 5 free parameters increases overfitting risk significantly
6. **Rule of thumb**: ≤5 parameters including entry/exit thresholds, lookback periods, holding periods

---

### 4.4 Statistical Methods

#### Sharpe Ratio
**Source:** Quantitative Trading (Chan)

`Annualized_Sharpe = sqrt(N_T) × mean(excess_returns) / std(excess_returns)`

Where N_T = number of trading periods per year:
- Daily trading: 252
- Hourly trading: 252 × 6.5 = 1,638
- 5-minute trading: 252 × 6.5 × 12 = 19,656
- Dollar-neutral strategy: do NOT subtract risk-free rate (portfolio is self-financing)
- Long-only overnight strategy: DO subtract risk-free rate (financing cost exists)

Thresholds:
- Sharpe < 1: not suitable as stand-alone strategy
- Sharpe > 2: typically profitable most months
- Sharpe > 3: typically profitable most days

#### Cointegration and ADF Test
**Source:** Quantitative Trading (Chan)

- ADF (Augmented Dickey-Fuller) test: null hypothesis = unit root (non-stationary)
- p-value < 0.05: reject null → series is stationary → mean-reverting behavior
- Cointegration: two non-stationary series with stationary linear combination (spread)
- Johansen test: multivariate cointegration; allows for more than 2 series
- Half-life of mean reversion: estimate from Ornstein-Uhlenbeck process fitting; shorter half-life = faster reversion

#### Position Sizing with ML Predictions
**Source:** Advances in Financial Machine Learning (de Prado, Chapter 10)

- Bet size proportional to predicted probability: `size = |p - 0.5| × 2` (maps [0.5, 1.0] to [0, 1])
- Alternatively: `size = discrete_quantile(predicted_probability, n_buckets)`
- Averaging active bets: when multiple signals overlap, average their sizes to avoid over-concentration

#### Hierarchical Risk Parity (HRP)
**Source:** ML for Algo Trading (Jansen, Chapter 12)

- Alternative to Markowitz optimization that avoids inversion of unstable covariance matrix
- Clusters assets by correlation (hierarchical clustering)
- Allocates risk inversely proportional to variance within clusters
- More stable out-of-sample than mean-variance optimization

#### Random Forest Feature Importance
**Source:** ML for Algo Trading (Jansen); Advances in Financial Machine Learning (de Prado, Chapter 8)

- Mean Decrease Impurity (MDI): fast but biased toward high-cardinality features
- Mean Decrease Accuracy (MDA): robust but computationally expensive
- Single Feature Importance (SFI): evaluate each feature independently; avoids substitution effects
- Recommendation (de Prado): use MDA for reliable importance measurement

---

## 5. Trading Psychology Principles

**Source:** Trading in the Zone (Mark Douglas)

### Core Principles for Systematic/Algorithmic Trading

1. **Think in Probabilities**: Each trade outcome is uncertain. The edge exists over a large sample, not on individual trades. Never evaluate system performance on fewer than 20-30 trades.

2. **Accept the Risk Fully**: Pre-define maximum acceptable loss before entering any trade. A stop loss must be determined before entry, not adjusted during the trade. Moving stops adversely is the primary cause of large losses.

3. **Four Primary Trading Fears** (that automated systems help eliminate):
   - Fear of being wrong
   - Fear of losing money
   - Fear of missing out
   - Fear of leaving money on the table

4. **Rule-Based Discipline**: "Patience, Courage, Discipline." Wait for the setup, execute without hesitation, follow the rules exactly.

5. **System Validation Before Live Trading**: The consistent winners back-test their systems extensively. They know precisely the type of drawdown they will face.

6. **Losses Are Expected**: In any probabilistic system, losses are unavoidable components. The goal is that the sum of wins > sum of losses over N trades, not that every trade wins.

7. **Market is Neutral**: The market does not cause emotional pain — your interpretation of market information does. Systematic rules remove this interpretation step.

---

## 6. Systematic Trading Architecture

**Source:** TSAM (Kaufman); ML for Algo Trading (Jansen); Advances in Financial Machine Learning (de Prado)

### Pipeline Components

```
Raw Data → Bar Formation → Feature Engineering → Signal Generation → 
Position Sizing → Risk Management → Execution → P&L Attribution
```

### Signal Construction Layers
1. **Primary signal**: Directional model (trend, mean-reversion, ML classifier)
2. **Filter layer**: Meta-labeling / regime detection / session filter / news filter
3. **Size layer**: Kelly/ATR/fixed-fractional position sizing
4. **Risk layer**: Stop loss, drawdown limits, correlation limits across positions

### Robustness Principles (Kaufman)
- "Success with fewer rules over more markets and data yields robustness"
- A robust strategy: return/risk profile not attractive as curve-fitted, but stable OOS
- Test across: multiple markets, multiple time periods, multiple parameter values
- Parameter sensitivity: Robust parameter choices show consistent results across a range, not a single optimal point

### Regime Detection
- Efficiency Ratio (ER) > 0.5: trending regime → use trend-following parameters
- ER < 0.3: mean-reverting regime → use oscillators/mean-reversion parameters
- ADX > 25: trending; ADX < 20: ranging (Murphy, Chapter 15)

---

## Appendix: Quick Reference Card

### Sharpe Ratio Annualization Multipliers
| Data Frequency | N_T | sqrt(N_T) |
|----------------|-----|-----------|
| Daily | 252 | 15.87 |
| Weekly | 52 | 7.21 |
| Hourly | 1,638 | 40.47 |
| 5-Minute | 19,656 | 140.2 |

### FX Session Hours (EST)
| Session | Start | End |
|---------|-------|-----|
| Sydney | 5 PM Sun | 2 AM |
| Tokyo | 7 PM | 4 AM |
| London | 3 AM | 12 PM |
| New York | 8 AM | 5 PM |
| London-NY Overlap | 8 AM | 12 PM |

### Minimum Backtest Data Requirements (Chan)
| Desired Confidence | Required Backtest Sharpe | Min Data Points |
|-------------------|--------------------------|-----------------|
| True Sharpe > 0 (95%) | ≥ 1.0 | 681 (~2.7 yrs daily) |
| True Sharpe > 0 (95%) | ≥ 2.0 | 174 (~0.7 yrs daily) |
| True Sharpe > 1 (95%) | ≥ 1.5 | 2,739 (~10.9 yrs daily) |

### Triple-Barrier Configuration Guide
| ptSl Config | Use Case |
|-------------|----------|
| [1, 1, 1] | Standard: profit target + stop + expiration |
| [1, 1, 0] | No expiration; hold until hit horizontal barrier |
| [0, 1, 1] | Stop-only: exit at expiration or stop, no profit target |
| [1, 2, 1] | Asymmetric: tight profit target, wider stop |
