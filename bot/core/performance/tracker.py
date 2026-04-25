"""
PerformanceTracker (US-006).

Computes Sharpe (annualised), max drawdown, win rate and profit factor
from a list of closed trades. Returns 0.0 (not NaN) when sample is too
small so downstream code can compare against guard thresholds without
None-checking.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


def _parse_dt(x) -> datetime | None:
    if x is None or x == "":
        return None
    if isinstance(x, datetime):
        return x
    try:
        return datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    except Exception:
        return None


class PerformanceTracker:
    def __init__(self, annualization: int = 252) -> None:
        self.annualization = annualization
        self.trades: list[dict] = []

    # ------------------------------------------------------------------ #
    # Recording                                                          #
    # ------------------------------------------------------------------ #

    def record_trade(self, trade: dict) -> None:
        self.trades.append(dict(trade))

    @property
    def trade_count(self) -> int:
        return len(self.trades)

    # ------------------------------------------------------------------ #
    # Metrics                                                            #
    # ------------------------------------------------------------------ #

    def _profits(self) -> np.ndarray:
        return np.array([float(t.get("profit", 0.0)) for t in self.trades], dtype=float)

    def _daily_returns(self) -> np.ndarray:
        """Aggregate trade profits by close-day. Returns USD-deltas; for
        Sharpe we treat them as returns (units cancel in mean/std ratio).
        """
        if not self.trades:
            return np.array([])
        rows = []
        for t in self.trades:
            ct = _parse_dt(t.get("close_time")) or _parse_dt(t.get("open_time"))
            if ct is None:
                continue
            rows.append((ct.date(), float(t.get("profit", 0.0))))
        if not rows:
            return np.array([])
        df = pd.DataFrame(rows, columns=["date", "profit"])
        return df.groupby("date")["profit"].sum().to_numpy()

    def sharpe(self) -> float:
        if self.trade_count < 2:
            return 0.0
        daily = self._daily_returns()
        if daily.size < 2:
            # Fall back to per-trade returns
            daily = self._profits()
        std = float(np.std(daily, ddof=1))
        if std == 0 or math.isnan(std):
            return 0.0
        return float(np.mean(daily) / std * math.sqrt(self.annualization))

    def max_drawdown(self) -> float:
        if self.trade_count < 2:
            return 0.0
        equity = np.cumsum(self._profits())
        peak = np.maximum.accumulate(equity)
        # express as fraction of peak when peak positive, else absolute relative to running max abs
        with np.errstate(divide="ignore", invalid="ignore"):
            base = np.where(np.abs(peak) > 1e-9, np.abs(peak), 1.0)
            dd = (peak - equity) / base
        return float(max(0.0, dd.max()))

    def win_rate(self) -> float:
        if self.trade_count < 2:
            return 0.0
        profits = self._profits()
        wins = int((profits > 0).sum())
        return wins / len(profits)

    def profit_factor(self) -> float:
        if self.trade_count < 2:
            return 0.0
        profits = self._profits()
        gross_profit = float(profits[profits > 0].sum())
        gross_loss = float(-profits[profits < 0].sum())
        if gross_loss == 0:
            return 0.0 if gross_profit == 0 else float("inf")
        return gross_profit / gross_loss

    def summary(self) -> dict:
        return {
            "trade_count": self.trade_count,
            "sharpe": self.sharpe(),
            "max_drawdown": self.max_drawdown(),
            "win_rate": self.win_rate(),
            "profit_factor": self.profit_factor(),
        }

    # ------------------------------------------------------------------ #
    # Persistence                                                        #
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict:
        return {"trades": self.trades, "summary": self.summary()}

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), default=str, indent=2))

    def load(self, path: Path) -> None:
        data = json.loads(Path(path).read_text())
        self.trades = list(data.get("trades", []))
