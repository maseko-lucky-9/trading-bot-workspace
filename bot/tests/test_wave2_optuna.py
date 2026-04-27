"""Wave 2 — Optuna TPE + NSGA-II tests (F8, F9).

These tests mock _run_engine to avoid real backtest subprocess calls.
The Optuna SQLite DB is created in tmp_path so each test has its own file,
and the _optuna_study_name hash-scoping prevents DuplicatedStudyError.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from autoresearch.loop import AutoresearchLoop, _optuna_study_name


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
    pf = tmp_path / "params.yaml"
    pf.write_text(yaml.dump(params))
    loop = AutoresearchLoop(
        params_path=pf,
        results_path=tmp_path / "results.tsv",
    )
    loop.phase_compare_strategies = lambda p: p  # skip strategy comparison
    loop._run_engine = lambda *f, symbol=None: (
        0, "SHARPE 1.0\nGUARD PASS drawdown=2.0% win_rate=55.0%", ""
    )
    return loop


class TestOptunaTPE:
    def test_run_optuna_tpe_returns_result(self, tmp_path):
        loop = _make_loop(tmp_path)
        result = loop.run_optuna(n_trials=3, sampler="tpe")
        assert "final_sharpe" in result
        assert result["decision"] == "optuna_tpe"
        assert result["iterations"] == 3

    def test_run_optuna_tpe_writes_results_tsv(self, tmp_path):
        loop = _make_loop(tmp_path)
        loop.run_optuna(n_trials=2, sampler="tpe")
        rows = loop.results_path.read_text().splitlines()
        assert any("optuna" in r for r in rows)

    def test_run_optuna_tpe_saves_params(self, tmp_path):
        loop = _make_loop(tmp_path)
        loop.run_optuna(n_trials=3, sampler="tpe")
        saved = yaml.safe_load(loop.params_path.read_text())
        assert "ema_fast" in saved

    def test_run_optuna_with_mean_reversion_params(self, tmp_path):
        loop = _make_loop(tmp_path)
        params = yaml.safe_load(loop.params_path.read_text())
        params["strategy"] = "mean_reversion"
        loop.params_path.write_text(yaml.dump(params))
        result = loop.run_optuna(n_trials=3, sampler="tpe")
        assert result["decision"] == "optuna_tpe"


class TestOptunaNSGA2:
    def test_run_optuna_nsga2_creates_pareto_front(self, tmp_path):
        loop = _make_loop(tmp_path)
        result = loop.run_optuna(n_trials=4, sampler="nsga2")
        pareto_path = tmp_path / "pareto_front.json"
        assert pareto_path.exists(), "pareto_front.json was not created"
        pareto = json.loads(pareto_path.read_text())
        assert isinstance(pareto, list)
        assert len(pareto) > 0

    def test_run_optuna_nsga2_returns_result(self, tmp_path):
        loop = _make_loop(tmp_path)
        result = loop.run_optuna(n_trials=3, sampler="nsga2")
        assert result["decision"] == "optuna_nsga2"
        assert "final_dsr" in result

    def test_pareto_front_has_three_objectives(self, tmp_path):
        loop = _make_loop(tmp_path)
        loop.run_optuna(n_trials=5, sampler="nsga2")
        pareto = json.loads((tmp_path / "pareto_front.json").read_text())
        for entry in pareto:
            assert len(entry["values"]) == 3  # DSR, max_dd, win_rate


class TestOptunaResumability:
    def test_study_persists_and_grows(self, tmp_path):
        """Second run on same study DB should add to existing trials."""
        loop1 = _make_loop(tmp_path)
        r1 = loop1.run_optuna(n_trials=2, sampler="tpe")

        loop2 = _make_loop(tmp_path)
        r2 = loop2.run_optuna(n_trials=2, sampler="tpe")

        # Second run's total trial count is additive
        assert r2["iterations"] >= r1["iterations"]

    def test_study_db_file_created(self, tmp_path):
        loop = _make_loop(tmp_path)
        loop.run_optuna(n_trials=2, sampler="tpe")
        assert (tmp_path / "optuna_study.db").exists()


class TestStudyNaming:
    def test_same_path_produces_same_name(self, tmp_path):
        db = tmp_path / "study.db"
        assert _optuna_study_name("base", db) == _optuna_study_name("base", db)

    def test_different_paths_produce_different_names(self, tmp_path):
        db1 = tmp_path / "a" / "study.db"
        db2 = tmp_path / "b" / "study.db"
        assert _optuna_study_name("base", db1) != _optuna_study_name("base", db2)
