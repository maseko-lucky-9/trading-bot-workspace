"""Pairs trading strategy (Wave 3 — Chan, F-pairs).

Implements a cointegration-based spread trading strategy for two correlated
forex pairs (default: EURUSD / GBPUSD).

Theory
------
If two price series are cointegrated their spread (s1 - β * s2) is
stationary with a finite half-life of mean reversion.  When the z-score of
the spread is sufficiently large, the expected value of a reversion trade is
positive.

Algorithm
---------
1. Rolling OLS hedge ratio β over the last ``hedge_window`` bars.
2. Spread = s1 - β * s2.
3. Z-score = (spread - rolling_mean) / rolling_std over ``spread_window`` bars.
4. Entry when |z| > ``entry_zscore``; no entry when half-life > ``spread_window``
   (the reversion is too slow to trade profitably).
5. Generates a BUY signal (long s1, short s2) when z < -entry_zscore, SELL
   (short s1, long s2) when z > +entry_zscore.

The cointegration ADF test is run lazily (every ``coint_check_every`` calls)
using ``statsmodels.tsa.stattools.coint``.  The p-value is informational
and does not gate signal generation by default — set
``require_cointegration=True`` to enforce it.

Interface
---------
Because this strategy operates on two synchronised price series it provides
``generate_signal_pairs(df1, df2)`` in addition to the standard
``generate_signal(df)`` method (which returns HOLD with a descriptive
reason directing callers to use the pairs method).
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from core.strategy.base import Signal, Strategy
from core.strategy.indicators import atr as _atr_series


class PairsTradingStrategy(Strategy):
    """Cointegration-based spread trading for two forex pairs."""

    name = "pairs_trading"

    def __init__(
        self,
        symbol1: str = "EURUSD",
        symbol2: str = "GBPUSD",
        entry_zscore: float = 2.0,
        exit_zscore: float = 0.5,
        spread_window: int = 60,
        hedge_window: int = 60,
        atr_sl_multiplier: float = 1.5,
        atr_period: int = 14,
        coint_pvalue_threshold: float = 0.05,
        require_cointegration: bool = False,
        coint_check_every: int = 20,
    ) -> None:
        self.symbol1 = symbol1
        self.symbol2 = symbol2
        self.entry_zscore = entry_zscore
        self.exit_zscore = exit_zscore
        self.spread_window = spread_window
        self.hedge_window = hedge_window
        self.atr_sl_multiplier = atr_sl_multiplier
        self.atr_period = atr_period
        self.coint_pvalue_threshold = coint_pvalue_threshold
        self.require_cointegration = require_cointegration
        self.coint_check_every = coint_check_every
        self._call_count = 0
        self._last_coint_pvalue: float = 0.0

    # ------------------------------------------------------------------ #
    # Statistical helpers                                                #
    # ------------------------------------------------------------------ #

    def hedge_ratio(self, s1: pd.Series, s2: pd.Series) -> float:
        """OLS hedge ratio β: s1 ~ β·s2 (uses last ``hedge_window`` bars)."""
        n = min(self.hedge_window, len(s1), len(s2))
        y = s1.iloc[-n:].values.astype(float)
        x = s2.iloc[-n:].values.astype(float)
        var_x = float(np.var(x, ddof=1))
        if var_x == 0 or np.isnan(var_x):
            return 1.0
        cov = float(np.cov(x, y, ddof=1)[0, 1])
        return cov / var_x

    def spread(self, s1: pd.Series, s2: pd.Series, beta: float) -> pd.Series:
        """Compute the spread series: s1 - β·s2."""
        return s1 - beta * s2

    def half_life(self, spread_series: pd.Series) -> float:
        """Half-life of mean reversion estimated by AR(1) regression on the spread.

        Returns ``float("inf")`` when the spread is not mean-reverting
        (φ ≥ 0) or when there is insufficient data.
        """
        lagged = spread_series.shift(1)
        delta = spread_series - lagged
        mask = ~(lagged.isna() | delta.isna())
        x = lagged[mask].values.astype(float)
        y = delta[mask].values.astype(float)
        if len(x) < 2:
            return float("inf")
        var_x = float(np.var(x, ddof=1))
        if var_x == 0 or np.isnan(var_x):
            return float("inf")
        phi = float(np.cov(x, y, ddof=1)[0, 1]) / var_x
        if phi >= 0 or phi <= -1:
            return float("inf")  # non-mean-reverting or degenerate AR(1) coefficient
        return -math.log(2) / math.log(1.0 + phi)

    def cointegration_pvalue(self, s1: pd.Series, s2: pd.Series) -> float:
        """Run Engle-Granger cointegration test; return the ADF p-value."""
        try:
            from statsmodels.tsa.stattools import coint
            _, pvalue, _ = coint(s1.values.astype(float), s2.values.astype(float))
            return float(pvalue)
        except Exception:
            return 1.0

    # ------------------------------------------------------------------ #
    # Signal generation                                                  #
    # ------------------------------------------------------------------ #

    def generate_signal_pairs(
        self,
        df1: pd.DataFrame,
        df2: pd.DataFrame,
    ) -> Signal:
        """Generate a spread signal from two synchronised OHLCV DataFrames.

        Convention
        ----------
        BUY  = buy s1, short s2 (spread expected to rise / revert up)
        SELL = short s1, buy s2 (spread expected to fall / revert down)

        Parameters
        ----------
        df1 : OHLCV DataFrame for ``symbol1`` (primary leg).
        df2 : OHLCV DataFrame for ``symbol2`` (hedge leg).
        """
        self._call_count += 1
        min_bars = max(self.spread_window, self.hedge_window) + 2
        if len(df1) < min_bars or len(df2) < min_bars:
            return Signal(action="HOLD", reason="insufficient_bars")

        s1 = df1["close"]
        s2 = df2["close"]

        # Lazy cointegration check (expensive ADF test)
        if self._call_count % self.coint_check_every == 1:
            self._last_coint_pvalue = self.cointegration_pvalue(
                s1.iloc[-self.spread_window:], s2.iloc[-self.spread_window:]
            )

        if (
            self.require_cointegration
            and self._last_coint_pvalue >= self.coint_pvalue_threshold
        ):
            return Signal(
                action="HOLD",
                reason=f"not_cointegrated p={self._last_coint_pvalue:.3f}",
            )

        beta = self.hedge_ratio(s1, s2)
        spd = self.spread(s1, s2, beta)

        # Rolling z-score of the spread
        spd_mean = spd.rolling(self.spread_window).mean()
        spd_std  = spd.rolling(self.spread_window).std(ddof=1)
        z_series = (spd - spd_mean) / spd_std.replace(0, np.nan)
        current_z = float(z_series.iloc[-1])

        if np.isnan(current_z):
            return Signal(action="HOLD", reason="insufficient_spread_data")

        # Reject if mean-reversion half-life is longer than the spread window
        hl = self.half_life(spd.iloc[-self.spread_window:])
        spread_meta_base = {
            "hedge_ratio": beta,
            "spread_zscore": current_z,
            "spread": float(spd.iloc[-1]),
            "half_life": hl,
            "coint_pvalue": self._last_coint_pvalue,
        }
        if hl > self.spread_window:
            return Signal(
                action="HOLD",
                reason=f"half_life_too_long={hl:.0f}",
                meta=spread_meta_base,
            )

        # Entry decision
        action = "HOLD"
        reason = "no_signal"
        if current_z < -self.entry_zscore:
            action = "BUY"
            reason = f"spread_z={current_z:.2f}"
        elif current_z > self.entry_zscore:
            action = "SELL"
            reason = f"spread_z={current_z:.2f}"

        close1 = float(s1.iloc[-1])
        atr_val = float(
            _atr_series(df1.iloc[-max(self.atr_period * 2, 20):], self.atr_period).iloc[-1]
        )
        strength = (
            min(1.0, abs(current_z) / self.entry_zscore)
            if action != "HOLD" else 0.0
        )

        meta: dict = {**spread_meta_base, "entry_price": close1}
        if action == "BUY":
            meta["sl"] = close1 - self.atr_sl_multiplier * atr_val
            meta["tp"] = close1 + 2.0 * self.atr_sl_multiplier * atr_val
        elif action == "SELL":
            meta["sl"] = close1 + self.atr_sl_multiplier * atr_val
            meta["tp"] = close1 - 2.0 * self.atr_sl_multiplier * atr_val

        ts_raw = df1.iloc[-1].get("time") if "time" in df1.columns else None
        ts: datetime
        if ts_raw is None:
            ts = datetime.now(tz=timezone.utc)
        elif isinstance(ts_raw, pd.Timestamp):
            ts = ts_raw.to_pydatetime()
        else:
            ts = ts_raw

        return Signal(action=action, strength=strength, reason=reason, timestamp=ts, meta=meta)

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        """Single-DataFrame interface — always returns HOLD.

        PairsTradingStrategy requires two synchronised price series.
        Use ``generate_signal_pairs(df1, df2)`` instead.
        """
        return Signal(
            action="HOLD",
            reason="use_generate_signal_pairs_for_pairs_trading",
        )


