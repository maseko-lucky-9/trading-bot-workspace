"""
Bollinger Band Mean Reversion strategy.

Signals: price touches lower band + RSI oversold -> BUY;
         price touches upper band + RSI overbought -> SELL.
ATR reuses the helper from ema_crossover rather than duplicating it.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from core.strategy.base import Signal, Strategy
from core.strategy.indicators import atr as _atr


class BollingerBandMeanReversion(Strategy):
    """Bollinger Band + RSI mean-reversion strategy.

    Parameters
    ----------
    bb_period : int
        Lookback for the Bollinger Band (SMA + std-dev bands).
    bb_std : float
        Number of standard deviations for the bands.
    rsi_period : int
        RSI lookback using Wilder EWM.
    rsi_oversold : float
        RSI threshold below which the market is considered oversold.
    rsi_overbought : float
        RSI threshold above which the market is considered overbought.
    atr_period : int
        ATR lookback for SL/TP sizing.
    atr_sl_multiplier : float
    atr_tp_multiplier : float
    """

    name = "bollinger_mean_reversion"

    def __init__(
        self,
        bb_period: int = 20,
        bb_std: float = 2.0,
        rsi_period: int = 14,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
        atr_period: int = 14,
        atr_sl_multiplier: float = 1.5,
        atr_tp_multiplier: float = 2.0,
    ) -> None:
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.atr_period = atr_period
        self.atr_sl_multiplier = atr_sl_multiplier
        self.atr_tp_multiplier = atr_tp_multiplier

    def _rsi(self, close: pd.Series) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        # Wilder smoothing: alpha = 1/period
        avg_gain = gain.ewm(alpha=1 / self.rsi_period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / self.rsi_period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        return 100.0 - (100.0 / (1.0 + rs))

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["bb_mid"] = out["close"].rolling(self.bb_period).mean()
        bb_std = out["close"].rolling(self.bb_period).std(ddof=0)
        out["bb_upper"] = out["bb_mid"] + self.bb_std * bb_std
        out["bb_lower"] = out["bb_mid"] - self.bb_std * bb_std
        out["rsi"] = self._rsi(out["close"])
        out["atr"] = _atr(out, self.atr_period)
        return out

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        min_bars = max(self.bb_period, self.rsi_period, self.atr_period) + 2
        if len(df) < min_bars:
            return Signal(action="HOLD", strength=0.0, reason="insufficient_bars")

        ind = self.compute_indicators(df)
        last = ind.iloc[-1]

        close = float(last["close"])
        rsi = float(last["rsi"]) if not np.isnan(last["rsi"]) else 50.0
        atr = float(last["atr"]) if not np.isnan(last["atr"]) else 0.0
        bb_lower = float(last["bb_lower"]) if not np.isnan(last["bb_lower"]) else close
        bb_upper = float(last["bb_upper"]) if not np.isnan(last["bb_upper"]) else close
        bb_mid = float(last["bb_mid"]) if not np.isnan(last["bb_mid"]) else close

        action = "HOLD"
        reason = "no_signal"
        strength = 0.0
        meta: dict = {
            "bb_upper": bb_upper,
            "bb_lower": bb_lower,
            "bb_mid": bb_mid,
            "rsi": rsi,
            "atr": atr,
            "entry_price": close,
        }

        if close <= bb_lower and rsi < self.rsi_oversold:
            action = "BUY"
            reason = "price_at_lower_band_rsi_oversold"
            distance = bb_mid - bb_lower
            strength = float(min(1.0, (1.0 - rsi / self.rsi_oversold) * 0.5 + 0.5))
            meta["sl"] = close - self.atr_sl_multiplier * atr
            meta["tp"] = close + self.atr_tp_multiplier * atr

        elif close >= bb_upper and rsi > self.rsi_overbought:
            action = "SELL"
            reason = "price_at_upper_band_rsi_overbought"
            distance = bb_upper - bb_mid
            strength = float(min(1.0, (rsi / self.rsi_overbought - 1.0) * 0.5 + 0.5))
            meta["sl"] = close + self.atr_sl_multiplier * atr
            meta["tp"] = close - self.atr_tp_multiplier * atr

        ts = last["time"] if "time" in ind.columns else datetime.now(tz=timezone.utc)
        if isinstance(ts, pd.Timestamp):
            ts = ts.to_pydatetime()

        return Signal(action=action, strength=strength, reason=reason, timestamp=ts, meta=meta)
