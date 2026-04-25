"""Tests for AutoresearchLoop multi-symbol aggregation + walk-forward wiring."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from autoresearch.loop import AutoresearchLoop


def _make_loop_with_cfg(tmp_path: Path, cfg_extra: dict | None = None,
                        symbols: list[str] | None = None) -> AutoresearchLoop:
    """Build a loop pointed at an isolated config + params file."""
    cfg = {
        "bot": {"instruments": symbols if symbols is not None else ["EURUSD", "GBPUSD"]},
        "autoresearch": cfg_extra or {},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(cfg))
    params_file = tmp_path / "params.yaml"
    params_file.write_text(yaml.dump({
        "strategy": "ema_crossover",
        "ema_fast": 9.0, "ema_slow": 21.0,
    }))
    return AutoresearchLoop(
        config_path=config_path,
        params_path=params_file,
        results_path=tmp_path / "results.tsv",
    )


# ------------------------------------------------------------------ #
# Multi-symbol verify aggregation                                     #
# ------------------------------------------------------------------ #

def test_phase_verify_two_symbols_makes_two_engine_calls(tmp_path):
    """With 2 symbols and multi_symbol_mean=True, phase_verify calls engine
    once per symbol."""
    loop = _make_loop_with_cfg(tmp_path, cfg_extra={"multi_symbol_mean": True})
    assert loop._symbols == ["EURUSD", "GBPUSD"]

    calls: list[str] = []

    def fake_run_engine(*flags, symbol=None):
        calls.append(symbol)
        return 0, "SHARPE 1.0000", ""

    loop._run_engine = fake_run_engine
    loop.phase_verify()
    assert calls == ["EURUSD", "GBPUSD"]


def test_phase_verify_returns_mean_across_symbols(tmp_path):
    """Mean Sharpe across symbols when multi_symbol_mean=True."""
    loop = _make_loop_with_cfg(tmp_path, cfg_extra={"multi_symbol_mean": True})
    sharpes_by_symbol = {"EURUSD": 1.0, "GBPUSD": 2.0}

    def fake_run_engine(*flags, symbol=None):
        return 0, f"SHARPE {sharpes_by_symbol[symbol]:.4f}", ""

    loop._run_engine = fake_run_engine
    result = loop.phase_verify()
    assert result == pytest.approx(1.5)


def test_phase_verify_single_symbol_unchanged_behaviour(tmp_path):
    """With 1 symbol, only one engine call is made (preserves old path)."""
    loop = _make_loop_with_cfg(tmp_path, symbols=["EURUSD"])
    calls: list[str] = []

    def fake_run_engine(*flags, symbol=None):
        calls.append(symbol)
        return 0, "SHARPE 1.2345", ""

    loop._run_engine = fake_run_engine
    result = loop.phase_verify()
    assert calls == ["EURUSD"]
    assert result == pytest.approx(1.2345)


def test_phase_verify_multi_symbol_mean_disabled_uses_first_only(tmp_path):
    """multi_symbol_mean=False with multiple symbols → single engine call
    against the first symbol (opt-out preserves single-symbol semantics)."""
    loop = _make_loop_with_cfg(tmp_path, cfg_extra={"multi_symbol_mean": False})
    calls: list[str] = []

    def fake_run_engine(*flags, symbol=None):
        calls.append(symbol)
        return 0, "SHARPE 0.99", ""

    loop._run_engine = fake_run_engine
    loop.phase_verify()
    assert calls == ["EURUSD"]


# ------------------------------------------------------------------ #
# Multi-symbol guard: ALL must pass                                   #
# ------------------------------------------------------------------ #

def test_phase_guard_two_symbols_returns_false_if_any_fails(tmp_path):
    """If GBPUSD guard returns rc!=0, phase_guard returns False even when
    EURUSD passes."""
    loop = _make_loop_with_cfg(tmp_path)

    def fake_run_engine(*flags, symbol=None):
        if symbol == "EURUSD":
            return 0, "GUARD PASS drawdown=2.0% win_rate=55.0% bars=2000 trades=30", ""
        return 1, "GUARD FAIL drawdown=9.0% win_rate=40.0%", ""

    loop._run_engine = fake_run_engine
    passed, text, avg_wr, worst_dd = loop.phase_guard()
    assert passed is False
    assert "EURUSD" in text and "GBPUSD" in text


def test_phase_guard_two_symbols_all_pass_returns_true(tmp_path):
    """All symbols rc=0 → guard True; aggregated metrics returned."""
    loop = _make_loop_with_cfg(tmp_path)

    def fake_run_engine(*flags, symbol=None):
        return 0, f"GUARD PASS drawdown=2.0% win_rate=55.0% bars=2000 trades=30", ""

    loop._run_engine = fake_run_engine
    passed, text, avg_wr, worst_dd = loop.phase_guard()
    assert passed is True
    assert avg_wr == pytest.approx(55.0)
    # worst_dd is the MAX drawdown across symbols
    assert worst_dd == pytest.approx(2.0)


# ------------------------------------------------------------------ #
# Walk-forward flag passthrough on guard                              #
# ------------------------------------------------------------------ #

def test_phase_guard_passes_wf_train_pct_when_configured(tmp_path):
    """When wf_train_pct > 0 in config, phase_guard adds --wf-train-pct
    to the engine flags."""
    loop = _make_loop_with_cfg(tmp_path, cfg_extra={"wf_train_pct": 0.8})
    captured_flags: list[tuple] = []

    def fake_run_engine(*flags, symbol=None):
        captured_flags.append(flags)
        return 0, "GUARD PASS drawdown=1.0% win_rate=55.0% bars=200 trades=10", ""

    loop._run_engine = fake_run_engine
    loop.phase_guard()
    assert len(captured_flags) == 2  # 2 symbols
    for flags in captured_flags:
        assert "--guard" in flags
        assert "--wf-train-pct" in flags
        # value is stringified
        idx = flags.index("--wf-train-pct")
        assert float(flags[idx + 1]) == pytest.approx(0.8)


def test_phase_guard_does_not_pass_wf_when_disabled(tmp_path):
    """wf_train_pct=0 (or absent) → no --wf-train-pct flag added."""
    loop = _make_loop_with_cfg(tmp_path, cfg_extra={"wf_train_pct": 0.0})
    captured_flags: list[tuple] = []

    def fake_run_engine(*flags, symbol=None):
        captured_flags.append(flags)
        return 0, "GUARD PASS drawdown=1.0% win_rate=55.0% bars=200 trades=10", ""

    loop._run_engine = fake_run_engine
    loop.phase_guard()
    for flags in captured_flags:
        assert "--wf-train-pct" not in flags


def test_phase_verify_does_not_pass_wf_train_pct(tmp_path):
    """Verify always uses the FULL window — never passes --wf-train-pct,
    even when configured."""
    loop = _make_loop_with_cfg(tmp_path, cfg_extra={"wf_train_pct": 0.8})
    captured_flags: list[tuple] = []

    def fake_run_engine(*flags, symbol=None):
        captured_flags.append(flags)
        return 0, "SHARPE 1.0", ""

    loop._run_engine = fake_run_engine
    loop.phase_verify()
    for flags in captured_flags:
        assert "--wf-train-pct" not in flags


# ------------------------------------------------------------------ #
# Config loading defaults & edge cases                                #
# ------------------------------------------------------------------ #

def test_load_autoresearch_cfg_defaults_when_keys_absent(tmp_path):
    """Missing autoresearch keys → wf_train_pct=0.0, multi_symbol_mean=True."""
    loop = _make_loop_with_cfg(tmp_path, cfg_extra={})
    assert loop._wf_train_pct == 0.0
    assert loop._multi_symbol_mean is True


def test_load_autoresearch_cfg_clamps_negative_wf(tmp_path):
    loop = _make_loop_with_cfg(tmp_path, cfg_extra={"wf_train_pct": -0.5})
    assert loop._wf_train_pct == 0.0


def test_load_autoresearch_cfg_clamps_wf_at_one(tmp_path):
    loop = _make_loop_with_cfg(tmp_path, cfg_extra={"wf_train_pct": 1.5})
    assert loop._wf_train_pct < 1.0
    assert loop._wf_train_pct > 0.99
