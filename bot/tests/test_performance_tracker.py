"""Unit tests for PerformanceTracker (US-006)."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

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


# ------------------------------------------------------------------ #
# Persistence                                                        #
# ------------------------------------------------------------------ #

def test_save_creates_file(tmp_path):
    pt = PerformanceTracker()
    for i, p in enumerate([10, -5, 8]):
        pt.record_trade(_trade(p, i))
    path = tmp_path / "tracker.json"
    pt.save(path)
    assert path.exists()


def test_load_round_trip(tmp_path):
    pt = PerformanceTracker()
    for i, p in enumerate([10, -5, 8, -3, 12]):
        pt.record_trade(_trade(p, i))
    path = tmp_path / "tracker.json"
    pt.save(path)

    pt2 = PerformanceTracker()
    pt2.load(path)
    assert pt2.trade_count == 5
    assert pt2.win_rate() == pt.win_rate()
    assert pt2.sharpe() == pytest.approx(pt.sharpe())


def test_load_restores_profit_values(tmp_path):
    profits = [7.5, -2.0, 11.0]
    pt = PerformanceTracker()
    for i, p in enumerate(profits):
        pt.record_trade(_trade(p, i))
    path = tmp_path / "tracker.json"
    pt.save(path)

    pt2 = PerformanceTracker()
    pt2.load(path)
    loaded_profits = [float(t["profit"]) for t in pt2.trades]
    assert loaded_profits == pytest.approx(profits)


def test_load_merges_appends_existing(tmp_path):
    pt = PerformanceTracker()
    for i, p in enumerate([5, -2, 8]):
        pt.record_trade(_trade(p, i))
    path = tmp_path / "tracker.json"
    pt.save(path)

    pt2 = PerformanceTracker()
    for i, p in enumerate([3, -1]):
        pt2.record_trade(_trade(p, i + 10))
    pt2.load(path)
    # load() replaces trades from file
    assert pt2.trade_count == 3


# ------------------------------------------------------------------ #
# to_dict                                                            #
# ------------------------------------------------------------------ #

def test_to_dict_contains_trades_and_summary():
    pt = PerformanceTracker()
    for i, p in enumerate([10, -5, 8, -3, 12]):
        pt.record_trade(_trade(p, i))
    d = pt.to_dict()
    assert "trades" in d
    assert "summary" in d
    assert len(d["trades"]) == 5
    assert "sharpe" in d["summary"]


def test_to_dict_summary_matches_direct_calls():
    pt = PerformanceTracker()
    for i, p in enumerate([10, -5, 8, -3, 12]):
        pt.record_trade(_trade(p, i))
    d = pt.to_dict()
    assert d["summary"]["win_rate"] == pytest.approx(pt.win_rate())
    assert d["summary"]["max_drawdown"] == pytest.approx(pt.max_drawdown())
    assert d["summary"]["sharpe"] == pytest.approx(pt.sharpe())


# ------------------------------------------------------------------ #
# max_drawdown edge cases                                            #
# ------------------------------------------------------------------ #

def test_max_drawdown_zero_when_monotone_profit():
    pt = PerformanceTracker()
    for i, p in enumerate([5, 10, 15, 20]):
        pt.record_trade(_trade(p, i))
    assert pt.max_drawdown() == pytest.approx(0.0)


def test_max_drawdown_positive_after_loss():
    pt = PerformanceTracker()
    for i, p in enumerate([20, 30, -100, 5]):
        pt.record_trade(_trade(p, i))
    assert pt.max_drawdown() > 0


def test_profit_factor_inf_when_no_losses():
    pt = PerformanceTracker()
    for i, p in enumerate([5, 10, 15, 20]):
        pt.record_trade(_trade(p, i))
    assert pt.profit_factor() == float("inf")


# ------------------------------------------------------------------ #
# _parse_dt edge cases                                               #
# ------------------------------------------------------------------ #

def test_parse_dt_none_and_empty_return_none():
    from core.performance.tracker import _parse_dt
    assert _parse_dt(None) is None
    assert _parse_dt("") is None


def test_parse_dt_datetime_passthrough():
    from core.performance.tracker import _parse_dt
    dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert _parse_dt(dt) is dt


def test_parse_dt_invalid_string_returns_none():
    from core.performance.tracker import _parse_dt
    assert _parse_dt("not-a-date") is None


def test_daily_returns_empty_when_no_trades():
    assert PerformanceTracker()._daily_returns().size == 0


def test_daily_returns_skips_trades_with_no_timestamp():
    pt = PerformanceTracker()
    pt.record_trade({"profit": 5.0})  # no close_time or open_time → skipped
    assert pt._daily_returns().size == 0


def test_sharpe_falls_back_to_per_trade_when_single_day():
    """All trades same day → 1 daily bucket → per-trade fallback, finite result."""
    pt = PerformanceTracker()
    for p in [5.0, 10.0, -3.0, 7.0]:
        pt.record_trade({"profit": p, "close_time": "2026-01-01T10:00:00+00:00"})
    assert math.isfinite(pt.sharpe())


def test_sharpe_returns_zero_when_all_same_daily_profit():
    """Identical daily profits → std=0 → sharpe=0."""
    pt = PerformanceTracker()
    for i in range(5):
        pt.record_trade({"profit": 10.0, "close_time": f"2026-01-0{i + 1}T10:00:00+00:00"})
    assert pt.sharpe() == pytest.approx(0.0)
