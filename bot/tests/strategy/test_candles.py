"""Tests for core.strategy.candles (US-012)."""
from __future__ import annotations

import pandas as pd

from core.strategy.candles import is_pin_bar, is_pin_bar_at


def _bar(o: float, h: float, l: float, c: float) -> dict:
    return {"open": o, "high": h, "low": l, "close": c}


# ------------------------------------------------------------------ #
# Bullish pin                                                        #
# ------------------------------------------------------------------ #

def test_bullish_pin_long_lower_tail():
    # Range = 100, body = 5 (top), lower tail = 95 — canonical bullish pin.
    bar = _bar(o=99.5, h=100.0, l=0.0, c=99.0)
    # Body sits above l + (1 - 1/3) * range = 66.67; body_bot = 99.0 > 66.67 ✓
    # body=0.5; lower_tail=99.0; ratio=198 ✓
    assert is_pin_bar(bar, "bullish") is True


def test_bullish_pin_requires_close_above_prior():
    bar = _bar(o=99.5, h=100.0, l=0.0, c=99.0)
    # Prior close above current close — pin rejected
    assert is_pin_bar(bar, "bullish", prior_close=99.5) is False
    # Prior close below current close — pin accepted
    assert is_pin_bar(bar, "bullish", prior_close=98.0) is True


def test_bearish_pin_long_upper_tail():
    bar = _bar(o=0.5, h=100.0, l=0.0, c=1.0)
    assert is_pin_bar(bar, "bearish") is True


def test_bearish_pin_requires_close_below_prior():
    bar = _bar(o=0.5, h=100.0, l=0.0, c=1.0)
    assert is_pin_bar(bar, "bearish", prior_close=0.5) is False
    assert is_pin_bar(bar, "bearish", prior_close=2.0) is True


# ------------------------------------------------------------------ #
# Rejections                                                         #
# ------------------------------------------------------------------ #

def test_doji_rejected():
    # Body = 0, range = 100 — body in middle, no tail bias.
    bar = _bar(o=50.0, h=100.0, l=0.0, c=50.0)
    assert is_pin_bar(bar, "bullish") is False
    assert is_pin_bar(bar, "bearish") is False


def test_marubozu_rejected():
    # Body fills the entire range — definitely not a pin.
    bar = _bar(o=0.0, h=100.0, l=0.0, c=100.0)
    assert is_pin_bar(bar, "bullish") is False
    assert is_pin_bar(bar, "bearish") is False


def test_body_in_middle_rejected():
    # Range = 100, body = 50 (middle) — symmetric tails, not a pin.
    bar = _bar(o=25.0, h=100.0, l=0.0, c=75.0)
    assert is_pin_bar(bar, "bullish") is False
    assert is_pin_bar(bar, "bearish") is False


def test_zero_range_rejected():
    bar = _bar(o=1.10, h=1.10, l=1.10, c=1.10)
    assert is_pin_bar(bar, "bullish") is False


def test_tail_below_min_ratio_rejected():
    # Range = 10, body = 4 (top), tail = 6 — ratio 1.5 < 2.0 default.
    bar = _bar(o=9.5, h=10.0, l=0.0, c=6.0)
    assert is_pin_bar(bar, "bullish") is False


# ------------------------------------------------------------------ #
# DataFrame wrapper                                                  #
# ------------------------------------------------------------------ #

def test_is_pin_bar_at_dataframe_uses_prior_close():
    df = pd.DataFrame(
        [
            {"open": 1.05, "high": 1.06, "low": 1.04, "close": 1.05},  # prior bar
            {"open": 1.058, "high": 1.060, "low": 1.000, "close": 1.057},  # bullish pin
        ]
    )
    # Prior close = 1.05; current close = 1.057 > prior close → pin accepted.
    assert is_pin_bar_at(df, 1, "bullish") is True


def test_is_pin_bar_at_dataframe_first_bar_no_prior():
    # Single-bar frame: prior_close is None, only structure check matters.
    df = pd.DataFrame([{"open": 99.5, "high": 100.0, "low": 0.0, "close": 99.0}])
    assert is_pin_bar_at(df, 0, "bullish") is True
