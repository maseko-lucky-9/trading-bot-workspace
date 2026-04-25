"""Tests for AutoresearchLoop strategy comparison and param search."""
from __future__ import annotations

from unittest.mock import MagicMock, patch, call
from pathlib import Path

import pytest
import yaml

from autoresearch.loop import AutoresearchLoop, _strategy_params, _PARAMS_EMA, _PARAMS_MR


def _make_loop(tmp_path: Path) -> AutoresearchLoop:
    params = {
        "strategy": "ema_crossover",
        "ema_fast": 9.0,
        "ema_slow": 21.0,
        "atr_multiplier": 1.5,
        "bb_period": 20,
        "bb_std": 2.0,
        "rsi_period": 14,
        "rsi_os": 30.0,
        "rsi_ob": 70.0,
    }
    params_file = tmp_path / "params.yaml"
    params_file.write_text(yaml.dump(params))
    return AutoresearchLoop(params_path=params_file, results_path=tmp_path / "results.tsv")


def test_strategy_params_ema():
    assert _strategy_params({"strategy": "ema_crossover"}) is _PARAMS_EMA


def test_strategy_params_mr():
    assert _strategy_params({"strategy": "mean_reversion"}) is _PARAMS_MR


def test_strategy_params_default_is_ema():
    assert _strategy_params({}) is _PARAMS_EMA


def test_compare_strategies_keeps_current_when_challenger_loses(tmp_path):
    loop = _make_loop(tmp_path)
    loop._symbols = ["EURUSD"]  # single symbol so iter has exact count
    params = loop._load_params()

    sharpe_calls = iter([1.0, 0.8])  # current=1.0, challenger=0.8

    def fake_run_engine(*flags, symbol=None):
        s = next(sharpe_calls)
        return 0, f"SHARPE {s:.4f}", ""

    loop._run_engine = fake_run_engine
    result = loop.phase_compare_strategies(params)
    assert result.get("strategy") == "ema_crossover"


def test_compare_strategies_switches_when_challenger_wins(tmp_path):
    loop = _make_loop(tmp_path)
    loop._symbols = ["EURUSD"]  # single symbol so iter has exact count
    params = loop._load_params()

    sharpe_calls = iter([1.0, 1.15])  # current=1.0, challenger=1.15

    def fake_run_engine(*flags, symbol=None):
        s = next(sharpe_calls)
        return 0, f"SHARPE {s:.4f}", ""

    loop._run_engine = fake_run_engine
    result = loop.phase_compare_strategies(params)
    assert result.get("strategy") == "mean_reversion"


def test_phase_ideate_only_varies_ema_params(tmp_path):
    loop = _make_loop(tmp_path)
    params = loop._load_params()  # strategy: ema_crossover
    ema_names = {p[0] for p in _PARAMS_EMA}
    for _ in range(10):
        proposal = loop.phase_ideate(params)
        assert proposal["param"] in ema_names


def test_phase_ideate_only_varies_mr_params(tmp_path):
    loop = _make_loop(tmp_path)
    params = loop._load_params()
    params["strategy"] = "mean_reversion"
    mr_names = {p[0] for p in _PARAMS_MR}
    for _ in range(10):
        proposal = loop.phase_ideate(params)
        assert proposal["param"] in mr_names


# ------------------------------------------------------------------ #
# Coverage: helpers, phases, run driver                             #
# ------------------------------------------------------------------ #

def test_load_visited_reads_existing_file(tmp_path):
    import json
    loop = _make_loop(tmp_path)
    loop._visited_path.write_text(json.dumps([["ema_fast", 10.0], ["ema_slow", 25.0]]))
    loaded = loop._load_visited()
    assert ("ema_fast", 10.0) in loaded


def test_save_visited_persists(tmp_path):
    import json
    loop = _make_loop(tmp_path)
    loop._visited.add(("ema_fast", 12.0))
    loop._save_visited()
    data = json.loads(loop._visited_path.read_text())
    assert ["ema_fast", 12.0] in data


def test_configured_symbols_from_config(tmp_path):
    cfg = {"bot": {"instruments": ["EURUSD", "GBPUSD"]}}
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(cfg))
    params_file = tmp_path / "params.yaml"
    params_file.write_text(yaml.dump({"strategy": "ema_crossover"}))
    loop = AutoresearchLoop(
        config_path=config_path,
        params_path=params_file,
        results_path=tmp_path / "results.tsv",
    )
    assert "EURUSD" in loop._symbols
    assert "GBPUSD" in loop._symbols


