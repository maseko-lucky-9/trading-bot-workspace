# Trading Bot Knowledge Base

Distilled from 11 ingested books (Chan, AFML, Jansen, Kaufman, Lien, Douglas, Grimes, etc.)

---

## EMA Crossover Strategy

Use a fast EMA (9) and slow EMA (21) to detect momentum shifts.
- BUY when the fast EMA crosses above the slow EMA
- SELL when the fast EMA crosses below the slow EMA
- Use ATR×1.5 for stop-loss and ATR×3 for take-profit

## Mean Reversion Strategy

Trade price reversions to the Bollinger Band mid-line.
- BUY when price touches lower band and RSI < 30 (oversold)
- SELL when price touches upper band and RSI > 70 (overbought)
- Use ATR×1.5 for stop-loss and ATR×2 for take-profit

## Pairs Trading Strategy

Exploit the cointegrating relationship between correlated forex pairs.
- Compute the rolling hedge ratio via OLS regression
- Enter when the z-score of the spread exceeds ±2
- Exit when the z-score reverts to within ±0.5 of zero
- Only trade when the half-life of mean reversion is < 60 bars

## Risk Management

All position sizing follows ATR-based fractional risk.
- Risk 1% of equity per trade (max_risk_per_trade: 0.01)
- Kelly fraction: 0.25 (half-Kelly; computed from rolling win-rate × payoff ratio)
- Never risk more than 10 lots on a single position
- Halt trading when drawdown exceeds 20% from peak (trailing_dd_halt: 0.20)
- Halt after 5 consecutive losing trades (consecutive_loss_halt: 5)
- Halt if daily loss exceeds 2% of equity (daily_loss_limit: 0.02)

## Position Sizing Rules

ATR-based formula:
    risk_$ = balance × max_risk_per_trade
    lots   = risk_$ / (ATR × atr_multiplier × pip_value_per_lot / pip_size)

Apply Kelly multiplier after base sizing when ≥30 closed trades are available.
Downweight correlated pairs: lots × (1 − |ρ|) for the secondary symbol.

## Session Filter

Trade only during liquid market sessions:
- London:   07:00–16:00 UTC
- New York: 12:00–21:00 UTC
- Tokyo:    00:00–09:00 UTC
- Sydney:   21:00–06:00 UTC (wraps midnight)

London/New York overlap (12:00–16:00 UTC) is the highest-liquidity window.

## Regime Detection

Two regimes:
- TREND (0): low volatility, directional price action
- RANGE (1): high volatility, mean-reverting / choppy price action

Use rolling log-return volatility vs its median (vol method) or a 2-state
Gaussian HMM (hmm method). Prefer TREND-following strategies in TREND regime
and mean-reversion strategies in RANGE regime.

## Deflated Sharpe Ratio

After N backtest trials, the observed best Sharpe is upward-biased.
The DSR corrects for this by subtracting the expected maximum of N
i.i.d. Gaussian draws (Gumbel approximation).

    DSR = Sharpe_observed − E[max of N Gaussians]

A positive DSR suggests edge beyond multiple-testing noise.
A DSR > 1.0 on out-of-sample data is a meaningful hurdle.

## Backtest Integrity Rules

1. Always use real bridge data; never optimize on synthetic bars.
2. Apply per-symbol transaction costs: spread + slippage + commission.
3. Use next-bar-open entry (not close of signal bar) to avoid look-ahead.
4. Size positions with RiskManager, not flat lots.
5. Evaluate the optimization objective with purged k-fold CV (k=5, embargo=24 bars).
6. Report DSR alongside raw Sharpe at end of autoresearch runs.

## Meta-Labelling

Wrap any base strategy with a probability-of-profit classifier:
- Features: log-returns (1/5/20 bar), ATR-normalised vol, RSI, EMA spread, regime
- Label: 1 if trade closed profitably, 0 otherwise
- Model: Logistic Regression (fast) or Gradient Boosting (richer)
- Only emit a signal when P(profit) > threshold (default: 0.55)
- Train after min_train_trades (default: 20) labelled examples are available
