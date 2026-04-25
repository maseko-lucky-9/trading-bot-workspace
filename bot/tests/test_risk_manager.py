"""Unit tests for RiskManager (US-005)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.risk.manager import RiskManager


def _make_df(seed: int = 0, n: int = 100, vol: float = 0.001) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    prices = 1.10 + np.cumsum(rng.normal(0, vol, n))
    return pd.DataFrame(
        {
            "high": prices + 0.001,
            "low": prices - 0.001,
            "close": prices,
            "open": prices,
            "volume": np.full(n, 1000),
        }
    )


def test_size_position_returns_positive_lots():
    rm = RiskManager({"risk": {"max_risk_per_trade": 0.01, "atr_multiplier": 1.5}})
    df = _make_df()
    lots = rm.size_position("EURUSD", None, {"balance": 10_000.0}, df)
    assert lots > 0
    assert lots <= rm.max_lots


def test_size_position_zero_balance():
    rm = RiskManager({})
    lots = rm.size_position("EURUSD", None, {"balance": 0.0}, _make_df())
    assert lots == 0.0


def test_breakers_halt_on_daily_loss():
    rm = RiskManager({"risk": {"daily_loss_limit": 0.02}})
    ok, reason = rm.check_circuit_breakers(
        {"balance": 9_700.0, "equity": 9_700.0},
        positions=[],
        recent_closed=[],
        peak_equity=10_000.0,
        day_start_equity=10_000.0,
    )
    assert ok is False
    assert "daily_loss" in reason


def test_breakers_consecutive_losses():
    rm = RiskManager({"risk": {"consecutive_loss_halt": 3}})
    closed = [{"profit": -1.0}, {"profit": -2.0}, {"profit": -3.0}]
    ok, reason = rm.check_circuit_breakers({"balance": 10_000, "equity": 10_000}, [], closed, 10_000, 10_000)
    assert ok is False
    assert "consecutive" in reason


def test_validate_order_requires_sl():
    rm = RiskManager({})
    ok, reason = rm.validate_order({"sl": 0, "volume": 0.1})
    assert ok is False
    assert "sl" in reason
    ok, _ = rm.validate_order({"sl": 1.16, "volume": 0.1})
    assert ok is True


def test_position_adjustment_thresholds():
    rm = RiskManager({})
    assert rm.get_position_adjustment(10_000, 10_000) == 1.0
    assert rm.get_position_adjustment(8_900, 10_000) == 0.7   # 11% DD
    assert rm.get_position_adjustment(8_400, 10_000) == 0.5   # 16% DD
    assert rm.get_position_adjustment(7_500, 10_000) == 0.0   # 25% DD


# ------------------------------------------------------------------ #
# Uncovered branch coverage                                          #
# ------------------------------------------------------------------ #

def test_size_position_atr_zero_returns_lot_step():
    """Constant prices → ATR=0 → LOT_STEP fallback."""
    rm = RiskManager({})
    const = np.full(20, 1.10)
    df = pd.DataFrame({
        "high": const, "low": const, "close": const,
        "open": const, "volume": np.ones(20),
    })
    assert rm.size_position("EURUSD", None, {"balance": 10_000.0}, df) == pytest.approx(0.01)


def test_size_position_atr_mult_zero_returns_lot_step():
    """atr_multiplier=0 → sl_distance_pips=0 → LOT_STEP fallback."""
    rm = RiskManager({"risk": {"atr_multiplier": 0}})
    assert rm.size_position("EURUSD", None, {"balance": 10_000.0}, _make_df()) == pytest.approx(0.01)


def test_breakers_halts_on_trailing_dd():
    """Equity 20%+ below peak triggers trailing_dd_halt."""
    rm = RiskManager({"risk": {"trailing_dd_halt": 0.10}})
    ok, reason = rm.check_circuit_breakers(
        {"equity": 8_000.0}, peak_equity=10_000.0, day_start_equity=10_000.0
    )
    assert ok is False
    assert "trailing_dd_halt" in reason


def test_breakers_returns_ok_when_all_clear():
    rm = RiskManager({})
    ok, reason = rm.check_circuit_breakers(
        {"equity": 10_000.0, "balance": 10_000.0}, [], [], 10_000.0, 10_000.0
    )
    assert ok is True
    assert reason == "ok"


def test_position_adjustment_returns_one_on_zero_peak():
    """peak_equity=0 → no adjustment possible → 1.0."""
    rm = RiskManager({})
    assert rm.get_position_adjustment(5_000.0, 0.0) == pytest.approx(1.0)


def test_validate_order_rejects_zero_volume():
    rm = RiskManager({})
    ok, reason = rm.validate_order({"sl": 1.10, "volume": 0.0})
    assert ok is False
    assert "volume" in reason


def test_validate_order_rejects_excess_volume():
    rm = RiskManager({"risk": {"max_lots": 1.0}})
    ok, reason = rm.validate_order({"sl": 1.10, "volume": 2.0})
    assert ok is False
    assert "volume" in reason
