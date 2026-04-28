"""Unit tests for ``dashboard.sources``.

All tests are offline — ``subprocess.run``, ``urllib.request.urlopen``,
``pd.read_parquet``, and config IO are monkeypatched.
"""
from __future__ import annotations

import math
import subprocess
import urllib.request
from pathlib import Path

import pandas as pd
import pytest

from dashboard import sources


# --------------------------------------------------------------------------- #
# probe_process                                                               #
# --------------------------------------------------------------------------- #


def test_probe_process_returns_not_running_when_pgrep_finds_nothing(monkeypatch, fake_subprocess_run_factory):
    canned = {("pgrep", "-f", r"main\.py.*--mode paper"): ("", 1)}
    monkeypatch.setattr(subprocess, "run", fake_subprocess_run_factory(canned))
    out = sources.probe_process()
    assert out["status"] == "not_running"
    assert out["pid"] is None


def test_probe_process_filters_out_non_python_processes(monkeypatch, fake_subprocess_run_factory):
    canned = {
        ("pgrep", "-f", r"main\.py.*--mode paper"): ("4242\n", 0),
        ("ps", "-p", "4242", "-o", "comm="): ("/bin/bash\n", 0),
    }
    monkeypatch.setattr(subprocess, "run", fake_subprocess_run_factory(canned))
    out = sources.probe_process()
    # Only candidate was bash — filtered out, so status is not_running.
    assert out["status"] == "not_running"


def test_probe_process_returns_running_for_python_match(monkeypatch, fake_subprocess_run_factory):
    canned = {
        ("pgrep", "-f", r"main\.py.*--mode paper"): ("68208\n", 0),
        ("ps", "-p", "68208", "-o", "comm="): ("/usr/bin/python3.11\n", 0),
        ("ps", "-p", "68208", "-o", "etime="): ("01:23:45\n", 0),
    }
    monkeypatch.setattr(subprocess, "run", fake_subprocess_run_factory(canned))
    out = sources.probe_process()
    assert out["status"] == "running"
    assert out["pid"] == 68208
    assert out["etime"] == "01:23:45"


def test_probe_process_handles_pgrep_missing_binary(monkeypatch):
    def _raise(*a, **kw):
        raise FileNotFoundError("pgrep")

    monkeypatch.setattr(subprocess, "run", _raise)
    out = sources.probe_process()
    assert out["status"] == "unavailable"
    assert "pgrep" in out["error"]


# --------------------------------------------------------------------------- #
# probe_bridge                                                                #
# --------------------------------------------------------------------------- #


def test_probe_bridge_returns_ok_with_ea_connected(monkeypatch, fake_urlopen_ok):
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen_ok())
    out = sources.probe_bridge("http://192.168.64.1:8080")
    assert out["status"] == "ok"
    assert out["pong"] is True
    assert out["ea_connected"] is True
    assert isinstance(out["latency_ms"], float)
    assert out["error"] is None


def test_probe_bridge_returns_ok_with_ea_disconnected(monkeypatch, fake_urlopen_ok):
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen_ok({"pong": True, "ea_connected": False}))
    out = sources.probe_bridge("http://192.168.64.1:8080")
    assert out["status"] == "ok"
    assert out["ea_connected"] is False


def test_probe_bridge_returns_unreachable_on_url_error(monkeypatch, fake_urlopen_unreachable):
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen_unreachable)
    out = sources.probe_bridge("http://192.168.64.1:8080")
    assert out["status"] == "unreachable"
    assert out["pong"] is None
    assert out["ea_connected"] is None
    assert out["latency_ms"] is None
    assert "Connection" in out["error"] or out["error"]


# --------------------------------------------------------------------------- #
# read_trades + split_open_closed                                             #
# --------------------------------------------------------------------------- #


def test_read_trades_returns_empty_df_when_file_missing(tmp_path):
    out = sources.read_trades(tmp_path / "no-such.csv")
    assert isinstance(out, pd.DataFrame)
    assert out.empty


def test_read_trades_loads_canonical_columns(trades_csv_mixed):
    df = sources.read_trades(trades_csv_mixed)
    assert not df.empty
    assert "ticket" in df.columns and "profit" in df.columns and "type" in df.columns
    assert len(df) == 5


def test_split_open_closed_separates_by_close_time(trades_csv_mixed):
    df = sources.read_trades(trades_csv_mixed)
    open_pos, closed = sources.split_open_closed(df)
    assert len(open_pos) == 2
    assert len(closed) == 3


# --------------------------------------------------------------------------- #
# compute_equity_series                                                       #
# --------------------------------------------------------------------------- #


def test_compute_equity_series_handles_empty_df():
    out = sources.compute_equity_series(pd.DataFrame(columns=["profit", "close_time"]))
    assert out["status"] == "ok"
    assert out["equity"] == []
    assert out["peak_equity"] == 0.0


