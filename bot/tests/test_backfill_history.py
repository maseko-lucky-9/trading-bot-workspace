"""Tests for scripts.backfill_history — CLI top-up logic with mocked bridge."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from core.data.historical_client import BridgeUnavailableError, HistoricalDataClient
from core.data.history_store import CANONICAL_COLUMNS, coerce_schema, write_atomic
from scripts.backfill_history import (
    backfill_one,
    main,
    parse_args,
    resolve_symbols,
)


def _seed_parquet(tmp_path: Path, symbol: str, n_bars: int, start_epoch: int = 1_700_000_000) -> Path:
    """Write a canonical parquet with ``n_bars`` synthetic rows."""
    rows = [
        {
            "time": start_epoch + i * 3600,
            "open": 1.10 + i * 0.0001,
            "high": 1.11 + i * 0.0001,
            "low": 1.09 + i * 0.0001,
            "close": 1.105 + i * 0.0001,
            "volume": 1000 + i,
        }
        for i in range(n_bars)
    ]
    df = coerce_schema(pd.DataFrame(rows, columns=CANONICAL_COLUMNS))
    p = tmp_path / f"{symbol}_H1.parquet"
    write_atomic(df, p)
    return p


def _bridge_rows(start_epoch: int, count: int) -> list[dict]:
    return [
        {
            "time": start_epoch + i * 3600,
            "open": 2.0 + i * 0.0001,
            "high": 2.01 + i * 0.0001,
            "low": 1.99 + i * 0.0001,
            "close": 2.005 + i * 0.0001,
            "volume": 5000 + i,
        }
        for i in range(count)
    ]


# ---------------------------------------------------------------- backfill_one

def test_backfill_one_noop_when_target_already_met(tmp_path):
    _seed_parquet(tmp_path, "EURUSD", n_bars=5000)
    bridge = MagicMock()  # should never be called

    result = backfill_one(
        symbol="EURUSD",
        target=5000,
        timeframe="H1",
        cache_dir=tmp_path,
        bridge=bridge,
    )

    assert result["fetched"] == 0
    assert result["cached_after"] == 5000
    assert result["status"] == "noop"
    bridge.get_history.assert_not_called()


def test_backfill_one_fetches_gap_when_under_target(tmp_path):
    _seed_parquet(tmp_path, "GBPUSD", n_bars=200)
    # Bridge returns 5000 rows starting at a much earlier epoch (so prefer-existing
    # keeps cached rows where they overlap, fetched fills the rest).
    bridge = MagicMock()
    bridge.get_history.return_value = _bridge_rows(
        start_epoch=1_700_000_000 - 5000 * 3600, count=5000
    )

    result = backfill_one(
        symbol="GBPUSD",
        target=5000,
        timeframe="H1",
        cache_dir=tmp_path,
        bridge=bridge,
    )

    assert result["status"] == "fetched"
    assert result["cached_before"] == 200
    assert result["cached_after"] >= 5000
    bridge.get_history.assert_called_once()
    # bulk request: first call asks for the full target count at offset=0
    _, kwargs = bridge.get_history.call_args
    assert kwargs["bars"] == 5000
    assert kwargs["offset"] == 0


def test_backfill_one_starts_from_zero_when_no_cache(tmp_path):
    bridge = MagicMock()
    bridge.get_history.return_value = _bridge_rows(1_700_000_000, 5000)

    result = backfill_one(
        symbol="USDJPY",
        target=5000,
        timeframe="H1",
        cache_dir=tmp_path,
        bridge=bridge,
    )

    assert result["cached_before"] == 0
    assert result["cached_after"] == 5000
    assert (tmp_path / "USDJPY_H1.parquet").exists()


def test_backfill_one_propagates_bridge_failure(tmp_path):
    _seed_parquet(tmp_path, "EURUSD", n_bars=200)
    bridge = MagicMock()
    bridge.get_history.side_effect = BridgeUnavailableError("bridge down")

    with pytest.raises(BridgeUnavailableError):
        backfill_one(
            symbol="EURUSD",
            target=5000,
            timeframe="H1",
            cache_dir=tmp_path,
            bridge=bridge,
        )


def test_backfill_one_preserves_cached_on_overlap(tmp_path):
    """Cached close prices must survive a backfill that overlaps timestamps."""
    p = _seed_parquet(tmp_path, "EURUSD", n_bars=200)
    cached_close = pd.read_parquet(p)["close"].iloc[100]

    bridge = MagicMock()
    # Bridge returns conflicting closes for the overlapping window.
    bridge.get_history.return_value = _bridge_rows(1_700_000_000, 5000)
    backfill_one(
        symbol="EURUSD",
        target=5000,
        timeframe="H1",
        cache_dir=tmp_path,
        bridge=bridge,
    )

    after = pd.read_parquet(p)
    overlap_row = after[after["time"] == pd.Timestamp(1_700_000_000 + 100 * 3600, unit="s", tz="UTC")]
    assert overlap_row["close"].iloc[0] == pytest.approx(cached_close)


# ---------------------------------------------------------------- resolve_symbols

def test_resolve_symbols_uses_cli_when_provided(tmp_path):
    cfg = {"bot": {"instruments": ["USDJPY"]}}
    assert resolve_symbols(cli_symbols="EURUSD,GBPUSD", config=cfg) == [
        "EURUSD",
        "GBPUSD",
    ]


def test_resolve_symbols_falls_back_to_config():
    cfg = {"bot": {"instruments": ["USDJPY"]}}
    assert resolve_symbols(cli_symbols=None, config=cfg) == ["USDJPY"]


def test_resolve_symbols_strips_whitespace():
    cfg = {"bot": {"instruments": ["USDJPY"]}}
    assert resolve_symbols(cli_symbols=" EURUSD , GBPUSD ", config=cfg) == [
        "EURUSD",
        "GBPUSD",
    ]


def test_resolve_symbols_raises_when_neither_provided():
    with pytest.raises(ValueError):
        resolve_symbols(cli_symbols=None, config={"bot": {}})


# ---------------------------------------------------------------- parse_args

def test_parse_args_defaults():
    args = parse_args([])
    assert args.target == 5000
    assert args.timeframe == "H1"
    assert args.symbols is None


def test_parse_args_overrides():
    args = parse_args(["--target", "3000", "--symbols", "EURUSD,GBPUSD"])
    assert args.target == 3000
    assert args.symbols == "EURUSD,GBPUSD"


# ---------------------------------------------------------------- main (CLI)

def test_main_returns_zero_on_success(tmp_path, monkeypatch):
    _seed_parquet(tmp_path, "EURUSD", n_bars=5000)
    bridge = MagicMock()

    with patch("scripts.backfill_history._build_bridge", return_value=bridge):
        rc = main(
            argv=[
                "--target", "5000",
                "--symbols", "EURUSD",
                "--cache-dir", str(tmp_path),
            ]
        )

    assert rc == 0


def test_main_returns_nonzero_on_bridge_failure(tmp_path):
    bridge = MagicMock()
    bridge.get_history.side_effect = BridgeUnavailableError("bridge down")

    with patch("scripts.backfill_history._build_bridge", return_value=bridge):
        rc = main(
            argv=[
                "--target", "5000",
                "--symbols", "EURUSD",
                "--cache-dir", str(tmp_path),
            ]
        )

    assert rc != 0
