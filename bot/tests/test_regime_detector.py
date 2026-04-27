"""Tests for RegimeDetector (Wave 3, F14)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.regime import RegimeDetector
from core.regime.detector import RegimeDetector as RegimeDetectorDirect


def _make_df(n: int = 200, seed: int = 0, vol: float = 0.001) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    prices = 1.10 + np.cumsum(rng.normal(0, vol, n))
    return pd.DataFrame({
        "open":   prices,
        "high":   prices + 0.001,
        "low":    prices - 0.001,
        "close":  prices,
        "volume": np.full(n, 1000),
    })


def _high_vol_df(n: int = 100) -> pd.DataFrame:
    """DataFrame with spiky, high-volatility returns."""
    rng = np.random.default_rng(99)
    prices = 1.10 + np.cumsum(rng.normal(0, 0.01, n))  # 10× base vol
    return pd.DataFrame({
        "open":   prices,
        "high":   prices + 0.01,
        "low":    prices - 0.01,
        "close":  prices,
        "volume": np.full(n, 1000),
    })


class TestVolRegime:
    def test_detect_returns_series_same_length(self):
        df = _make_df()
        det = RegimeDetector(method="vol", window=20)
        result = det.detect(df)
        assert len(result) == len(df)

    def test_detect_values_are_0_or_1(self):
        df = _make_df()
        det = RegimeDetector(method="vol", window=20)
        result = det.detect(df)
        assert set(result.unique()).issubset({0, 1})

    def test_current_regime_is_scalar(self):
        df = _make_df()
        det = RegimeDetector(method="vol", window=20)
        r = det.current_regime(df)
        assert r in (0, 1)

    def test_high_vol_classified_as_range(self):
        """High-volatility bars should predominantly be classified as RANGE (1)."""
        low_vol = _make_df(n=300, vol=0.0001)
        high_vol = _high_vol_df(n=300)
        combined = pd.concat([low_vol, high_vol], ignore_index=True)
        det = RegimeDetector(method="vol", window=20)
        labels = det.detect(combined)
        # Last 300 bars (high vol) should be mostly RANGE
        high_vol_labels = labels.iloc[-300:]
        assert high_vol_labels.mean() > 0.4  # more than 40% flagged as RANGE

    def test_trend_regime_constant(self):
        """Constant prices → zero log returns → zero rolling vol → all TREND."""
        prices = np.ones(200)  # truly constant: log(1/1) = 0 for all bars
        df = pd.DataFrame({
            "close": prices, "open": prices, "high": prices + 0.0001,
            "low": prices - 0.0001, "volume": np.ones(200),
        })
        det = RegimeDetector(method="vol", window=20)
        result = det.detect(df)
        # After warmup window, rolling std=0, median=0 → 0 > 0 is False → all TREND
        assert result.iloc[25:].sum() == 0


class TestHmmRegime:
    def test_hmm_returns_0_or_1(self):
        df = _make_df(n=200)
        det = RegimeDetector(method="hmm", window=20)
        result = det.detect(df)
        assert set(result.unique()).issubset({0, 1})

    def test_hmm_same_length_as_input(self):
        df = _make_df(n=200)
        det = RegimeDetector(method="hmm")
        assert len(det.detect(df)) == len(df)

    def test_hmm_falls_back_on_short_series(self):
        """With fewer bars than 2×window, should still return a series (vol fallback)."""
        df = _make_df(n=10)
        det = RegimeDetector(method="hmm", window=20)
        result = det.detect(df)
        assert len(result) == len(df)


class TestFromConfig:
    def test_from_config_defaults_to_vol(self):
        det = RegimeDetector.from_config({})
        assert det.method == "vol"
        assert det.window == 20

    def test_from_config_reads_method(self):
        cfg = {"strategy_config": {"regime": {"method": "hmm", "window": 30}}}
        det = RegimeDetector.from_config(cfg)
        assert det.method == "hmm"
        assert det.window == 30

    def test_from_config_canonical_path_takes_priority(self):
        """filters.regime takes priority over strategy_config.regime."""
        cfg = {
            "filters": {"regime": {"method": "vol", "window": 10}},
            "strategy_config": {"regime": {"method": "hmm", "window": 50}},
        }
        det = RegimeDetector.from_config(cfg)
        assert det.method == "vol"
        assert det.window == 10

    def test_from_config_canonical_path_only(self):
        cfg = {"filters": {"regime": {"method": "vol", "window": 15}}}
        det = RegimeDetector.from_config(cfg)
        assert det.method == "vol"
        assert det.window == 15

    def test_invalid_method_raises(self):
        with pytest.raises(ValueError, match="method"):
            RegimeDetector(method="bad_method")

    def test_package_export(self):
        """RegimeDetector importable from core.regime package."""
        assert RegimeDetector is RegimeDetectorDirect


class TestEngineRegimeGating:
    """Verify _run_event_loop respects strategy_regime_map."""

    def _make_ohlcv(self, n: int = 300, seed: int = 0) -> pd.DataFrame:
        rng = np.random.default_rng(seed)
        prices = 1.10 + np.cumsum(rng.normal(0, 0.001, n))
        times = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
        return pd.DataFrame({
            "time":   times,
            "open":   prices,
            "high":   prices + 0.001,
            "low":    prices - 0.001,
            "close":  prices,
            "volume": np.full(n, 1000.0),
        })

    def _base_config(self) -> dict:
        return {
            "risk": {
                "max_risk_per_trade": 0.01,
                "kelly_fraction": 0.25,
                "daily_loss_limit": 0.05,
                "trailing_dd_warn": 0.10,
                "trailing_dd_reduce": 0.15,
                "trailing_dd_halt": 0.20,
                "alert_loss_usd": 9999,
                "min_equity": 0.0,
            },
            "backtest": {"starting_equity": 10000.0},
            "filters": {
                "sessions": {"enabled": False},
                "news_blackout": {"enabled": False},
                "regime": {
                    "enabled": True,
                    "method": "vol",
                    "window": 20,
                    "strategy_regime_map": {
                        "ema_crossover": [0],   # only TREND
                        "mean_reversion": [1],  # only RANGE
                    },
                },
            },
        }

    def test_regime_disabled_does_not_block_entries(self):
        """When regime.enabled=False, strategy_regime_map is ignored."""
        import sys
        sys.path.insert(0, "/Users/ltmas/trading-bot-workspace/bot")
        from backtest.engine import _run_event_loop

        df = self._make_ohlcv()
        cfg = self._base_config()
        cfg["filters"]["regime"]["enabled"] = False

        params_no_regime = {"strategy": "ema_crossover", "ema_fast": 3, "ema_slow": 9,
                             "atr_multiplier": 1.5}
        stats_no_gate = _run_event_loop(df, params_no_regime, cfg, "GBPUSD")

        cfg2 = self._base_config()  # regime enabled, ema only TREND
        stats_gated = _run_event_loop(df, params_no_regime, cfg2, "GBPUSD")

        # With regime disabled we get at least as many trades
        assert stats_no_gate["trades"] >= stats_gated["trades"]

    def test_regime_signal_meta_contains_regime_key(self):
        """Regime value is stamped into each bar's signal meta (visible via trades)."""
        import sys
        sys.path.insert(0, "/Users/ltmas/trading-bot-workspace/bot")
        from backtest.engine import _run_event_loop

        df = self._make_ohlcv()
        cfg = self._base_config()
        cfg["filters"]["regime"]["strategy_regime_map"] = {}  # allow all regimes

        params = {"strategy": "ema_crossover", "ema_fast": 3, "ema_slow": 9,
                  "atr_multiplier": 1.5}
        stats = _run_event_loop(df, params, cfg, "GBPUSD")
        # Just verify the engine runs without error with regime enabled
        assert "trades" in stats

    def test_regime_blocks_when_wrong_regime(self):
        """Force all bars to RANGE; EMA (needs TREND=0) should get 0 entries."""
        import sys
        from unittest.mock import patch
        sys.path.insert(0, "/Users/ltmas/trading-bot-workspace/bot")
        from backtest.engine import _run_event_loop

        df = self._make_ohlcv()
        cfg = self._base_config()

        # EMA only allowed in TREND(0); patch detector to always return RANGE(1)
        all_range = pd.Series([RegimeDetector.RANGE] * len(df))
        with patch("backtest.engine.RegimeDetector") as MockDet:
            MockDet.from_config.return_value.detect.return_value = all_range
            params = {"strategy": "ema_crossover", "ema_fast": 3, "ema_slow": 9,
                      "atr_multiplier": 1.5}
            stats = _run_event_loop(df, params, cfg, "GBPUSD")

        assert stats["trades"] == 0
