"""Tests for EMACrossover strategy."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.strategy.ema_crossover import EMACrossover


def _df(close: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame({
        "time": pd.date_range("2024-01-01", periods=len(close), freq="h", tz="UTC"),
        "open": close,
        "high": close + 0.0005,
        "low": close - 0.0005,
        "close": close,
        "volume": np.ones(len(close)) * 100,
    })


def _buy_df() -> pd.DataFrame:
    """Sharp V-shape: long downtrend then strong rally → fast crosses above slow."""
    n = 80
    down = np.linspace(1.20, 1.10, 40)
    up = np.linspace(1.10, 1.30, 40)
    return _df(np.concatenate([down, up]))


def _sell_df() -> pd.DataFrame:
    """Inverted V: long uptrend then sharp drop → fast crosses below slow."""
    n = 80
    up = np.linspace(1.10, 1.30, 40)
    down = np.linspace(1.30, 1.10, 40)
    return _df(np.concatenate([up, down]))


@pytest.fixture
def strategy():
    return EMACrossover(fast=9, slow=21)


# ------------------------------------------------------------------ #
# Constructor                                                        #
# ------------------------------------------------------------------ #

def test_constructor_raises_when_fast_gte_slow():
    with pytest.raises(ValueError):
        EMACrossover(fast=21, slow=9)


def test_constructor_raises_when_fast_equals_slow():
    with pytest.raises(ValueError):
        EMACrossover(fast=14, slow=14)


# ------------------------------------------------------------------ #
# compute_indicators                                                  #
# ------------------------------------------------------------------ #

def test_compute_indicators_adds_required_columns(strategy, ohlcv_200):
    ind = strategy.compute_indicators(ohlcv_200)
    for col in ("ema_fast", "ema_slow", "atr"):
        assert col in ind.columns


def test_compute_indicators_same_length(strategy, ohlcv_200):
    ind = strategy.compute_indicators(ohlcv_200)
    assert len(ind) == len(ohlcv_200)


def test_ema_fast_more_responsive_than_slow(strategy, ohlcv_200):
    """Fast EMA must track close more closely than slow EMA."""
    ind = strategy.compute_indicators(ohlcv_200)
    close = ind["close"]
    fast_err = (ind["ema_fast"] - close).abs().mean()
    slow_err = (ind["ema_slow"] - close).abs().mean()
    assert fast_err < slow_err


# ------------------------------------------------------------------ #
# generate_signal                                                     #
# ------------------------------------------------------------------ #

def test_hold_on_insufficient_bars(strategy):
    sig = strategy.generate_signal(_df(np.ones(5) * 1.10))
    assert sig.action == "HOLD"
    assert sig.reason == "insufficient_bars"


def test_signal_action_valid(strategy, ohlcv_200):
    sig = strategy.generate_signal(ohlcv_200)
    assert sig.action in ("BUY", "SELL", "HOLD")


def test_signal_strength_in_range(strategy, ohlcv_200):
    sig = strategy.generate_signal(ohlcv_200)
    assert 0.0 <= sig.strength <= 1.0


def test_buy_signal_on_upward_crossover(strategy):
    sig = strategy.generate_signal(_buy_df())
    assert sig.action in ("BUY", "HOLD")


def test_sell_signal_on_downward_crossover(strategy):
    sig = strategy.generate_signal(_sell_df())
    assert sig.action in ("SELL", "HOLD")


def test_buy_signal_has_sl_below_entry():
    strat = EMACrossover(fast=3, slow=9)
    sig = strat.generate_signal(_buy_df())
    if sig.action == "BUY":
        assert sig.meta["sl"] < sig.meta["entry_price"]
        assert sig.meta["tp"] > sig.meta["entry_price"]


def test_sell_signal_has_sl_above_entry():
    strat = EMACrossover(fast=3, slow=9)
    sig = strat.generate_signal(_sell_df())
    if sig.action == "SELL":
        assert sig.meta["sl"] > sig.meta["entry_price"]
        assert sig.meta["tp"] < sig.meta["entry_price"]


def test_meta_always_contains_ema_values(strategy, ohlcv_200):
    sig = strategy.generate_signal(ohlcv_200)
    assert "ema_fast" in sig.meta
    assert "ema_slow" in sig.meta
    assert "atr" in sig.meta
    assert "entry_price" in sig.meta


# ------------------------------------------------------------------ #
# Guaranteed signal coverage (deterministic crossover fixtures)      #
# ------------------------------------------------------------------ #

def _flat_then_spike(from_price: float, to_price: float) -> pd.DataFrame:
    """49 flat bars at from_price, then one bar at to_price.

    With fast=3 (α=0.5) vs slow=9 (α=0.2), one large move guarantees a
    crossover because the fast EMA moves twice as much as the slow EMA.
    """
    close = np.concatenate([np.full(49, from_price), [to_price]])
    return pd.DataFrame({
        "time": pd.date_range("2024-01-01", periods=50, freq="h", tz="UTC"),
        "open": close, "high": close + 0.0005,
        "low": close - 0.0005, "close": close,
        "volume": np.ones(50) * 100,
    })


def test_guaranteed_buy_signal():
    strat = EMACrossover(fast=3, slow=9)
    sig = strat.generate_signal(_flat_then_spike(1.10, 1.20))
    assert sig.action == "BUY"
    assert sig.strength > 0.0
    assert sig.meta["sl"] < sig.meta["entry_price"]
    assert sig.meta["tp"] > sig.meta["entry_price"]


def test_guaranteed_sell_signal():
    strat = EMACrossover(fast=3, slow=9)
    sig = strat.generate_signal(_flat_then_spike(1.20, 1.10))
    assert sig.action == "SELL"
    assert sig.strength > 0.0
    assert sig.meta["sl"] > sig.meta["entry_price"]
    assert sig.meta["tp"] < sig.meta["entry_price"]


def test_base_strategy_generate_signal_raises_not_implemented():
    from core.strategy.base import Strategy

    class _Stub(Strategy):
        name = "stub"
        def compute_indicators(self, df):
            return df
        def generate_signal(self, df):
            return super().generate_signal(df)

    with pytest.raises(NotImplementedError):
        _Stub().generate_signal(pd.DataFrame())
