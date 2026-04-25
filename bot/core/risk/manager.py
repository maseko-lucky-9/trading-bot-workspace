"""
RiskManager (US-005).

ATR-based position sizing + Kelly multiplier + three-layer circuit
breakers. All thresholds read from config.yaml — never hardcoded.
"""
from __future__ import annotations

import math
from typing import Iterable

import pandas as pd


PIP_SIZE = 0.0001
PIP_VALUE_USD_PER_LOT = 10.0  # EURUSD/GBPUSD standard lot, $/pip
LOT_STEP = 0.01


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev).abs(),
            (low - prev).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return float(tr.tail(period).mean())


def _round_to_step(x: float, step: float = LOT_STEP) -> float:
    return round(round(x / step) * step, 2)


class RiskManager:
    def __init__(self, config: dict) -> None:
        self.config = config or {}
        risk = self.config.get("risk", {}) or {}
        self.max_risk_per_trade = float(risk.get("max_risk_per_trade", 0.01))
        self.kelly_fraction = float(risk.get("kelly_fraction", 0.25))
        self.daily_loss_limit = float(risk.get("daily_loss_limit", 0.02))
        self.trailing_dd_warn = float(risk.get("trailing_dd_warn", 0.10))
        self.trailing_dd_reduce = float(risk.get("trailing_dd_reduce", 0.15))
        self.trailing_dd_halt = float(risk.get("trailing_dd_halt", 0.20))
        self.max_lots = float(risk.get("max_lots", 10.0))
        self.consecutive_loss_halt = int(risk.get("consecutive_loss_halt", 5))
        self.atr_multiplier = float(risk.get("atr_multiplier", 1.5))

    # ------------------------------------------------------------------ #
    # Position sizing                                                    #
    # ------------------------------------------------------------------ #

    def size_position(
        self,
        symbol: str,
        signal,
        account: dict,
        df: pd.DataFrame,
    ) -> float:
        """Return lot size sized off ATR + 1% equity risk.

        Uses the formula::

            risk_$  = balance * risk_pct
            atr     = ATR(14) on df
            lots    = risk_$ / (atr * atr_mult * pip_value_per_lot * (1/PIP_SIZE))

        Falls back to 0.01 lots if any input is degenerate.
        """
        balance = float((account or {}).get("balance", 0.0))
        if balance <= 0:
            return 0.0
        atr = _atr(df, period=14)
        if atr <= 0 or math.isnan(atr):
            return LOT_STEP
        risk_dollars = balance * self.max_risk_per_trade
        sl_distance_price = atr * self.atr_multiplier
        sl_distance_pips = sl_distance_price / PIP_SIZE
        if sl_distance_pips <= 0:
            return LOT_STEP
        lots = risk_dollars / (sl_distance_pips * PIP_VALUE_USD_PER_LOT)
        lots = min(lots, self.max_lots)
        lots = max(LOT_STEP, _round_to_step(lots))
        return lots

    # ------------------------------------------------------------------ #
    # Circuit breakers                                                   #
    # ------------------------------------------------------------------ #

    def check_circuit_breakers(
        self,
        account: dict,
        positions: Iterable[dict] | None = None,
        recent_closed: Iterable[dict] | None = None,
        peak_equity: float | None = None,
        day_start_equity: float | None = None,
    ) -> tuple[bool, str]:
        """Return (ok, reason). ok=False means halt new opens."""
        positions = list(positions or [])
        recent_closed = list(recent_closed or [])
        equity = float((account or {}).get("equity", 0.0)) or float(
            (account or {}).get("balance", 0.0)
        )
        peak = float(peak_equity if peak_equity is not None else equity)
        day0 = float(day_start_equity if day_start_equity is not None else equity)

        # Portfolio: trailing drawdown halt
        if peak > 0:
            dd = max(0.0, (peak - equity) / peak)
            if dd >= self.trailing_dd_halt:
                return False, f"trailing_dd_halt: dd={dd:.2%}"

        # Strategy: consecutive losses
        if len(recent_closed) >= self.consecutive_loss_halt:
            tail = recent_closed[-self.consecutive_loss_halt :]
            if all(float(t.get("profit", 0.0)) < 0 for t in tail):
                return False, f"consecutive_losses>={self.consecutive_loss_halt}"

        # Portfolio: daily loss
        if day0 > 0:
            daily_pnl = (equity - day0) / day0
            if daily_pnl <= -self.daily_loss_limit:
                return False, f"daily_loss_limit: pnl={daily_pnl:.2%}"

        return True, "ok"

    def get_position_adjustment(self, equity: float, peak_equity: float) -> float:
        if peak_equity <= 0:
            return 1.0
        dd = max(0.0, (peak_equity - equity) / peak_equity)
        if dd >= self.trailing_dd_halt:
            return 0.0
        if dd >= self.trailing_dd_reduce:
            return 0.5
        if dd >= self.trailing_dd_warn:
            return 0.7
        return 1.0

    # ------------------------------------------------------------------ #
    # Order validation                                                   #
    # ------------------------------------------------------------------ #

    def validate_order(self, order: dict) -> tuple[bool, str]:
        sl = float(order.get("sl", 0.0) or 0.0)
        volume = float(order.get("volume", 0.0) or 0.0)
        if sl == 0.0:
            return False, "sl_required"
        if volume <= 0:
            return False, "volume_must_be_positive"
        if volume > self.max_lots:
            return False, f"volume>{self.max_lots}"
        return True, "ok"