def test_compute_equity_series_matches_cumsum(trades_csv_mixed):
    df = sources.read_trades(trades_csv_mixed)
    _, closed = sources.split_open_closed(df)
    out = sources.compute_equity_series(closed)
    # Closed trades in order (sorted by close_time): +10, +5, -10 → equity [10, 15, 5]
    assert out["equity"] == pytest.approx([10.0, 15.0, 5.0])
    assert out["peak"] == pytest.approx([10.0, 15.0, 15.0])
    # drawdown at last point: (15 - 5) / 15
    assert out["drawdown"][-1] == pytest.approx(10.0 / 15.0)
    assert out["current_drawdown"] == pytest.approx(10.0 / 15.0)


def test_compute_equity_series_drawdown_non_negative_and_peak_monotonic(trades_csv_mixed):
    df = sources.read_trades(trades_csv_mixed)
    _, closed = sources.split_open_closed(df)
    out = sources.compute_equity_series(closed)
    assert all(d >= 0.0 for d in out["drawdown"])
    peaks = out["peak"]
    assert all(peaks[i] <= peaks[i + 1] for i in range(len(peaks) - 1))


# --------------------------------------------------------------------------- #
# DSR                                                                         #
# --------------------------------------------------------------------------- #


def test_dsr_returns_zero_for_too_few_trades():
    assert sources._compute_dsr(1.5, n_trades=1) == 0.0
    assert sources._compute_dsr(1.5, n_trades=0) == 0.0


def test_dsr_known_case_normal_returns():
    # Normal returns: skew=0, kurt=3 → denom_sq = 1, z = sharpe*sqrt(n-1)
    # sharpe=1.5, n=100 → z = 1.5 * sqrt(99) ≈ 14.92 → Φ(z) ≈ 1.0
    out = sources._compute_dsr(1.5, n_trades=100, skew=0.0, kurt=3.0)
    assert 0.99 < out <= 1.0
    # Negative sharpe → z < 0 → Φ(z) < 0.5
    out = sources._compute_dsr(-0.5, n_trades=50, skew=0.0, kurt=3.0)
    assert out < 0.5


def test_dsr_handles_negative_variance_term():
    # If denom_sq goes <= 0, function returns 0.0
    out = sources._compute_dsr(sharpe=10.0, n_trades=100, skew=2.0, kurt=3.0)
    # 1 - 2*10 + 0.5*100 = 1 - 20 + 50 = 31 → still positive, recompute.
    # Construct a degenerate case explicitly:
    out = sources._compute_dsr(sharpe=2.0, n_trades=100, skew=10.0, kurt=3.0)
    # 1 - 10*2 + 0.5*4 = 1 - 20 + 2 = -17 → returns 0.0
    assert out == 0.0


# --------------------------------------------------------------------------- #
# compute_metrics                                                             #
# --------------------------------------------------------------------------- #


def test_compute_metrics_on_empty_df_returns_zeros():
    out = sources.compute_metrics(pd.DataFrame(columns=["profit", "open_time", "close_time"]))
    assert out["status"] == "ok"
    assert out["trade_count"] == 0


def test_compute_metrics_populates_keys(trades_csv_mixed):
    df = sources.read_trades(trades_csv_mixed)
    _, closed = sources.split_open_closed(df)
    out = sources.compute_metrics(closed)
    assert out["status"] == "ok"
    assert {"sharpe", "dsr", "expectancy", "win_rate", "payoff_ratio", "trade_count"} <= set(out)
    assert out["trade_count"] == 3
    assert 0.0 <= out["dsr"] <= 1.0
    assert math.isfinite(out["sharpe"])


# --------------------------------------------------------------------------- #
# current_regime                                                              #
# --------------------------------------------------------------------------- #


def test_current_regime_returns_unknown_when_parquet_missing(tmp_path):
    out = sources.current_regime(
        config={"bot": {"instruments": ["EURUSD"], "timeframe": "M15"}},
        parquet_path=tmp_path / "missing.parquet",
    )
    assert out["status"] == "unavailable"
    assert out["label"] == "unknown"
    assert out["error"] == "parquet_missing"


def test_current_regime_classifies_synthetic_bars(tmp_path):
    # Synthesise a small parquet with a clear trending series.
    n = 200
    times = pd.date_range("2026-04-01", periods=n, freq="15min", tz="UTC")
    closes = pd.Series(range(1000, 1000 + n), dtype=float) / 1000.0  # monotone rising
    df = pd.DataFrame(
        {
            "timestamp": times,
            "open": closes - 0.0001,
            "high": closes + 0.0002,
            "low": closes - 0.0002,
            "close": closes,
            "volume": [100] * n,
        }
    )
    parquet_path = tmp_path / "EURUSD_M15.parquet"
    df.to_parquet(parquet_path)

    out = sources.current_regime(
        config={"bot": {"instruments": ["EURUSD"], "timeframe": "M15"}, "filters": {"regime": {"method": "vol", "window": 20}}},
        parquet_path=parquet_path,
        bars=200,
    )
    assert out["status"] == "ok"
    assert out["label"] in ("trend", "range")
    assert isinstance(out["regime_id"], int)
    assert out["bars_used"] == 200
