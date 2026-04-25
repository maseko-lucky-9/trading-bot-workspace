"""Tests for autoresearch wiring helpers in main.py."""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from main import _load_strategy, _start_autoresearch
from core.strategy.ema_crossover import EMACrossover
from core.strategy.mean_reversion import BollingerBandMeanReversion


def test_load_strategy_uses_params():
    strategy = _load_strategy({"ema_fast": 13, "ema_slow": 20})
    assert isinstance(strategy, EMACrossover)
    assert strategy.fast == 13
    assert strategy.slow == 20


def test_load_strategy_defaults_when_empty():
    strategy = _load_strategy({})
    assert strategy.fast == 9
    assert strategy.slow == 21


def test_load_strategy_swaps_invalid_fast_slow():
    """fast >= slow must fall back to defaults, not raise."""
    strategy = _load_strategy({"ema_fast": 21, "ema_slow": 9})
    assert strategy.fast == 9
    assert strategy.slow == 21


def test_load_strategy_returns_mean_reversion():
    strategy = _load_strategy({
        "strategy": "mean_reversion",
        "bb_period": 15,
        "bb_std": 2.5,
        "rsi_period": 10,
        "rsi_os": 25.0,
        "rsi_ob": 75.0,
        "atr_multiplier": 2.0,
    })
    assert isinstance(strategy, BollingerBandMeanReversion)
    assert strategy.bb_period == 15
    assert strategy.bb_std == 2.5
    assert strategy.rsi_period == 10
    assert strategy.rsi_oversold == 25.0
    assert strategy.rsi_overbought == 75.0
    assert strategy.atr_sl_multiplier == 2.0


def test_start_autoresearch_returns_running_thread():
    loop = MagicMock()
    loop.run = MagicMock()
    t = _start_autoresearch(loop, iterations=1)
    assert isinstance(t, threading.Thread)
    assert t.daemon is True
    t.join(timeout=2)
    loop.run.assert_called_once_with(max_iterations=1)


def test_start_autoresearch_thread_completes(capsys):
    loop = MagicMock()
    loop.run = MagicMock(return_value={"final_sharpe": 1.2})
    t = _start_autoresearch(loop, iterations=2)
    t.join(timeout=5)
    assert not t.is_alive()
    captured = capsys.readouterr()
    assert "autoresearch started" in captured.out
