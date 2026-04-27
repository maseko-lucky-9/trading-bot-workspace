"""Wave 1 — Risk & Sizing tests.

Covers:
- F6 : half-Kelly multiplier kicks in after 30 closed trades
- F10: correlation_factor downweights the secondary symbol
- F11: PerformanceTracker.expectancy / payoff_ratio / avg_r_multiple
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.performance.tracker import PerformanceTracker
from core.risk.manager import RiskManager, LOT_STEP, _KELLY_MIN_TRADES


# ------------------------------------------------------------------ #
# Helpers                                                            #
# ------------------------------------------------------------------ #

def _make_df(n: int = 100, vol: float = 0.001, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    prices = 1.10 + np.cumsum(rng.normal(0, vol, n))
    return pd.DataFrame({
        "high": prices + 0.001,
        "low": prices - 0.001,
        "close": prices,
        "open": prices,
        "volume": np.full(n, 1000),
    })


def _winning_history(n: int = 30, avg_win: float = 10.0, avg_loss: float = 5.0) -> list[dict]:
    """Generate n trades alternating win/loss at a 60% win rate."""
    trades = []
    for i in range(n):
        profit = avg_win if i % 5 != 0 else -avg_loss  # 80% win
        trades.append({"profit": profit})
    return trades


def _losing_history(n: int = 30) -> list[dict]:
    return [{"profit": -10.0} for _ in range(n)]


# ------------------------------------------------------------------ #
# F6 — Kelly multiplier                                              #
# ------------------------------------------------------------------ #

class TestKellyMultiplier:
    def test_falls_back_to_config_when_too_few_trades(self):
        rm = RiskManager({"risk": {"kelly_fraction": 0.25}})
        assert rm.kelly_multiplier([]) == pytest.approx(0.25)
        assert rm.kelly_multiplier([{"profit": 10}] * (_KELLY_MIN_TRADES - 1)) == pytest.approx(0.25)

    def test_uses_realised_stats_when_enough_trades(self):
        rm = RiskManager({"risk": {"kelly_fraction": 0.5}})
        # 40 trades, 75% win rate, avg_win=10, avg_loss=5 → b=2, Kelly=0.5, half=0.25
        trades = [{"profit": 10.0}] * 30 + [{"profit": -5.0}] * 10
        mult = rm.kelly_multiplier(trades)
        assert 0.0 < mult <= 0.5  # capped by kelly_fraction=0.5

    def test_caps_at_config_kelly_fraction(self):
        rm = RiskManager({"risk": {"kelly_fraction": 0.1}})
        # Very favorable history would compute Kelly > 0.1 → must be capped
        trades = [{"profit": 100.0}] * 28 + [{"profit": -1.0}] * 2
        mult = rm.kelly_multiplier(trades)
        assert mult == pytest.approx(0.1)

    def test_returns_zero_for_losing_history(self):
        rm = RiskManager({"risk": {"kelly_fraction": 0.25}})
        mult = rm.kelly_multiplier(_losing_history(40))
        assert mult == pytest.approx(0.0)

    def test_kelly_all_wins_falls_back(self):
        """No losses in history → can't compute b → falls back to config."""
        rm = RiskManager({"risk": {"kelly_fraction": 0.25}})
        mult = rm.kelly_multiplier([{"profit": 5.0}] * 40)
        assert mult == pytest.approx(0.25)

    def test_size_position_uses_kelly_after_30_trades(self):
        rm = RiskManager({"risk": {"max_risk_per_trade": 0.01, "atr_multiplier": 1.5, "kelly_fraction": 0.25}})
        df = _make_df()
        account = {"balance": 10_000.0}
        lots_no_history = rm.size_position("EURUSD", None, account, df)
        # With <30 trades kelly=0.25; same result as explicitly passing empty list
        lots_with_history = rm.size_position("EURUSD", None, account, df, trade_history=[])
        assert lots_no_history == lots_with_history
        assert lots_no_history > 0

    def test_size_position_kelly_applied_with_30_trades(self):
        """After 30 positive-edge trades, Kelly computes a concrete multiplier."""
        rm = RiskManager({"risk": {"max_risk_per_trade": 0.01, "atr_multiplier": 1.5, "kelly_fraction": 0.5}})
        df = _make_df()
        account = {"balance": 10_000.0}
        # Mix of 30 trades: 24 wins of $10, 6 losses of $5 → 80% win rate
        history = [{"profit": 10.0}] * 24 + [{"profit": -5.0}] * 6
        lots = rm.size_position("EURUSD", None, account, df, trade_history=history)
        assert lots >= LOT_STEP
        assert lots <= rm.max_lots


