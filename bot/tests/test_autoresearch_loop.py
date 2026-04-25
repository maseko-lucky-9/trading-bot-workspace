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
