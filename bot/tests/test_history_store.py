"""Tests for core.data.history_store — parquet I/O + prefer-existing merge."""
from __future__ import annotations

import pandas as pd
import pytest

from core.data.history_store import (
    CANONICAL_COLUMNS,
    SchemaMismatchError,
    coerce_schema,
    merge_prefer_existing,
    read_existing,
    write_atomic,
)


def _make_df(times: list[str], close_base: float = 100.0) -> pd.DataFrame:
    """Build a canonical-schema DataFrame from ISO timestamps."""
    rows = []
    for i, t in enumerate(times):
        c = close_base + i * 0.1
        rows.append(
            {
                "time": pd.Timestamp(t, tz="UTC"),
                "open": c - 0.05,
                "high": c + 0.10,
                "low": c - 0.10,
                "close": c,
                "volume": 1000 + i,
            }
        )
    df = pd.DataFrame(rows, columns=CANONICAL_COLUMNS)
    return coerce_schema(df)


# ---------------------------------------------------------------- coerce_schema

def test_coerce_schema_normalizes_dtypes():
    df = pd.DataFrame(
        {
            "time": [1745539200, 1745542800],  # epoch seconds
            "open": [1.1, 1.2],
            "high": [1.15, 1.25],
            "low": [1.05, 1.15],
            "close": [1.12, 1.22],
            "volume": [500, 600],
        }
    )
    out = coerce_schema(df)
    assert list(out.columns) == CANONICAL_COLUMNS
    assert str(out["time"].dtype) == "datetime64[ms, UTC]"
    assert out["open"].dtype == "float64"
    assert out["volume"].dtype == "int64"
    assert out["time"].is_monotonic_increasing


def test_coerce_schema_rejects_missing_columns():
    df = pd.DataFrame({"time": [0], "open": [1.0]})
    with pytest.raises(SchemaMismatchError):
        coerce_schema(df)


# ---------------------------------------------------------------- merge_prefer_existing

def test_merge_prefer_existing_keeps_cached_on_overlap():
    cached = _make_df(["2026-01-01T00:00:00", "2026-01-01T01:00:00"], close_base=100.0)
    fetched = _make_df(["2026-01-01T01:00:00", "2026-01-01T02:00:00"], close_base=999.0)
    merged = merge_prefer_existing(cached, fetched)
    # 3 unique timestamps
    assert len(merged) == 3
    # Cached row at 01:00 wins (close_base 100.0 + 0.1 = 100.1, NOT 999.1)
    overlap = merged[merged["time"] == pd.Timestamp("2026-01-01T01:00:00", tz="UTC")]
    assert overlap["close"].iloc[0] == pytest.approx(100.1)
    # New row at 02:00 from fetched
    new_row = merged[merged["time"] == pd.Timestamp("2026-01-01T02:00:00", tz="UTC")]
    assert new_row["close"].iloc[0] == pytest.approx(999.1)


def test_merge_prefer_existing_with_empty_cached():
    cached = pd.DataFrame(columns=CANONICAL_COLUMNS)
    fetched = _make_df(["2026-01-01T00:00:00", "2026-01-01T01:00:00"])
    merged = merge_prefer_existing(cached, fetched)
    assert len(merged) == 2


def test_merge_prefer_existing_with_empty_fetched():
    cached = _make_df(["2026-01-01T00:00:00", "2026-01-01T01:00:00"])
    fetched = pd.DataFrame(columns=CANONICAL_COLUMNS)
    merged = merge_prefer_existing(cached, fetched)
    assert len(merged) == 2


def test_merge_result_is_sorted_and_unique():
    cached = _make_df(["2026-01-01T02:00:00", "2026-01-01T00:00:00"])
    fetched = _make_df(["2026-01-01T01:00:00", "2026-01-01T03:00:00"])
    merged = merge_prefer_existing(cached, fetched)
    assert merged["time"].is_monotonic_increasing
    assert merged["time"].is_unique


# ---------------------------------------------------------------- read_existing

def test_read_existing_returns_none_when_missing(tmp_path):
    assert read_existing(tmp_path / "GBPUSD_H1.parquet") is None


def test_read_existing_roundtrip(tmp_path):
    p = tmp_path / "EURUSD_H1.parquet"
    df = _make_df(["2026-01-01T00:00:00", "2026-01-01T01:00:00"])
    df.to_parquet(p, index=False)
    out = read_existing(p)
    assert out is not None
    assert list(out.columns) == CANONICAL_COLUMNS
    assert len(out) == 2


# ---------------------------------------------------------------- write_atomic

def test_write_atomic_creates_parquet_and_no_tmp_left(tmp_path):
    p = tmp_path / "USDJPY_H1.parquet"
    df = _make_df(["2026-01-01T00:00:00"])
    write_atomic(df, p)
    assert p.exists()
    # No leftover .tmp file
    assert not (p.with_suffix(p.suffix + ".tmp")).exists()
    # Roundtrip preserves schema
    rt = pd.read_parquet(p)
    assert list(rt.columns) == CANONICAL_COLUMNS


def test_write_atomic_overwrites_existing(tmp_path):
    p = tmp_path / "EURUSD_H1.parquet"
    write_atomic(_make_df(["2026-01-01T00:00:00"]), p)
    write_atomic(_make_df(["2026-01-01T00:00:00", "2026-01-01T01:00:00"]), p)
    rt = pd.read_parquet(p)
    assert len(rt) == 2


def test_write_atomic_rejects_unsorted_or_duplicate(tmp_path):
    p = tmp_path / "EURUSD_H1.parquet"
    bad = pd.DataFrame(
        {
            "time": pd.to_datetime([0, 0], unit="s", utc=True).astype("datetime64[ms, UTC]"),
            "open": [1.0, 1.0],
            "high": [1.0, 1.0],
            "low": [1.0, 1.0],
            "close": [1.0, 1.0],
            "volume": [1, 1],
        }
    )
    with pytest.raises(SchemaMismatchError):
        write_atomic(bad, p)
