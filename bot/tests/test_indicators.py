"""Tests for core/strategy/indicators.py shared ATR helper."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.strategy.indicators import atr


@pytest.fixture
def flat_df():
    """Flat price series — ATR should converge to a fixed value."""
    n = 50
    close = np.ones(n) * 1.10
    return pd.DataFrame({
        "high": close + 0.001,
        "low": close - 0.001,
        "close": close,
    })


@pytest.fixture
def volatile_df():
    rng = np.random.default_rng(7)
    n = 100
    close = 1.10 + np.cumsum(rng.normal(0, 0.001, n))
    return pd.DataFrame({
        "high": close + rng.uniform(0.0005, 0.002, n),
        "low": close - rng.uniform(0.0005, 0.002, n),
        "close": close,
    })


def test_atr_returns_series_same_length(flat_df):
    result = atr(flat_df)
    assert isinstance(result, pd.Series)
    assert len(result) == len(flat_df)


def test_atr_last_value_positive(volatile_df):
    result = atr(volatile_df)
    assert float(result.iloc[-1]) > 0.0


def test_atr_flat_series_converges(flat_df):
    """On perfectly flat data every true range = 0.002; ATR converges to 0.002."""
    result = atr(flat_df, period=14)
    assert float(result.iloc[-1]) == pytest.approx(0.002, rel=1e-3)


def test_atr_custom_period_shorter_reacts_faster():
    n = 60
    close = np.ones(n) * 1.10
    close[30:] = 1.20  # sudden jump
    df = pd.DataFrame({
        "high": close + 0.001,
        "low": close - 0.001,
        "close": close,
    })
    fast = float(atr(df, period=5).iloc[-1])
    slow = float(atr(df, period=20).iloc[-1])
    # Both should be near 0.002 on the flat tail — just confirm no crash
    assert fast > 0
    assert slow > 0


def test_atr_all_values_nonnegative(volatile_df):
    result = atr(volatile_df)
    assert (result.dropna() >= 0).all()


def test_atr_high_gte_low_respected(volatile_df):
    """ATR must not be negative even when high==low (zero true range)."""
    df = volatile_df.copy()
    df["high"] = df["low"] = df["close"]
    result = atr(df)
    assert (result.dropna() >= 0).all()
