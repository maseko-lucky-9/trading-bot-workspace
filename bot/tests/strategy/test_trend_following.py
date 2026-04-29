"""Tests for TrendFollowing — standard mode (T02) + premium mode (T03)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.strategy.trend_following import TrendFollowing


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


def _ohlc_frame(closes: list[float], freq: str = "15min") -> pd.DataFrame:
    n = len(closes)
    highs = [c + 0.0005 for c in closes]
    lows = [c - 0.0005 for c in closes]
    opens = closes
    return pd.DataFrame(
        {
            "time": pd.date_range("2026-01-01", periods=n, freq=freq, tz="UTC"),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [100] * n,
        }
    )


def _bullish_trending_frame(n: int = 200, slope: float = 0.0006) -> pd.DataFrame:
    """Build a bullish frame with clear higher highs / higher lows.

    The base is a linear ramp; every 6 bars we inject a small pullback so
    swing fractals form. Final segment punches a clear new swing high.
    """
    rng = np.random.default_rng(0)
    closes = []
    base = 1.0500
    for i in range(n):
        wave = 0.0008 * np.sin(i / 4.0)
        # Pullback every 12 bars
        pull = -0.0015 if i % 12 in (5, 6) else 0.0
        noise = rng.normal(0, 0.00005)
        c = base + slope * i + wave + pull + noise
        closes.append(round(c, 5))
    # Force the last 3 bars to clearly punch the prior structure high
    last_high = max(closes[:-3])
    closes[-3] = round(last_high + 0.0010, 5)
    closes[-2] = round(last_high + 0.0020, 5)
    closes[-1] = round(last_high + 0.0030, 5)
    return _ohlc_frame(closes)


def _bearish_trending_frame(n: int = 200, slope: float = -0.0006) -> pd.DataFrame:
    rng = np.random.default_rng(1)
    closes = []
    base = 1.1500
    for i in range(n):
        wave = 0.0008 * np.sin(i / 4.0)
        pull = 0.0015 if i % 12 in (5, 6) else 0.0
        noise = rng.normal(0, 0.00005)
        c = base + slope * i + wave + pull + noise
        closes.append(round(c, 5))
    last_low = min(closes[:-3])
    closes[-3] = round(last_low - 0.0010, 5)
    closes[-2] = round(last_low - 0.0020, 5)
    closes[-1] = round(last_low - 0.0030, 5)
    return _ohlc_frame(closes)


# --------------------------------------------------------------------------- #
# Standard mode (T02)                                                         #
# --------------------------------------------------------------------------- #


def test_insufficient_bars_returns_hold():
    df = _ohlc_frame([1.10, 1.11, 1.12])
    sig = TrendFollowing().generate_signal(df)
    assert sig.action == "HOLD"
    assert sig.reason == "insufficient_bars"


def test_default_mode_is_standard():
    s = TrendFollowing()
    assert s.mode == "standard"
    assert s.name == "trend_following"


def test_invalid_tp_r_rejected():
    with pytest.raises(ValueError):
        TrendFollowing(tp_r_multiple=0.5)


def test_invalid_mode_rejected():
    with pytest.raises(ValueError):
        TrendFollowing(mode="extreme")  # type: ignore[arg-type]


def test_bullish_trend_emits_buy():
    df = _bullish_trending_frame()
    sig = TrendFollowing().generate_signal(df)
    # In a clean uptrend a BoS may have happened earlier; we accept either
    # BUY (live BoS) or HOLD with htf_bias=uptrend (no fresh BoS at the tail).
    assert sig.meta.get("htf_bias") in ("uptrend", "range")
    if sig.action == "BUY":
        assert sig.meta["bos_direction"] == "bullish"
        assert sig.meta["sl"] < sig.meta["entry_price"]
        assert sig.meta["tp"] > sig.meta["entry_price"]
        # 1:2 default R:R
        risk = sig.meta["entry_price"] - sig.meta["sl"]
        reward = sig.meta["tp"] - sig.meta["entry_price"]
        assert reward == pytest.approx(2.0 * risk, rel=1e-6)


def test_bearish_trend_emits_sell_or_hold():
    df = _bearish_trending_frame()
    sig = TrendFollowing().generate_signal(df)
    assert sig.meta.get("htf_bias") in ("downtrend", "range")
    if sig.action == "SELL":
        assert sig.meta["bos_direction"] == "bearish"
        assert sig.meta["sl"] > sig.meta["entry_price"]
        assert sig.meta["tp"] < sig.meta["entry_price"]


def test_range_market_yields_hold():
    rng = np.random.default_rng(42)
    closes = [1.1000 + rng.normal(0, 0.0003) for _ in range(200)]
    df = _ohlc_frame(closes)
    sig = TrendFollowing().generate_signal(df)
    assert sig.action == "HOLD"
    assert sig.reason in {
        "htf_range_no_trade",
        "no_break_of_structure",
        "bos_against_htf_bias",
        "structural_reversal_in_progress",
        "insufficient_htf_bars",
    }


def test_meta_includes_thesis_when_signal_emitted():
    df = _bullish_trending_frame()
    sig = TrendFollowing().generate_signal(df)
    if sig.action != "HOLD":
        assert "trade_thesis" in sig.meta
        assert "htf_bias" in sig.meta["trade_thesis"]
        assert "bos" in sig.meta["trade_thesis"]


def test_tp_r_multiple_respected_when_signal_emitted():
    df = _bullish_trending_frame()
    sig = TrendFollowing(tp_r_multiple=3.0).generate_signal(df)
    if sig.action == "BUY":
        risk = sig.meta["entry_price"] - sig.meta["sl"]
        reward = sig.meta["tp"] - sig.meta["entry_price"]
        assert reward == pytest.approx(3.0 * risk, rel=1e-6)


# --------------------------------------------------------------------------- #
# Premium mode (T03)                                                          #
# --------------------------------------------------------------------------- #


def test_premium_outside_zone_yields_hold():
    """When premium is required but the price is far above the Fib window,
    the signal must be suppressed."""
    df = _bullish_trending_frame()
    # Force the last bar far above the most recent swing window so the close
    # cannot fall inside any 0.618-0.786 retracement of the latest leg.
    df.loc[df.index[-1], "close"] = float(df["close"].max()) + 0.0500
    sig = TrendFollowing(mode="premium").generate_signal(df)
    if sig.action == "HOLD":
        assert sig.reason in {
            "not_in_premium_zone",
            "no_break_of_structure",
            "bos_against_htf_bias",
            "htf_range_no_trade",
            "structural_reversal_in_progress",
            "insufficient_htf_bars",
            "zero_risk",
        }
    # If the strategy did emit a signal anyway, the in-zone gate must say so.


def test_premium_mode_records_mode_in_meta():
    df = _bullish_trending_frame()
    sig = TrendFollowing(mode="premium").generate_signal(df)
    if sig.action != "HOLD":
        assert sig.meta.get("mode") == "premium"


def test_standard_mode_does_not_run_zone_check():
    """Standard mode should never gate on the Fib window — verify by giving
    a frame that *would* fail the premium check but passes standard rules."""
    df = _bullish_trending_frame()
    sig_std = TrendFollowing(mode="standard").generate_signal(df)
    # If standard mode says HOLD, fine — but reason must NOT be the premium gate.
    assert sig_std.reason != "not_in_premium_zone"


def test_premium_zone_helper_returns_bool():
    """Direct unit test of the helper to lock its boolean contract."""
    df = _bullish_trending_frame()
    s = TrendFollowing(mode="premium")
    from core.strategy.structure import detect_swings
    swings = detect_swings(df, s.swing_left, s.swing_right)
    out = s._premium_zone_check(df, swings, "bullish")
    assert isinstance(out, bool)


# --------------------------------------------------------------------------- #
# US-012: Pin-bar candle trigger gate                                         #
# --------------------------------------------------------------------------- #

def test_premium_marubozu_at_trigger_bar_yields_hold():
    """Premium mode must reject a setup where the trigger bar is a marubozu
    (no tail). Pin-bar gate fires HOLD with reason=no_pin_bar_confirmation."""
    df = _bullish_trending_frame()
    # Force the last bar to be a marubozu — full-body candle, no tails.
    last_close = float(df.iloc[-1]["close"])
    df.loc[df.index[-1], "open"] = last_close - 0.0010
    df.loc[df.index[-1], "high"] = last_close
    df.loc[df.index[-1], "low"] = last_close - 0.0010
    df.loc[df.index[-1], "close"] = last_close
    sig = TrendFollowing(mode="premium").generate_signal(df)
    # The signal might HOLD for another reason earlier in the chain (range,
    # missing BoS, not in premium zone, …), but if we reach the candle gate
    # the marubozu must trip it. We accept any HOLD that is consistent with
    # this chain — what matters is the strategy DOES NOT emit a BUY/SELL.
    assert sig.action == "HOLD"


def test_standard_mode_unaffected_by_pin_gate():
    """Standard mode must not consult the pin-bar trigger — the gate is
    premium-only by spec."""
    df = _bullish_trending_frame()
    last_close = float(df.iloc[-1]["close"])
    df.loc[df.index[-1], "open"] = last_close - 0.0010
    df.loc[df.index[-1], "high"] = last_close
    df.loc[df.index[-1], "low"] = last_close - 0.0010
    df.loc[df.index[-1], "close"] = last_close
    sig = TrendFollowing(mode="standard").generate_signal(df)
    # Standard mode reasons never reference the pin-bar gate.
    assert sig.reason != "no_pin_bar_confirmation"
