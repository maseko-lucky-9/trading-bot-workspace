"""Tests for core.strategy.structure (FX GOAT swing / trend / BoS helpers)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.strategy.structure import (
    classify_trend,
    detect_swings,
    last_break_of_structure,
)


def _frame_from_highs_lows(highs: list[float], lows: list[float]) -> pd.DataFrame:
    """Build an OHLC frame where open=close=mean(high,low). Time is sequential."""
    n = len(highs)
    closes = [(h + l) / 2.0 for h, l in zip(highs, lows)]
    return pd.DataFrame(
        {
            "time": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [100] * n,
        }
    )


# --------------------------------------------------------------------------- #
# detect_swings                                                               #
# --------------------------------------------------------------------------- #


def test_detect_swings_marks_clear_fractal_high():
    # Index 4 has the highest high with 2 lower highs each side.
    highs = [1.0, 1.05, 1.10, 1.05, 1.20, 1.05, 1.10, 1.05, 1.0]
    lows = [0.9, 0.95, 1.00, 0.95, 1.10, 0.95, 1.00, 0.95, 0.9]
    df = _frame_from_highs_lows(highs, lows)
    out = detect_swings(df, left=2, right=2)
    assert bool(out.loc[4, "swing_high"]) is True
    # Bars too close to the edges cannot be swings.
    assert bool(out.loc[0, "swing_high"]) is False
    assert bool(out.loc[8, "swing_high"]) is False


def test_detect_swings_marks_clear_fractal_low():
    lows = [1.10, 1.05, 1.00, 1.05, 0.90, 1.05, 1.00, 1.05, 1.10]
    highs = [1.20, 1.15, 1.10, 1.15, 1.00, 1.15, 1.10, 1.15, 1.20]
    df = _frame_from_highs_lows(highs, lows)
    out = detect_swings(df, left=2, right=2)
    assert bool(out.loc[4, "swing_low"]) is True


def test_detect_swings_returns_all_false_when_too_short():
    df = _frame_from_highs_lows([1.0, 1.1], [0.9, 1.0])
    out = detect_swings(df)
    assert not out["swing_high"].any()
    assert not out["swing_low"].any()


def test_detect_swings_rejects_invalid_window():
    df = _frame_from_highs_lows([1.0, 1.1, 1.0], [0.9, 1.0, 0.9])
    with pytest.raises(ValueError):
        detect_swings(df, left=0, right=2)


# --------------------------------------------------------------------------- #
# classify_trend                                                              #
# --------------------------------------------------------------------------- #


def test_classify_trend_uptrend():
    # Two ascending swing highs and two ascending swing lows.
    # Layout: SL1=idx2 (low 0.95), SH1=idx5 (high 1.10), SL2=idx9 (low 1.00),
    #         SH2=idx12 (high 1.20).
    highs = [1.00, 1.05, 1.00, 1.05, 1.05, 1.10, 1.05, 1.05, 1.05, 1.10, 1.15, 1.15, 1.20, 1.15, 1.10]
    lows = [0.99, 0.97, 0.95, 0.97, 1.00, 1.05, 1.00, 1.02, 1.02, 1.00, 1.05, 1.05, 1.10, 1.05, 1.00]
    df = _frame_from_highs_lows(highs, lows)
    swings = detect_swings(df, left=2, right=2)
    assert classify_trend(swings) == "uptrend"


def test_classify_trend_downtrend():
    # Two descending swing highs (idx 2 = 1.20, idx 8 = 1.17 — strict less)
    # and two descending swing lows (idx 5 = 1.00, idx 12 = 0.95).
    highs = [1.15, 1.15, 1.20, 1.15, 1.15, 1.10, 1.15, 1.15, 1.17, 1.10, 1.05, 1.05, 1.00, 1.05, 1.10]
    lows = [1.10, 1.12, 1.15, 1.12, 1.05, 1.00, 1.05, 1.03, 1.03, 1.05, 1.00, 1.00, 0.95, 1.00, 1.05]
    df = _frame_from_highs_lows(highs, lows)
    swings = detect_swings(df, left=2, right=2)
    assert classify_trend(swings) == "downtrend"


def test_classify_trend_range_when_insufficient_swings():
    highs = [1.0, 1.05, 1.10, 1.05, 1.0]
    lows = [0.95, 1.00, 1.05, 1.00, 0.95]
    df = _frame_from_highs_lows(highs, lows)
    swings = detect_swings(df, left=2, right=2)
    # Only one swing high and zero confirmed swing lows -> range.
    assert classify_trend(swings) == "range"


def test_classify_trend_rejects_unprepared_frame():
    df = _frame_from_highs_lows([1.0, 1.1, 1.2], [0.9, 1.0, 1.1])
    with pytest.raises(ValueError):
        classify_trend(df)


# --------------------------------------------------------------------------- #
# last_break_of_structure                                                     #
# --------------------------------------------------------------------------- #


def test_bos_bullish_detected():
    # Up to bar 4 the swing high is at idx 2 with high=1.10. At bar 7 close
    # breaks above 1.10 -> bullish BoS at bar 7.
    highs = [1.00, 1.05, 1.10, 1.05, 1.05, 1.06, 1.07, 1.15, 1.18]
    lows = [0.95, 1.00, 1.05, 1.00, 1.00, 1.01, 1.02, 1.10, 1.12]
    df = _frame_from_highs_lows(highs, lows)
    # Override close so the breakout is unambiguous.
    df.loc[7, "close"] = 1.13
    swings = detect_swings(df, left=2, right=2)
    event = last_break_of_structure(df, swings)
    assert event is not None
    assert event["direction"] == "bullish"
    assert event["level"] == pytest.approx(1.10)
    assert event["bar_index"] >= 7


def test_bos_bearish_detected():
    highs = [1.20, 1.15, 1.10, 1.15, 1.15, 1.14, 1.13, 1.05, 1.02]
    lows = [1.10, 1.05, 1.00, 1.05, 1.05, 1.04, 1.03, 0.95, 0.90]
    df = _frame_from_highs_lows(highs, lows)
    df.loc[7, "close"] = 0.97  # break below the swing low at idx 2 (low=1.00)
    swings = detect_swings(df, left=2, right=2)
    event = last_break_of_structure(df, swings)
    assert event is not None
    assert event["direction"] == "bearish"
    assert event["level"] == pytest.approx(1.00)


def test_bos_returns_none_when_no_break():
    highs = [1.0, 1.02, 1.01, 1.02, 1.01, 1.02, 1.01]
    lows = [0.95, 0.97, 0.96, 0.97, 0.96, 0.97, 0.96]
    df = _frame_from_highs_lows(highs, lows)
    swings = detect_swings(df, left=2, right=2)
    assert last_break_of_structure(df, swings) is None
