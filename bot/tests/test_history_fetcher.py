"""Tests for HistoryFetcher (US-003)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from core.data.history import HistoryFetcher


def _bridge(rows=None, fail=False):
    b = MagicMock()
    if fail:
        b.get_history.side_effect = Exception("bridge down")
        b.get_tick.side_effect = Exception("bridge down")
    else:
        b.get_history.return_value = rows or []
        b.get_tick.return_value = {"bid": 1.10000}
    return b


def _make_rows(n=20):
    return [
        {
            "time": 1700000000 + i * 3600,
            "open": 1.10,
            "high": 1.101,
            "low": 1.099,
            "close": 1.10 + i * 0.0001,
            "volume": 100,
        }
        for i in range(n)
    ]


# ------------------------------------------------------------------ #
# Column contract                                                     #
# ------------------------------------------------------------------ #

def test_fetch_returns_dataframe(tmp_path):
    fetcher = HistoryFetcher(_bridge(_make_rows(30)), cache_dir=tmp_path)
    df = fetcher.fetch("EURUSD", "H1", bars=30)
    assert isinstance(df, pd.DataFrame)
    for col in ("time", "open", "high", "low", "close", "volume"):
        assert col in df.columns


def test_fetch_time_column_is_datetime(tmp_path):
    fetcher = HistoryFetcher(_bridge(_make_rows(20)), cache_dir=tmp_path)
    df = fetcher.fetch("EURUSD", "H1", bars=20)
    assert pd.api.types.is_datetime64_any_dtype(df["time"])


def test_fetch_rows_sorted_ascending(tmp_path):
    rows = _make_rows(20)
    import random; random.shuffle(rows)
    fetcher = HistoryFetcher(_bridge(rows), cache_dir=tmp_path)
    df = fetcher.fetch("EURUSD", "H1", bars=20)
    assert df["time"].is_monotonic_increasing


def test_fetch_numeric_ohlcv_columns(tmp_path):
    fetcher = HistoryFetcher(_bridge(_make_rows(20)), cache_dir=tmp_path)
    df = fetcher.fetch("EURUSD", "H1", bars=20)
    for col in ("open", "high", "low", "close"):
        assert pd.api.types.is_float_dtype(df[col])
    assert pd.api.types.is_integer_dtype(df["volume"])


# ------------------------------------------------------------------ #
# Parquet cache                                                       #
# ------------------------------------------------------------------ #

def test_fetch_writes_parquet_cache(tmp_path):
    fetcher = HistoryFetcher(_bridge(_make_rows(20)), cache_dir=tmp_path)
    fetcher.fetch("EURUSD", "H1", bars=20)
    assert (tmp_path / "EURUSD_H1.parquet").exists()


def test_save_load_cache_round_trip(tmp_path):
    fetcher = HistoryFetcher(_bridge(), cache_dir=tmp_path)
    df = pd.DataFrame(_make_rows(15))
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    fetcher.save_cache(df, "GBPUSD", "H1")
    loaded = fetcher.load_cache("GBPUSD", "H1")
    assert loaded is not None
    assert len(loaded) == 15


def test_load_cache_returns_none_when_missing(tmp_path):
    fetcher = HistoryFetcher(_bridge(), cache_dir=tmp_path)
    assert fetcher.load_cache("XAUUSD", "H1") is None


# ------------------------------------------------------------------ #
# Fallback behaviour                                                  #
# ------------------------------------------------------------------ #

def test_fetch_uses_synthetic_when_bridge_returns_empty(tmp_path):
    fetcher = HistoryFetcher(_bridge(rows=[]), cache_dir=tmp_path)
    df = fetcher.fetch("EURUSD", "H1", bars=50)
    assert len(df) == 50


def test_fetch_uses_synthetic_when_bridge_raises(tmp_path):
    fetcher = HistoryFetcher(_bridge(fail=True), cache_dir=tmp_path)
    df = fetcher.fetch("EURUSD", "H1", bars=30)
    assert len(df) == 30
    assert "close" in df.columns


def test_load_cache_returns_none_on_corrupt_parquet(tmp_path):
    fetcher = HistoryFetcher(_bridge(), cache_dir=tmp_path)
    bad = tmp_path / "EURUSD_H1.parquet"
    bad.write_bytes(b"this is not valid parquet")
    assert fetcher.load_cache("EURUSD", "H1") is None
