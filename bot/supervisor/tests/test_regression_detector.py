"""Tests for regression_detector.py."""
import json
import time
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from bot.supervisor.regression_detector import (
    _detect_cb_false_positive,
    _detect_visited_set_cycling,
    RegressionDetector,
    _CYCLING_PARAM_REPEAT_N,
    _CYCLING_WINDOW,
    _CYCLING_SHARPE_CONSECUTIVE,
)


_RESULTS_HEADER = (
    "iteration\tparam\told_val\tnew_val\tsharpe\tdsr\tmax_dd\twin_rate\tdecision\tstrategy\ttimestamp\n"
)


def _make_state_json(tmp_path, peak_equity=11000.0, cooling_off_until=None) -> Path:
    if cooling_off_until is None:
        cooling_off_until = time.time() + 3600  # future
    state = {
        "peak_equity": peak_equity,
        "cooling_off_until": cooling_off_until,
    }
    p = tmp_path / "state.json"
    p.write_text(json.dumps(state))
    return p


def _make_results_tsv(tmp_path, combo_slug, rows: list[dict]) -> Path:
    combo_dir = tmp_path / combo_slug
    combo_dir.mkdir(parents=True, exist_ok=True)
    path = combo_dir / "results.tsv"
    lines = [_RESULTS_HEADER.strip()]
    for i, row in enumerate(rows):
        lines.append("\t".join([
            str(i),
            row.get("param", "bb_period"),
            row.get("old_val", "14.0"),
            row.get("new_val", "15.0"),
            str(row.get("sharpe", "1.5")),
            str(row.get("dsr", "1.0")),
            str(row.get("max_dd", "0.03")),
            str(row.get("win_rate", "0.55")),
            row.get("decision", "keep"),
            row.get("strategy", "mean_reversion"),
            "2026-05-01T00:00:00",
        ]))
    path.write_text("\n".join(lines) + "\n")
    return path


class TestCBFalsePositive:
    def test_detects_false_positive(self, tmp_path):
        state_json = _make_state_json(tmp_path, peak_equity=11000.0)
        with patch(
            "bot.supervisor.regression_detector._get_bridge_equity",
            return_value=10050.0,
        ):
            r = _detect_cb_false_positive(state_json=state_json)
        assert r is not None
        assert r.regression_type == "circuit_breaker_false_positive"
        assert r.scope == "global"
        assert r.combo_slug is None

    def test_no_detection_peak_too_low(self, tmp_path):
        state_json = _make_state_json(tmp_path, peak_equity=9000.0)
        with patch(
            "bot.supervisor.regression_detector._get_bridge_equity",
            return_value=10050.0,
        ):
            r = _detect_cb_false_positive(state_json=state_json)
        assert r is None

    def test_no_detection_not_cooling_off(self, tmp_path):
        state_json = _make_state_json(
            tmp_path, peak_equity=11000.0,
            cooling_off_until=time.time() - 3600,  # past
        )
        with patch(
            "bot.supervisor.regression_detector._get_bridge_equity",
            return_value=10050.0,
        ):
            r = _detect_cb_false_positive(state_json=state_json)
        assert r is None

    def test_no_detection_bridge_equity_high(self, tmp_path):
        state_json = _make_state_json(tmp_path, peak_equity=11000.0)
        with patch(
            "bot.supervisor.regression_detector._get_bridge_equity",
            return_value=11500.0,  # real equity, not the fallback
        ):
            r = _detect_cb_false_positive(state_json=state_json)
        assert r is None

    def test_no_detection_bridge_unreachable(self, tmp_path):
        state_json = _make_state_json(tmp_path, peak_equity=11000.0)
        with patch(
            "bot.supervisor.regression_detector._get_bridge_equity",
            return_value=None,
        ):
            r = _detect_cb_false_positive(state_json=state_json)
        assert r is None

    def test_no_detection_state_missing(self, tmp_path):
        r = _detect_cb_false_positive(state_json=tmp_path / "nonexistent.json")
        assert r is None


class TestVisitedSetCycling:
    def test_detects_cycling_by_repeat_pair(self, tmp_path):
        rows = [{"param": "bb_period", "new_val": "14.0"}] * (_CYCLING_PARAM_REPEAT_N + 1)
        _make_results_tsv(tmp_path, "EURUSD_M15_mean_reversion", rows)
        results_path = tmp_path / "EURUSD_M15_mean_reversion" / "results.tsv"
        r = _detect_visited_set_cycling("EURUSD_M15_mean_reversion", results_path)
        assert r is not None
        assert r.regression_type == "visited_set_cycling"
        assert r.scope == "combo"
        assert r.combo_slug == "EURUSD_M15_mean_reversion"

    def test_detects_cycling_by_sharpe_flat(self, tmp_path):
        rows = [{"sharpe": str(1.5 + 0.001 * i)} for i in range(_CYCLING_SHARPE_CONSECUTIVE + 2)]
        _make_results_tsv(tmp_path, "EURUSD_M15_ema_crossover", rows)
        results_path = tmp_path / "EURUSD_M15_ema_crossover" / "results.tsv"
        r = _detect_visited_set_cycling("EURUSD_M15_ema_crossover", results_path)
        assert r is not None

    def test_no_false_positive_varied_params(self, tmp_path):
        rows = [{"param": f"param_{i}", "new_val": str(i), "sharpe": str(1.0 + i * 0.05)}
                for i in range(_CYCLING_WINDOW)]
        _make_results_tsv(tmp_path, "EURUSD_H1_mean_reversion", rows)
        results_path = tmp_path / "EURUSD_H1_mean_reversion" / "results.tsv"
        r = _detect_visited_set_cycling("EURUSD_H1_mean_reversion", results_path)
        assert r is None

    def test_empty_results_no_detection(self, tmp_path):
        results_path = tmp_path / "results.tsv"
        r = _detect_visited_set_cycling("EURUSD_M15_mean_reversion", results_path)
        assert r is None


class TestRegressionDetectorScan:
    def test_scan_returns_cb_and_cycling(self, tmp_path):
        checkpoints_dir = tmp_path / "checkpoints"
        checkpoints_dir.mkdir()
        state_json = _make_state_json(checkpoints_dir)

        combos_dir = tmp_path / "combos"
        rows = [{"param": "bb_period", "new_val": "14.0"}] * (_CYCLING_PARAM_REPEAT_N + 1)
        _make_results_tsv(combos_dir, "EURUSD_M15_mean_reversion", rows)

        with patch("bot.supervisor.regression_detector._get_bridge_equity", return_value=10050.0):
            detector = RegressionDetector(
                combos_dir=combos_dir,
                state_json=state_json,
            )
            regressions = detector.scan()

        types = {r.regression_type for r in regressions}
        assert "circuit_breaker_false_positive" in types
        assert "visited_set_cycling" in types

    def test_scan_empty_combos_dir(self, tmp_path):
        combos_dir = tmp_path / "combos"
        combos_dir.mkdir()
        detector = RegressionDetector(
            combos_dir=combos_dir,
            state_json=tmp_path / "state.json",
        )
        regressions = detector.scan()
        # CB false positive won't fire (no state.json); no combos to check
        assert isinstance(regressions, list)
