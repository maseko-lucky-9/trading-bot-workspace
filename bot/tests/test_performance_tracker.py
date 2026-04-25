"""Unit tests for PerformanceTracker (US-006)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.performance.tracker import PerformanceTracker


def _trade(profit: float, day: int) -> dict:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return {
        "profit": profit,
        "open_time": (base + timedelta(days=day)).isoformat(),
        "close_time": (base + timedelta(days=day, hours=1)).isoformat(),
        "type": "BUY",
    }


def test_metrics_zero_when_too_few_trades():
    pt = PerformanceTracker()
    pt.record_trade(_trade(10, 0))
    s = pt.summary()
    assert s["sharpe"] == 0.0
    assert s["max_drawdown"] == 0.0
    assert s["win_rate"] == 0.0


def test_win_rate_and_profit_factor():
    pt = PerformanceTracker()
    profits = [10, -5, 8, -3, 12]
    for i, p in enumerate(profits):
        pt.record_trade(_trade(p, i))
    s = pt.summary()
    assert 0.0 < s["win_rate"] <= 1.0
    assert s["profit_factor"] > 0


def test_drawdown_non_negative():
    pt = PerformanceTracker()
    profits = [10, 20, -50, 5, -10]
    for i, p in enumerate(profits):
        pt.record_trade(_trade(p, i))
    assert pt.max_drawdown() >= 0


def test_sharpe_finite_with_mixed_returns():
    pt = PerformanceTracker()
    for i, p in enumerate([10, -5, 8, -3, 12, 4, -7, 9, -2, 11]):
        pt.record_trade(_trade(p, i))
    sharpe = pt.sharpe()
    assert sharpe == sharpe  # not NaN
    assert sharpe > 0
