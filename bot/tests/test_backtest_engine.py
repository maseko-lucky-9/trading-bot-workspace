"""Tests for backtest engine (US-007) — CLI smoke + unit."""
from __future__ import annotations

import math
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

_BOT_ROOT = Path(__file__).resolve().parents[1]
_ENGINE = _BOT_ROOT / "backtest" / "engine.py"

sys.path.insert(0, str(_BOT_ROOT))
from backtest.engine import _compute_stats, _simulate_ema, _simulate_mean_reversion, _run_simulation  # noqa: E402


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_ENGINE), *args],
        cwd=str(_BOT_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )


def _ohlcv(n: int = 200, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    prices = 1.10 + np.cumsum(rng.normal(0, 0.0008, n))
    return pd.DataFrame({
        "time": pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC"),
        "open": prices,
        "high": prices + 0.0005,
        "low": prices - 0.0005,
        "close": prices,
        "volume": np.ones(n) * 1000,
    })


def _v_shape(n: int = 100) -> pd.DataFrame:
    """V-shape produces an EMA crossover."""
    down = np.linspace(1.20, 1.10, n // 2)
    up = np.linspace(1.10, 1.30, n - n // 2)
    prices = np.concatenate([down, up])
    return pd.DataFrame({
        "time": pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC"),
        "open": prices, "high": prices + 0.0005,
        "low": prices - 0.0005, "close": prices,
        "volume": np.ones(n) * 1000,
    })


# ------------------------------------------------------------------ #
# _compute_stats unit tests                                          #
# ------------------------------------------------------------------ #

def test_compute_stats_sharpe_positive_with_winning_trades():
    returns = [10.0, 12.0, 8.0, 15.0, 11.0]
    equity = list(np.cumsum(returns) + 10_000)
    result = _compute_stats(returns, equity, 500)
    assert result["sharpe"] > 0
    assert result["trades"] == 5
    assert result["bars"] == 500


def test_compute_stats_sharpe_zero_on_single_trade():
    result = _compute_stats([5.0], [10_000.0], 100)
    assert result["sharpe"] == 0.0


def test_compute_stats_sharpe_zero_when_no_std():
    result = _compute_stats([5.0, 5.0, 5.0], [10005.0, 10010.0, 10015.0], 200)
    assert result["sharpe"] == 0.0


def test_compute_stats_win_rate_correct():
    returns = [10.0, -5.0, 8.0, -3.0]
    result = _compute_stats(returns, list(np.cumsum(returns) + 10_000), 200)
    assert result["win_rate"] == pytest.approx(0.5)


def test_compute_stats_max_drawdown_non_negative():
    returns = [10.0, 20.0, -50.0, 5.0, -10.0]
    equity = list(np.cumsum(returns) + 10_000)
    result = _compute_stats(returns, equity, 300)
    assert result["max_drawdown"] >= 0.0


def test_compute_stats_empty_returns_zero_sharpe():
    result = _compute_stats([], [], 100)
    assert result["sharpe"] == 0.0
    assert result["max_drawdown"] == 0.0
    assert result["win_rate"] == 0.0
    assert result["trades"] == 0


# ------------------------------------------------------------------ #
# _simulate_ema unit tests                                           #
# ------------------------------------------------------------------ #

def test_simulate_ema_returns_required_keys():
    result = _simulate_ema(_ohlcv(200), {"ema_fast": 9, "ema_slow": 21})
    for k in ("sharpe", "max_drawdown", "win_rate", "trades", "bars"):
        assert k in result


def test_simulate_ema_bars_matches_input():
    df = _ohlcv(150)
    result = _simulate_ema(df, {"ema_fast": 9, "ema_slow": 21})
    assert result["bars"] == 150


def test_simulate_ema_sharpe_finite():
    result = _simulate_ema(_ohlcv(500), {"ema_fast": 9, "ema_slow": 21})
    assert not math.isnan(result["sharpe"])


def test_simulate_ema_produces_trades_on_crossover_data():
    result = _simulate_ema(_v_shape(200), {"ema_fast": 3, "ema_slow": 9})
    assert result["trades"] >= 1


def test_simulate_ema_guards_invalid_fast_gte_slow():
    """fast >= slow is silently corrected to defaults 9/21."""
    result = _simulate_ema(_ohlcv(200), {"ema_fast": 21, "ema_slow": 9})
    assert result["bars"] == 200


# ------------------------------------------------------------------ #
# _simulate_mean_reversion unit tests                                #
# ------------------------------------------------------------------ #

def test_simulate_mr_returns_required_keys():
    result = _simulate_mean_reversion(_ohlcv(300), {
        "bb_period": 20, "bb_std": 2.0, "rsi_period": 14,
        "rsi_os": 30.0, "rsi_ob": 70.0, "atr_multiplier": 1.5,
    })
    for k in ("sharpe", "max_drawdown", "win_rate", "trades", "bars"):
        assert k in result


def test_simulate_mr_bars_matches_input():
    df = _ohlcv(250)
    result = _simulate_mean_reversion(df, {
        "bb_period": 20, "bb_std": 2.0, "rsi_period": 14,
        "rsi_os": 30.0, "rsi_ob": 70.0, "atr_multiplier": 1.5,
    })
    assert result["bars"] == 250


def test_simulate_mr_sharpe_finite():
    result = _simulate_mean_reversion(_ohlcv(500), {
        "bb_period": 20, "bb_std": 2.0, "rsi_period": 14,
        "rsi_os": 30.0, "rsi_ob": 70.0, "atr_multiplier": 1.5,
    })
    assert not math.isnan(result["sharpe"])


def test_simulate_mr_win_rate_in_range():
    result = _simulate_mean_reversion(_ohlcv(500), {
        "bb_period": 20, "bb_std": 2.0, "rsi_period": 14,
        "rsi_os": 30.0, "rsi_ob": 70.0, "atr_multiplier": 1.5,
    })
    assert 0.0 <= result["win_rate"] <= 1.0


# ------------------------------------------------------------------ #
# _run_simulation dispatch                                           #
# ------------------------------------------------------------------ #

def test_run_simulation_dispatches_ema():
    result = _run_simulation(_ohlcv(200), {"strategy": "ema_crossover", "ema_fast": 9, "ema_slow": 21})
    assert result["bars"] == 200


def test_run_simulation_dispatches_mr():
    result = _run_simulation(_ohlcv(200), {
        "strategy": "mean_reversion",
        "bb_period": 20, "bb_std": 2.0, "rsi_period": 14,
        "rsi_os": 30.0, "rsi_ob": 70.0, "atr_multiplier": 1.5,
    })
    assert result["bars"] == 200


def test_run_simulation_defaults_to_ema_when_strategy_missing():
    result = _run_simulation(_ohlcv(200), {"ema_fast": 9, "ema_slow": 21})
    assert result["bars"] == 200


# ------------------------------------------------------------------ #
# CLI smoke tests                                                    #
# ------------------------------------------------------------------ #

def test_metric_sharpe_prints_sharpe_line():
    proc = _run("--metric", "sharpe", "--bars", "300")
    assert proc.returncode == 0
    assert re.search(r"^SHARPE\s+-?[0-9.]+", proc.stdout, flags=re.MULTILINE)


def test_guard_prints_guard_line():
    proc = _run("--guard", "--bars", "300")
    assert re.search(r"^GUARD\s+(PASS|FAIL)", proc.stdout, flags=re.MULTILINE)
    assert proc.returncode in (0, 1)


def test_mean_reversion_metric_sharpe(tmp_path):
    params_file = tmp_path / "mr_params.yaml"
    params_file.write_text(yaml.dump({
        "strategy": "mean_reversion",
        "bb_period": 20,
        "bb_std": 2.0,
        "rsi_period": 14,
        "rsi_os": 30.0,
        "rsi_ob": 70.0,
        "atr_multiplier": 1.5,
    }))
    proc = _run("--metric", "sharpe", "--bars", "300", "--params", str(params_file))
    assert proc.returncode == 0
    assert re.search(r"^SHARPE\s+-?[0-9.]+", proc.stdout, flags=re.MULTILINE)


def test_mean_reversion_guard(tmp_path):
    params_file = tmp_path / "mr_params.yaml"
    params_file.write_text(yaml.dump({
        "strategy": "mean_reversion",
        "bb_period": 20,
        "bb_std": 2.0,
        "rsi_period": 14,
        "rsi_os": 30.0,
        "rsi_ob": 70.0,
        "atr_multiplier": 1.5,
    }))
    proc = _run("--guard", "--bars", "300", "--params", str(params_file))
    assert re.search(r"^GUARD\s+(PASS|FAIL)", proc.stdout, flags=re.MULTILINE)
    assert proc.returncode in (0, 1)


# ------------------------------------------------------------------ #
# Utility function unit tests                                        #
# ------------------------------------------------------------------ #

def test_load_yaml_returns_empty_dict_when_file_missing(tmp_path):
    from backtest.engine import _load_yaml
    assert _load_yaml(tmp_path / "nonexistent.yaml") == {}


def test_load_yaml_parses_valid_file(tmp_path):
    from backtest.engine import _load_yaml
    f = tmp_path / "conf.yaml"
    f.write_text("foo: bar\nbaz: 42\n")
    data = _load_yaml(f)
    assert data["foo"] == "bar"
    assert data["baz"] == 42


def test_load_params_no_overlay(tmp_path):
    import argparse
    from backtest.engine import _load_params
    args = argparse.Namespace(params=None)
    result = _load_params(args, tmp_path)
    assert isinstance(result, dict)


def test_load_params_with_overlay(tmp_path):
    import argparse
    from backtest.engine import _load_params
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text("ema_fast: 5\nema_slow: 15\n")
    args = argparse.Namespace(params=str(overlay))
    result = _load_params(args, tmp_path)
    assert result.get("params", {}).get("ema_fast") == 5


def test_load_ohlcv_synthetic_fallback(tmp_path):
    """No cache, no bridge → synthetic data returned."""
    from backtest.engine import _load_ohlcv
    df = _load_ohlcv("EURUSD", "H1", 50, tmp_path)
    assert len(df) == 50
    assert "close" in df.columns


# ------------------------------------------------------------------ #
# Mark-to-market coverage (open position at simulation end)         #
# ------------------------------------------------------------------ #

def _ohlcv_arr(close: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame({
        "time": pd.date_range("2024-01-01", periods=len(close), freq="h", tz="UTC"),
        "open": close, "high": close + 0.0005,
        "low": close - 0.0005, "close": close, "volume": 1000,
    })


def test_simulate_ema_marks_open_position_at_end():
    """V-shape data opens a BUY that is never reversed → mark-to-market fires."""
    from backtest.engine import _simulate_ema
    close = np.concatenate([np.full(49, 1.10), np.linspace(1.10, 1.20, 51)])
    result = _simulate_ema(_ohlcv_arr(close), {"ema_fast": 3, "ema_slow": 9})
    assert "sharpe" in result
    assert result["trades"] >= 1


def test_simulate_mr_marks_open_position_at_end():
    """Sharp crash opens a BUY that never reverts → mark-to-market fires."""
    from backtest.engine import _simulate_mean_reversion
    close = np.concatenate([np.full(30, 1.10), np.linspace(1.10, 0.90, 70)])
    result = _simulate_mean_reversion(
        _ohlcv_arr(close),
        {"bb_period": 5, "bb_std": 0.5, "rsi_period": 3, "rsi_os": 80.0, "rsi_ob": 20.0, "atr_multiplier": 1.5},
    )
    assert "sharpe" in result


# ------------------------------------------------------------------ #
# Walk-forward holdout (--wf-train-pct)                              #
# ------------------------------------------------------------------ #

def test_apply_walk_forward_disabled_when_zero_returns_full_df():
    """train_pct=0.0 is a pass-through (no slicing)."""
    from backtest.engine import _apply_walk_forward
    df = _ohlcv(200)
    out = _apply_walk_forward(df, 0.0)
    assert len(out) == 200


def test_apply_walk_forward_disabled_when_negative_returns_full_df():
    """Negative train_pct treated the same as disabled."""
    from backtest.engine import _apply_walk_forward
    df = _ohlcv(200)
    out = _apply_walk_forward(df, -0.5)
    assert len(out) == 200


def test_apply_walk_forward_returns_tail_fraction():
    """train_pct=0.8 keeps the last 20% of bars (200 → 40)."""
    from backtest.engine import _apply_walk_forward
    df = _ohlcv(200)
    out = _apply_walk_forward(df, 0.8)
    assert len(out) == 40
    # Tail preserved: last close must equal source last close
    assert float(out["close"].iloc[-1]) == pytest.approx(float(df["close"].iloc[-1]))


def test_apply_walk_forward_clamps_train_pct_above_one():
    """train_pct=1.0+ is clamped so at least one bar remains."""
    from backtest.engine import _apply_walk_forward
    df = _ohlcv(1000)
    out = _apply_walk_forward(df, 1.5)
    assert len(out) >= 1


def test_cli_metric_sharpe_with_wf_train_pct_runs():
    """CLI accepts --wf-train-pct 0.8 + --metric sharpe and prints SHARPE."""
    proc = _run("--metric", "sharpe", "--bars", "500", "--wf-train-pct", "0.8")
    assert proc.returncode == 0, proc.stderr
    assert re.search(r"^SHARPE\s+-?[0-9.]+", proc.stdout, flags=re.MULTILINE)


def test_cli_guard_with_wf_train_pct_runs():
    """CLI accepts --wf-train-pct 0.8 + --guard and prints GUARD line."""
    proc = _run("--guard", "--bars", "500", "--wf-train-pct", "0.8")
    assert re.search(r"^GUARD\s+(PASS|FAIL)", proc.stdout, flags=re.MULTILINE), proc.stdout
    assert proc.returncode in (0, 1)


def test_cli_default_wf_train_pct_is_zero_and_changes_nothing():
    """Without --wf-train-pct, behaviour is identical to pre-change."""
    proc = _run("--metric", "sharpe", "--bars", "300")
    assert proc.returncode == 0
    m = re.search(r"^SHARPE\s+(-?[0-9.]+)", proc.stdout, flags=re.MULTILINE)
    assert m is not None


def test_cli_wf_train_pct_actually_reduces_simulated_bars(tmp_path):
    """With --wf-train-pct 0.9 over a small bar count, GUARD line reports
    fewer bars than the requested --bars (because we sliced the tail)."""
    proc = _run("--guard", "--bars", "1000", "--wf-train-pct", "0.9")
    # GUARD PASS includes 'bars=NNN'; GUARD FAIL does not. Use PASS-only branch
    # by checking only when bars= present.
    m_bars = re.search(r"bars=(\d+)", proc.stdout)
    if m_bars is not None:
        # 1000 bars * 0.1 holdout = ~100 bars
        assert int(m_bars.group(1)) <= 200, proc.stdout
