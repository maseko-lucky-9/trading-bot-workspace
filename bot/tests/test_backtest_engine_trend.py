"""Smoke tests for the backtest engine's trend_following branch (T05 / AC-1)."""
from __future__ import annotations

from backtest.engine import _build_strategy
from core.strategy.trend_following import TrendFollowing
from core.strategy.mean_reversion import BollingerBandMeanReversion
from core.strategy.ema_crossover import EMACrossover


def test_build_strategy_trend_following_default():
    s = _build_strategy({"strategy": "trend_following"})
    assert isinstance(s, TrendFollowing)
    assert s.mode == "standard"
    assert s.htf_resample_rule == "4h"


def test_build_strategy_trend_following_premium_with_overrides():
    s = _build_strategy(
        {
            "strategy": "trend_following",
            "mode": "premium",
            "tp_r_multiple": 2.5,
            "atr_sl_multiplier": 2.0,
        }
    )
    assert isinstance(s, TrendFollowing)
    assert s.mode == "premium"
    assert s.tp_r_multiple == 2.5
    assert s.atr_sl_multiplier == 2.0


def test_build_strategy_mean_reversion_branch_unchanged():
    s = _build_strategy({"strategy": "mean_reversion"})
    assert isinstance(s, BollingerBandMeanReversion)


def test_build_strategy_default_falls_back_to_ema_crossover():
    # Backwards compatibility — unknown name still returns EMA crossover.
    s = _build_strategy({"strategy": "made_up_strategy"})
    assert isinstance(s, EMACrossover)
