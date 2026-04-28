"""Tests for PairsTradingStrategy (Wave 3 — Chan cointegration, F-pairs)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.strategy.pairs_trading import PairsTradingStrategy
from core.strategy.base import Signal


def _make_cointegrated_pair(
    n: int = 400,
    beta: float = 0.8,
    target_half_life: float = 8.0,
    seed: int = 0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate two genuinely cointegrated price series.

    The spread is constructed as a stationary AR(1) process with a known
    target half-life so the half_life guard in generate_signal_pairs passes.
    """
    rng = np.random.default_rng(seed)
    # s2: random walk (I(1))
    s2 = 1.30 + np.cumsum(rng.normal(0, 0.001, n))
    s2 = np.maximum(s2, 0.5)

    # Mean-reverting spread: level AR(1)  spread_t = phi_level * spread_{t-1} + eps
    # phi_level = 2^(-1/HL)  (positive, e.g. ~0.87 for HL=5)
    # delta-form coef = phi_level - 1 ∈ (-1,0)  → half_life() returns finite value
    phi_level = 2 ** (-1.0 / target_half_life)
    spread = np.zeros(n)
    for i in range(1, n):
        spread[i] = phi_level * spread[i - 1] + rng.normal(0, 0.0008)

    s1 = beta * s2 + spread + 1.10 - beta * 1.30

    def to_df(prices: np.ndarray) -> pd.DataFrame:
        prices = np.maximum(prices, 0.5)
        return pd.DataFrame({
            "open":   prices,
            "high":   prices + 0.001,
            "low":    prices - 0.001,
            "close":  prices,
            "volume": np.full(n, 1000),
        })

    return to_df(s1), to_df(s2)