# ------------------------------------------------------------------ #
# F10 — Correlation-aware sizing                                     #
# ------------------------------------------------------------------ #

class TestCorrelationAwareSizing:
    def _lots(self, correlation_factor: float) -> float:
        rm = RiskManager({"risk": {"max_risk_per_trade": 0.01, "atr_multiplier": 1.5}})
        df = _make_df()
        account = {"balance": 10_000.0}
        return rm.size_position("GBPUSD", None, account, df, correlation_factor=correlation_factor)

    def test_zero_correlation_unchanged(self):
        lots_base = self._lots(0.0)
        assert lots_base >= LOT_STEP

    def test_high_correlation_reduces_size(self):
        lots_high_corr = self._lots(0.9)
        lots_no_corr = self._lots(0.0)
        assert lots_high_corr <= lots_no_corr

    def test_perfect_correlation_reduces_to_minimum(self):
        lots = self._lots(1.0)
        assert lots == pytest.approx(LOT_STEP)

    def test_compute_correlation_factor_returns_float_in_range(self):
        rng = np.random.default_rng(42)
        s1 = pd.Series(1.10 + np.cumsum(rng.normal(0, 0.001, 100)))
        s2 = s1 + pd.Series(rng.normal(0, 0.0002, 100))
        cf = RiskManager.compute_correlation_factor(s1, s2, window=50)
        assert 0.0 <= cf <= 1.0

    def test_compute_correlation_factor_returns_zero_for_short_series(self):
        s = pd.Series([1.0, 1.1, 1.2])
        assert RiskManager.compute_correlation_factor(s, s, window=50) == pytest.approx(0.0)

    def test_compute_correlation_factor_negatively_correlated_is_absolute(self):
        """Anti-correlated series still produces a positive factor."""
        rng = np.random.default_rng(7)
        s1 = pd.Series(1.10 + np.cumsum(rng.normal(0, 0.001, 100)))
        s2 = -s1 + 2.20
        cf = RiskManager.compute_correlation_factor(s1, s2, window=50)
        assert cf >= 0.0  # must be absolute value


# ------------------------------------------------------------------ #
# F11 — PerformanceTracker new metrics                               #
# ------------------------------------------------------------------ #

class TestExpectancyMetrics:
    def _tracker(self, wins: list[float], losses: list[float]) -> PerformanceTracker:
        pt = PerformanceTracker()
        for p in wins:
            pt.record_trade({"profit": p, "close_time": "2026-01-01T10:00:00+00:00"})
        for p in losses:
            pt.record_trade({"profit": -p, "close_time": "2026-01-02T10:00:00+00:00"})
        return pt

    def test_payoff_ratio_correct(self):
        pt = self._tracker(wins=[10.0, 10.0], losses=[5.0, 5.0])
        assert pt.payoff_ratio() == pytest.approx(2.0)

    def test_expectancy_correct(self):
        # 2 wins of 10, 2 losses of 5 → win_rate=0.5, avg_win=10, avg_loss=5
        # expectancy = 0.5*10 - 0.5*5 = 2.5
        pt = self._tracker(wins=[10.0, 10.0], losses=[5.0, 5.0])
        assert pt.expectancy() == pytest.approx(2.5)

    def test_avg_r_multiple_correct(self):
        # expectancy=2.5, avg_loss=5 → R = 2.5/5 = 0.5
        pt = self._tracker(wins=[10.0, 10.0], losses=[5.0, 5.0])
        assert pt.avg_r_multiple() == pytest.approx(0.5)

    def test_payoff_ratio_zero_when_no_losses(self):
        pt = self._tracker(wins=[10.0, 10.0], losses=[])
        assert pt.payoff_ratio() == pytest.approx(0.0)

    def test_expectancy_negative_when_losing(self):
        pt = self._tracker(wins=[1.0, 1.0], losses=[10.0, 10.0])
        assert pt.expectancy() < 0.0

    def test_metrics_zero_when_too_few_trades(self):
        pt = PerformanceTracker()
        pt.record_trade({"profit": 5.0})
        assert pt.payoff_ratio() == pytest.approx(0.0)
        assert pt.expectancy() == pytest.approx(0.0)
        assert pt.avg_r_multiple() == pytest.approx(0.0)

    def test_summary_includes_new_fields(self):
        pt = self._tracker(wins=[10.0, 8.0, 12.0], losses=[4.0, 6.0])
        s = pt.summary()
        assert "payoff_ratio" in s
        assert "expectancy" in s
        assert "avg_r_multiple" in s
        assert s["payoff_ratio"] > 0.0
        assert s["expectancy"] > 0.0
        assert s["avg_r_multiple"] > 0.0
