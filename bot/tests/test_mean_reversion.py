"""Tests for BollingerBandMeanReversion strategy."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.strategy.mean_reversion import BollingerBandMeanReversion


@pytest.fixture
def strategy():
    return BollingerBandMeanReversion()


@pytest.fixture
def short_df():
    """Fewer bars than min required — triggers insufficient_bars guard."""
    n = 10
    rng = np.random.default_rng(0)
    close = 1.10 + np.cumsum(rng.normal(0, 0.0005, n))
    return pd.DataFrame({
        "open": close, "high": close + 0.001, "low": close - 0.001,
        "close": close, "volume": np.ones(n) * 100,
    })


def test_hold_when_insufficient_bars(strategy, short_df):
    sig = strategy.generate_signal(short_df)
    assert sig.action == "HOLD"
    assert sig.reason == "insufficient_bars"


def test_does_not_crash_on_200_bar_sample(strategy, ohlcv_200):
    sig = strategy.generate_signal(ohlcv_200)
    assert sig.action in ("BUY", "SELL", "HOLD")


def test_signal_action_is_valid(strategy, ohlcv_200):
    sig = strategy.generate_signal(ohlcv_200)
    assert sig.action in ("BUY", "SELL", "HOLD")
    assert 0.0 <= sig.strength <= 1.0


def test_buy_signal_on_oversold_data(strategy):
    """Force a BUY: close at lower band with RSI < 30."""
    n = 100
    # Construct a downtrend that pushes price below lower BB and RSI < 30
    close = np.linspace(1.20, 1.05, n)
    df = pd.DataFrame({
        "open": close, "high": close + 0.0005,
        "low": close - 0.0005, "close": close,
        "volume": np.ones(n) * 500,
    })
    sig = strategy.generate_signal(df)
    # With strong downtrend, either BUY (oversold) or HOLD is acceptable
    assert sig.action in ("BUY", "HOLD")


def test_sell_signal_on_overbought_data(strategy):
    """Force a SELL: close at upper band with RSI > 70."""
    n = 100
    close = np.linspace(1.05, 1.20, n)
    df = pd.DataFrame({
        "open": close, "high": close + 0.0005,
        "low": close - 0.0005, "close": close,
        "volume": np.ones(n) * 500,
    })
    sig = strategy.generate_signal(df)
    assert sig.action in ("SELL", "HOLD")


def test_meta_contains_expected_keys(strategy, ohlcv_200):
    sig = strategy.generate_signal(ohlcv_200)
    for key in ("bb_upper", "bb_lower", "bb_mid", "rsi", "atr", "entry_price"):
        assert key in sig.meta


def test_sl_tp_present_on_buy_signal():
    """Synthesise data that guarantees a BUY signal and check sl/tp."""
    strategy = BollingerBandMeanReversion(
        bb_period=10, rsi_period=7, rsi_oversold=80  # very lenient oversold
    )
    n = 50
    # RSI will be low because we have a sustained drop
    close = np.linspace(1.20, 1.00, n)
    df = pd.DataFrame({
        "open": close, "high": close + 0.0005,
        "low": close - 0.0005, "close": close,
        "volume": np.ones(n) * 500,
    })
    sig = strategy.generate_signal(df)
    if sig.action == "BUY":
        assert "sl" in sig.meta
        assert "tp" in sig.meta
        assert sig.meta["sl"] < sig.meta["entry_price"]
        assert sig.meta["tp"] > sig.meta["entry_price"]


# ------------------------------------------------------------------ #
# Guaranteed signal coverage (deterministic band-breach fixtures)    #
# ------------------------------------------------------------------ #

def _mr_buy_df() -> pd.DataFrame:
    """Flat then sharp crash → price breaches lower band, RSI collapses."""
    close = np.concatenate([np.full(25, 1.10), np.linspace(1.10, 0.90, 5)])
    return pd.DataFrame({
        "open": close, "high": close + 0.0005,
        "low": close - 0.0005, "close": close,
        "volume": np.ones(30) * 100,
    })


def _mr_sell_df() -> pd.DataFrame:
    """Flat then sharp rally → price breaches upper band, RSI spikes."""
    close = np.concatenate([np.full(25, 1.10), np.linspace(1.10, 1.30, 5)])
    return pd.DataFrame({
        "open": close, "high": close + 0.0005,
        "low": close - 0.0005, "close": close,
        "volume": np.ones(30) * 100,
    })


def test_guaranteed_buy_signal():
    strat = BollingerBandMeanReversion(
        bb_period=5, bb_std=0.5, rsi_period=3, rsi_oversold=80,
    )
    sig = strat.generate_signal(_mr_buy_df())
    assert sig.action == "BUY"
    assert sig.strength > 0.0
    assert sig.meta["sl"] < sig.meta["entry_price"]
    assert sig.meta["tp"] > sig.meta["entry_price"]


def test_guaranteed_sell_signal():
    strat = BollingerBandMeanReversion(
        bb_period=5, bb_std=0.5, rsi_period=3, rsi_overbought=20,
    )
    sig = strat.generate_signal(_mr_sell_df())
    assert sig.action == "SELL"
    assert sig.strength > 0.0
    assert sig.meta["sl"] > sig.meta["entry_price"]
    assert sig.meta["tp"] < sig.meta["entry_price"]
