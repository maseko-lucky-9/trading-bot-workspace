"""
EMA Crossover strategy (US-004).

Pure pandas implementation — no TA-Lib. ATR uses Wilder smoothing.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from core.strategy.base import Signal, Strategy
from core.strategy.indicators import atr as _atr


class EMACrossover(Strategy):
    """9/21 EMA crossover with ATR-derived SL/TP.

    BUY when fast EMA crosses above slow on the latest *closed* bar.
    SELL when fast EMA crosses below slow on the latest closed bar.
    """

    name = "ema_crossover"

    def __init__(
        self,
        fast: int = 9,
        slow: int = 21,
        atr_period: int = 14,
        atr_sl_multiplier: float = 1.5,
        atr_tp_multiplier: float = 3.0,
    ) -> None:
        if fast >= slow:
            raise ValueError("fast period must be < slow period")
        self.fast = fast
        self.slow = slow
        self.atr_period = atr_period
        self.atr_sl_multiplier = atr_sl_multiplier
        self.atr_tp_multiplier = atr_tp_multiplier

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["ema_fast"] = out["close"].ewm(span=self.fast, adjust=False).mean()
        out["ema_slow"] = out["close"].ewm(span=self.slow, adjust=False).mean()
        out["atr"] = _atr(out, self.atr_period)
        return out

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        if len(df) < max(self.slow, self.atr_period) + 2:
            return Signal(action="HOLD", strength=0.0, reason="insufficient_bars")

        ind = self.compute_indicators(df)
        last = ind.iloc[-1]
        prev = ind.iloc[-2]
        atr = float(last["atr"]) if not np.isnan(last["atr"]) else 0.0

        prev_diff = prev["ema_fast"] - prev["ema_slow"]
        last_diff = last["ema_fast"] - last["ema_slow"]
        close = float(last["close"])

        action = "HOLD"
        reason = "no_cross"
        strength = 0.0

        if prev_diff <= 0 < last_diff:
            action = "BUY"
            reason = "fast_ema_crossed_above_slow"
        elif prev_diff >= 0 > last_diff:
            action = "SELL"
            reason = "fast_ema_crossed_below_slow"

        if action != "HOLD":
            sep = abs(last_diff) / max(abs(close), 1e-9)
            strength = float(min(1.0, sep * 1000.0))  # 1bp -> 0.1

        meta: dict = {
            "ema_fast": float(last["ema_fast"]),
            "ema_slow": float(last["ema_slow"]),
            "atr": atr,
            "entry_price": close,
        }
        if action == "BUY":
            meta["sl"] = close - self.atr_sl_multiplier * atr
            meta["tp"] = close + self.atr_tp_multiplier * atr
        elif action == "SELL":
            meta["sl"] = close + self.atr_sl_multiplier * atr
            meta["tp"] = close - self.atr_tp_multiplier * atr

        ts = last["time"] if "time" in ind.columns else datetime.now(tz=timezone.utc)
        if isinstance(ts, pd.Timestamp):
            ts = ts.to_pydatetime()
        return Signal(action=action, strength=strength, reason=reason, timestamp=ts, meta=meta)
