"""Tests for main._load_strategy after the trend_following branch is added (T05)."""
from __future__ import annotations

from main import _load_strategy
from core.strategy.ema_crossover import EMACrossover
from core.strategy.mean_reversion import BollingerBandMeanReversion
from core.strategy.trend_following import TrendFollowing


def test_load_strategy_mean_reversion_unchanged():
    s = _load_strategy(
        {
            "strategy": "mean_reversion",
            "bb_period": 14,
            "bb_std": 2.25,
            "rsi_period": 7,
            "rsi_os": 30,
            "rsi_ob": 70,
            "atr_multiplier": 2.25,
        }
    )
    assert isinstance(s, BollingerBandMeanReversion)


def test_load_strategy_ema_crossover_default_branch_unchanged():
    # The fallback branch is the EMA crossover; any unrecognised name lands here.
    s = _load_strategy({"strategy": "ema_crossover", "ema_fast": 9, "ema_slow": 21})
    assert isinstance(s, EMACrossover)


def test_load_strategy_trend_following():
    s = _load_strategy(
        {
            "strategy": "trend_following",
            "htf_resample_rule": "4h",
            "tp_r_multiple": 2.0,
            "mode": "standard",
        }
    )
    assert isinstance(s, TrendFollowing)
    assert s.mode == "standard"
    assert s.tp_r_multiple == 2.0
    assert s.htf_resample_rule == "4h"


def test_load_strategy_trend_following_premium_mode():
    s = _load_strategy({"strategy": "trend_following", "mode": "premium"})
    assert isinstance(s, TrendFollowing)
    assert s.mode == "premium"


def test_load_strategy_trend_following_default_kwargs():
    # No params besides the name — falls back to class defaults (v1.1).
    s = _load_strategy({"strategy": "trend_following"})
    assert isinstance(s, TrendFollowing)
    assert s.htf_resample_rule == "4h"
    assert s.tp_r_multiple == 1.5  # v1.1 default
    assert s.sl_atr_buffer == 1.0  # v1.1 default
    assert s.swing_left == 2
    assert s.swing_right == 2
    assert s.reversal_lookback == 10
    assert s.mode == "standard"