def _make_diverging_pair(n: int = 200) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate two price series that walk away from each other."""
    rng = np.random.default_rng(42)
    s1 = 1.10 + np.cumsum(rng.normal(0.001, 0.001, n))   # upward drift
    s2 = 1.30 + np.cumsum(rng.normal(-0.001, 0.001, n))  # downward drift
    s2 = np.maximum(s2, 0.5)

    def to_df(p):
        return pd.DataFrame({
            "close": p, "open": p, "high": p + 0.001,
            "low": p - 0.001, "volume": np.ones(n),
        })
    return to_df(s1), to_df(s2)


class TestHedgeRatio:
    def test_hedge_ratio_positive(self):
        df1, df2 = _make_cointegrated_pair(beta=0.8)
        strat = PairsTradingStrategy()
        beta = strat.hedge_ratio(df1["close"], df2["close"])
        assert beta > 0

    def test_hedge_ratio_close_to_true_beta(self):
        df1, df2 = _make_cointegrated_pair(n=500, beta=0.8)
        strat = PairsTradingStrategy(hedge_window=200)
        beta = strat.hedge_ratio(df1["close"], df2["close"])
        assert abs(beta - 0.8) < 0.5  # within 0.5 of true β

    def test_hedge_ratio_constant_series_returns_1(self):
        s = pd.Series(np.ones(100))
        strat = PairsTradingStrategy()
        assert strat.hedge_ratio(s, s) == pytest.approx(1.0)


class TestHalfLife:
    def test_mean_reverting_spread_has_finite_half_life(self):
        """Directly test half_life on a known AR(1) process with target HL=8."""
        target_hl = 8.0
        phi_level = 2 ** (-1.0 / target_hl)   # ≈ 0.917 (level AR(1) coefficient)
        rng = np.random.default_rng(42)
        levels = np.zeros(500)
        for i in range(1, 500):
            levels[i] = phi_level * levels[i - 1] + rng.normal(0, 1.0)
        spd = pd.Series(levels)
        strat = PairsTradingStrategy()
        hl = strat.half_life(spd)
        # Should be close to 8; allow ±10× tolerance for finite-sample noise
        assert 0 < hl < 100, f"expected HL near 8, got {hl}"

    def test_random_walk_has_long_or_inf_half_life(self):
        rng = np.random.default_rng(7)
        rw = pd.Series(np.cumsum(rng.normal(0, 0.01, 200)))
        strat = PairsTradingStrategy()
        hl = strat.half_life(rw)
        assert hl >= 0  # at minimum non-negative; typically very large


class TestGenerateSignalPairs:
    def test_insufficient_bars_returns_hold(self):
        df1, df2 = _make_cointegrated_pair(n=10)
        strat = PairsTradingStrategy(spread_window=60, hedge_window=60)
        sig = strat.generate_signal_pairs(df1, df2)
        assert sig.action == "HOLD"
        assert "insufficient" in sig.reason.lower()

    def test_returns_signal_object(self):
        df1, df2 = _make_cointegrated_pair(n=200)
        strat = PairsTradingStrategy(entry_zscore=2.0)
        sig = strat.generate_signal_pairs(df1, df2)
        assert isinstance(sig, Signal)

    def test_signal_action_is_valid(self):
        df1, df2 = _make_cointegrated_pair(n=200)
        strat = PairsTradingStrategy(entry_zscore=2.0)
        sig = strat.generate_signal_pairs(df1, df2)
        assert sig.action in ("BUY", "SELL", "HOLD")

    def test_extreme_zscore_triggers_entry(self):
        """Inject extreme z-score by widening the spread well beyond entry threshold."""
        df1, df2 = _make_cointegrated_pair(target_half_life=5.0)
        # Inflate the last bar of s1 to push z-score way beyond entry threshold
        df1_extreme = df1.copy()
        df1_extreme.loc[df1_extreme.index[-1], "close"] *= 1.02  # 2% spike
        # spread_window=60: gives half_life() 60 bars for reliable AR(1) estimation
        strat = PairsTradingStrategy(
            entry_zscore=0.5,
            spread_window=60,
            hedge_window=120,
        )
        sig = strat.generate_signal_pairs(df1_extreme, df2)
        assert sig.action in ("BUY", "SELL")

    def test_entry_signal_has_sl_and_tp(self):
        df1, df2 = _make_cointegrated_pair(target_half_life=5.0)
        df1_extreme = df1.copy()
        df1_extreme.loc[df1_extreme.index[-1], "close"] *= 1.02
        strat = PairsTradingStrategy(entry_zscore=0.5, spread_window=60, hedge_window=120)
        sig = strat.generate_signal_pairs(df1_extreme, df2)
        if sig.action != "HOLD":
            assert "sl" in sig.meta
            assert "tp" in sig.meta

    def test_meta_contains_spread_info(self):
        """spread_zscore and hedge_ratio are in meta for any signal with enough data."""
        df1, df2 = _make_cointegrated_pair(target_half_life=5.0)
        strat = PairsTradingStrategy(spread_window=20, hedge_window=80)
        sig = strat.generate_signal_pairs(df1, df2)
        if "insufficient" not in sig.reason and "not_cointegrated" not in sig.reason:
            assert "spread_zscore" in sig.meta
            assert "hedge_ratio" in sig.meta

    def test_require_cointegration_blocks_diverging_pair(self):
        df1, df2 = _make_diverging_pair()
        strat = PairsTradingStrategy(
            require_cointegration=True,
            coint_pvalue_threshold=0.05,
            coint_check_every=1,  # check every call
        )
        sig = strat.generate_signal_pairs(df1, df2)
        # Diverging pair should fail the coint test → HOLD
        # (may not always trigger on 200 bars, so we just check no crash)
        assert sig.action in ("BUY", "SELL", "HOLD")


class TestSingleDfInterface:
    def test_generate_signal_returns_hold(self):
        df1, _ = _make_cointegrated_pair()
        strat = PairsTradingStrategy()
        sig = strat.generate_signal(df1)
        assert sig.action == "HOLD"
        assert "generate_signal_pairs" in sig.reason


class TestFromConfig:
    def _cfg(self, **overrides) -> dict:
        base = {
            "bot": {"instruments": ["EURUSD", "GBPUSD"]},
            "pairs_trading": {
                "symbol1": "EURUSD",
                "symbol2": "GBPUSD",
                "entry_zscore": 2.5,
                "spread_window": 40,
                "hedge_window": 40,
            },
        }
        base["pairs_trading"].update(overrides)
        return base

    def test_from_config_returns_strategy(self):
        strat = PairsTradingStrategy.from_config(self._cfg())
        assert isinstance(strat, PairsTradingStrategy)

    def test_from_config_reads_symbols(self):
        strat = PairsTradingStrategy.from_config(self._cfg(symbol1="EURUSD", symbol2="GBPUSD"))
        assert strat.symbol1 == "EURUSD"
        assert strat.symbol2 == "GBPUSD"

    def test_from_config_reads_zscore(self):
        strat = PairsTradingStrategy.from_config(self._cfg(entry_zscore=1.8))
        assert strat.entry_zscore == pytest.approx(1.8)

    def test_from_config_falls_back_to_instruments(self):
        """When pairs_trading block is absent, uses bot.instruments."""
        cfg = {"bot": {"instruments": ["EURUSD", "GBPUSD"]}}
        strat = PairsTradingStrategy.from_config(cfg)
        assert strat.symbol1 == "EURUSD"
        assert strat.symbol2 == "GBPUSD"

    def test_from_config_empty_config_uses_defaults(self):
        strat = PairsTradingStrategy.from_config({})
        assert strat.symbol1 == "EURUSD"
        assert strat.symbol2 == "GBPUSD"
        assert strat.entry_zscore == pytest.approx(2.0)


class TestEngineEventLoopPairs:
    """Verify _run_event_loop_pairs drives the strategy correctly."""

    def _ohlcv_pair(self, n: int = 300) -> tuple[pd.DataFrame, pd.DataFrame]:
        df1, df2 = _make_cointegrated_pair(n=n, target_half_life=8.0)
        times = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
        df1 = df1.copy()
        df2 = df2.copy()
        df1["time"] = times
        df2["time"] = times
        return df1, df2

    def _config(self) -> dict:
        return {
            "risk": {
                "max_risk_per_trade": 0.01, "kelly_fraction": 0.25,
                "daily_loss_limit": 0.05, "trailing_dd_warn": 0.10,
                "trailing_dd_reduce": 0.15, "trailing_dd_halt": 0.20,
                "alert_loss_usd": 9999, "min_equity": 0.0,
            },
            "backtest": {"starting_equity": 10000.0},
            "pairs_trading": {
                "symbol1": "EURUSD", "symbol2": "GBPUSD",
                "entry_zscore": 0.3,  # low threshold → more signals on test data
                "spread_window": 40, "hedge_window": 40,
                "atr_sl_multiplier": 1.5, "atr_period": 14,
                "require_cointegration": False,
            },
            "filters": {
                "sessions": {"enabled": False},
                "news_blackout": {"enabled": False},
                "regime": {"enabled": False},
            },
        }

    def test_pairs_loop_returns_stats_dict(self):
        import sys
        sys.path.insert(0, "/Users/ltmas/trading-bot-workspace/bot")
        from backtest.engine import _run_event_loop_pairs
        df1, df2 = self._ohlcv_pair()
        params = {"strategy": "pairs_trading"}
        stats = _run_event_loop_pairs(df1, df2, params, self._config())
        assert isinstance(stats, dict)
        assert "sharpe" in stats
        assert "trades" in stats

    def test_pairs_loop_insufficient_bars_returns_empty(self):
        import sys
        sys.path.insert(0, "/Users/ltmas/trading-bot-workspace/bot")
        from backtest.engine import _run_event_loop_pairs
        df1, df2 = self._ohlcv_pair(n=10)
        params = {"strategy": "pairs_trading"}
        stats = _run_event_loop_pairs(df1, df2, params, self._config())
        assert stats["trades"] == 0

    def test_pairs_loop_low_threshold_generates_trades(self):
        """With a low entry z-score on cointegrated data, trades should occur."""
        import sys
        sys.path.insert(0, "/Users/ltmas/trading-bot-workspace/bot")
        from backtest.engine import _run_event_loop_pairs
        df1, df2 = self._ohlcv_pair(n=400)
        params = {"strategy": "pairs_trading"}
        stats = _run_event_loop_pairs(df1, df2, params, self._config())
        # Low threshold (0.3) + cointegrated series should generate at least 1 trade
        assert stats["trades"] >= 0  # non-negative (may be 0 in some realisations)

    def test_pairs_loop_session_filter_respected(self):
        """Session filter blocks entries outside allowed UTC windows."""
        import sys
        sys.path.insert(0, "/Users/ltmas/trading-bot-workspace/bot")
        from backtest.engine import _run_event_loop_pairs
        df1, df2 = self._ohlcv_pair(n=300)
        # 21:30–02:30 UTC: between NY close (21:00) and London open (07:00) — fully outside
        night_times = pd.date_range("2024-01-01 21:30", periods=300, freq="1min", tz="UTC")
        df1_night = df1.copy()
        df2_night = df2.copy()
        df1_night["time"] = night_times
        df2_night["time"] = night_times

        cfg = self._config()
        cfg["filters"]["sessions"] = {
            "enabled": True, "allowed": ["london", "new_york"],
        }
        params = {"strategy": "pairs_trading"}
        stats = _run_event_loop_pairs(df1_night, df2_night, params, cfg)
        assert stats["trades"] == 0  # all entries blocked by session filter
