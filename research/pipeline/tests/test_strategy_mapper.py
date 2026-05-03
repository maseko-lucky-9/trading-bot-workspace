"""Tests for strategy_mapper covering all four mapped types."""
from research.pipeline.models import StrategyCandidate
from research.pipeline.strategy_mapper import map_strategy


def _candidate(name, hypothesis, entry, exit_, params=None):
    return StrategyCandidate(
        name=name,
        hypothesis=hypothesis,
        entry_rules=[entry],
        exit_rules=[exit_],
        parameters=params or {},
    )


def test_maps_crossover_to_ema_crossover():
    c = _candidate(
        "EMA 9/21 cross", "trend ride",
        "fast EMA crosses above slow EMA",
        "atr-based stop",
        {"fast": 12, "slow": 26},
    )
    m = map_strategy(c)
    assert m.mapped_type == "ema_crossover"
    assert m.yaml_params["ema_fast"] == 12
    assert m.yaml_params["ema_slow"] == 26


def test_maps_band_to_mean_reversion():
    c = _candidate(
        "Bollinger reversion", "fade extremes",
        "price touches lower band; RSI oversold",
        "exit at middle band",
        {"bb_period": 25, "std_dev": 2.5},
    )
    m = map_strategy(c)
    assert m.mapped_type == "mean_reversion"
    assert m.yaml_params["bb_period"] == 25
    assert m.yaml_params["bb_std"] == 2.5


def test_maps_breakout_to_trend_following():
    c = _candidate(
        "Donchian breakout", "ride trends",
        "break above 20-day high with higher high",
        "swing low stop",
    )
    m = map_strategy(c)
    assert m.mapped_type == "trend_following"


def test_maps_pair_to_pairs_trading():
    c = _candidate(
        "EURUSD-GBPUSD pair", "cointegrated",
        "z-score of spread > 2",
        "spread reverts to mean",
        {"entry_zscore": 2.5, "lookback": 60},
    )
    m = map_strategy(c)
    assert m.mapped_type == "pairs_trading"
    assert m.yaml_params["entry_zscore"] == 2.5


def test_ml_signal_falls_back_to_mean_reversion():
    c = _candidate(
        "Neural net classifier", "ml signal",
        "classifier prob > 0.6",
        "fixed atr stop",
    )
    m = map_strategy(c)
    assert m.mapped_type == "mean_reversion"


def test_invalid_ema_fast_slow_falls_back():
    c = _candidate(
        "Bad cross", "test", "MA cross", "stop",
        {"fast": 50, "slow": 20},  # inverted
    )
    m = map_strategy(c)
    assert m.mapped_type == "ema_crossover"
    # invalid → reset to defaults
    assert m.yaml_params["ema_fast"] == 9
    assert m.yaml_params["ema_slow"] == 21