def test_results_header_rotated_on_schema_change(tmp_path):
    loop = _make_loop(tmp_path)
    loop.results_path.write_text("old_col1\told_col2\n")  # stale schema
    loop._ensure_results_header()
    assert "iteration" in loop.results_path.read_text()
    assert (tmp_path / "results.tsv.bak").exists()


def test_parse_guard_true_on_pass(tmp_path):
    loop = _make_loop(tmp_path)
    assert loop._parse_guard("GUARD PASS drawdown=2%") is True
    assert loop._parse_guard("GUARD FAIL drawdown=9%") is False
    assert loop._parse_guard("no match here") is False


def test_phase_decide_keep_on_guard_pass(tmp_path):
    loop = _make_loop(tmp_path)
    assert loop.phase_decide(1.0, 1.1, True, 55.0, 56.0) == "keep"


def test_phase_decide_keep_on_improved_wr(tmp_path):
    loop = _make_loop(tmp_path)
    # guard fails but win_rate improved and sharpe not badly regressed
    assert loop.phase_decide(1.0, 0.95, False, 50.0, 55.0) == "keep"


def test_phase_decide_rollback_when_worse(tmp_path):
    loop = _make_loop(tmp_path)
    assert loop.phase_decide(1.0, 0.5, False, 50.0, 40.0) == "rollback"


def test_phase_log_writes_row_with_metrics(tmp_path):
    loop = _make_loop(tmp_path)
    proposal = {"param": "ema_fast", "old": 9.0, "new": 10.0}
    loop.phase_log(
        1, proposal, 1.23,
        "EURUSD: GUARD PASS drawdown=2.5% win_rate=55.0%",
        "keep", strategy="ema_crossover",
    )
    rows = loop.results_path.read_text().splitlines()
    assert len(rows) == 2  # header + data
    assert "ema_fast" in rows[1] and "2.5" in rows[1]


def test_run_single_iteration_keeps_better_params(tmp_path):
    loop = _make_loop(tmp_path)
    loop._symbols = ["EURUSD"]

    responses = iter([
        (0, "SHARPE 1.0", ""),   # compare_strategies: current
        (0, "SHARPE 0.8", ""),   # compare_strategies: challenger
        (0, "SHARPE 1.0", ""),   # baseline verify
        (0, "SHARPE 1.0\nGUARD PASS drawdown=2.5% win_rate=55.0%", ""),  # baseline guard
        (0, "SHARPE 1.1", ""),   # iter 1 verify
        (0, "SHARPE 1.1\nGUARD PASS drawdown=2.0% win_rate=56.0%", ""),  # iter 1 guard
    ])

    loop._run_engine = lambda *flags, symbol=None: next(responses)
    result = loop.run(max_iterations=1)

    assert result["final_sharpe"] == pytest.approx(1.1)
    assert result["iterations"] == 1
    assert result["decision"] in ("keep", "converged")


def test_run_single_iteration_rolls_back_worse_params(tmp_path):
    loop = _make_loop(tmp_path)
    loop._symbols = ["EURUSD"]

    responses = iter([
        (0, "SHARPE 1.0", ""),   # compare_strategies: current
        (0, "SHARPE 0.8", ""),   # compare_strategies: challenger
        (0, "SHARPE 1.0", ""),   # baseline verify
        (0, "SHARPE 1.0\nGUARD FAIL drawdown=9.0% win_rate=40.0%", ""),  # baseline guard
        (0, "SHARPE 0.5", ""),   # iter 1 verify
        (1, "SHARPE 0.5\nGUARD FAIL drawdown=12.0% win_rate=35.0%", ""),  # iter 1 guard
    ])

    loop._run_engine = lambda *flags, symbol=None: next(responses)
    result = loop.run(max_iterations=1)

    assert result["decision"] == "rollback"


# ------------------------------------------------------------------ #
# Exception / edge-case branches                                     #
# ------------------------------------------------------------------ #

def test_ensure_results_header_no_op_when_already_correct(tmp_path):
    loop = _make_loop(tmp_path)
    loop._ensure_results_header()  # writes correct header
    mtime_before = loop.results_path.stat().st_mtime_ns
    loop._ensure_results_header()  # should early-return without touching file
    assert loop.results_path.stat().st_mtime_ns == mtime_before


def test_load_visited_returns_empty_on_corrupt_json(tmp_path):
    loop = _make_loop(tmp_path)
    loop._visited_path.write_text("not valid json {{{{")
    assert loop._load_visited() == set()


def test_save_visited_silently_swallows_os_error(tmp_path):
    loop = _make_loop(tmp_path)
    loop._visited.add(("ema_fast", 10.0))
    loop._visited_path = tmp_path / "no_such_dir" / "visited.json"  # unwritable path
    loop._save_visited()  # must not raise


def test_configured_symbols_falls_back_on_missing_config(tmp_path):
    params_file = tmp_path / "params.yaml"
    params_file.write_text(yaml.dump({"strategy": "ema_crossover"}))
    loop = AutoresearchLoop(
        config_path=tmp_path / "nonexistent.yaml",
        params_path=params_file,
        results_path=tmp_path / "results.tsv",
    )
    assert loop._symbols == ["EURUSD"]


def test_load_params_defaults_when_file_missing(tmp_path):
    loop = _make_loop(tmp_path)
    loop.params_path = tmp_path / "nonexistent.yaml"
    params = loop._load_params()
    assert params["strategy"] == "ema_crossover"
    assert params["ema_fast"] == 9


def test_phase_decide_keep_on_pure_sharpe_improvement(tmp_path):
    loop = _make_loop(tmp_path)
    # sharpe improves, win_rate unchanged → line 231 path
    assert loop.phase_decide(1.0, 1.05, False, 50.0, 50.0) == "keep"


def test_phase_ideate_exhaustion_triggers_larger_steps(tmp_path):
    """Fill visited with all nearby ±1 candidates to force larger-step path."""
    loop = _make_loop(tmp_path)
    params = loop._load_params()  # ema_fast=9, ema_slow=21
    # Mark all ±step neighbours for every EMA param as visited
    for name, step, lo, hi in _PARAMS_EMA:
        val = float(params.get(name, lo))
        for d in (1, -1):
            c = round(max(lo, min(hi, val + d * step)), 4)
            loop._visited.add((name, c))
    proposal = loop.phase_ideate(params)
    assert proposal["param"] in {p[0] for p in _PARAMS_EMA}


def test_phase_ideate_hard_fallback_clears_visited(tmp_path):
    """Exhaust all larger-step candidates too → hard fallback clears visited."""
    loop = _make_loop(tmp_path)
    params = loop._load_params()
    # Saturate visited with every reachable candidate for every param
    for name, step, lo, hi in _PARAMS_EMA:
        val = float(params.get(name, lo))
        for mult in (1, 2, 3, 5):
            for d in (1, -1):
                c = round(max(lo, min(hi, val + d * step * mult)), 4)
                if c != val:
                    loop._visited.add((name, c))
    proposal = loop.phase_ideate(params)
    assert proposal["param"] in {p[0] for p in _PARAMS_EMA}
    assert len(loop._visited) == 0  # hard fallback cleared it


def test_run_converges_after_three_keeps_with_high_sharpe(tmp_path):
    """3 consecutive keep + guard_pass + sharpe > 1.5 → decision='converged'."""
    loop = _make_loop(tmp_path)
    loop._symbols = ["EURUSD"]

    # compare_strategies (2) + baseline (2) + 3 × (verify + guard) = 10 calls
    responses = iter([
        (0, "SHARPE 1.0", ""),    # compare: current
        (0, "SHARPE 0.8", ""),    # compare: challenger
        (0, "SHARPE 1.6", ""),    # baseline verify
        (0, "SHARPE 1.6\nGUARD PASS drawdown=1.0% win_rate=60.0%", ""),  # baseline guard
        (0, "SHARPE 1.7", ""),    # iter 1 verify
        (0, "SHARPE 1.7\nGUARD PASS drawdown=1.0% win_rate=61.0%", ""),  # iter 1 guard
        (0, "SHARPE 1.8", ""),    # iter 2 verify
        (0, "SHARPE 1.8\nGUARD PASS drawdown=1.0% win_rate=62.0%", ""),  # iter 2 guard
        (0, "SHARPE 1.9", ""),    # iter 3 verify
        (0, "SHARPE 1.9\nGUARD PASS drawdown=1.0% win_rate=63.0%", ""),  # iter 3 guard
    ])

    loop._run_engine = lambda *flags, symbol=None: next(responses)
    result = loop.run(max_iterations=10)

    assert result["decision"] == "converged"
    assert result["final_sharpe"] > 1.5


def test_run_engine_subprocess_real_call(tmp_path):
    """_run_engine actually shells out to backtest/engine.py."""
    loop = _make_loop(tmp_path)
    loop._symbols = ["EURUSD"]
    rc, stdout, stderr = loop._run_engine("--metric", "sharpe", "--bars", "100")
    assert rc == 0
    assert "SHARPE" in stdout
