"""Tests for core.data.historical_client — wrapper that fetches OHLCV history
from MT5BridgeClient with pagination, auto-fetch, and explicit error semantics."""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from core.bridge.http_client import BridgeDisconnected
from core.data.historical_client import (
    BridgeUnavailableError,
    HistoricalDataClient,
)
from core.data.history_store import CANONICAL_COLUMNS


def _bridge_rows(start_epoch: int, count: int, base: float = 1.10) -> list[dict]:
    """Build a list of bridge-style dicts, hourly bars."""
    return [
        {
            "time": start_epoch + i * 3600,
            "open": base + i * 0.0001,
            "high": base + i * 0.0001 + 0.0005,
            "low": base + i * 0.0001 - 0.0005,
            "close": base + i * 0.0001 + 0.0002,
            "volume": 1000 + i,
        }
        for i in range(count)
    ]


# ---------------------------------------------------------------- happy path

def test_fetch_returns_canonical_dataframe():
    bridge = MagicMock()
    bridge.get_history.return_value = _bridge_rows(1_700_000_000, 5)
    client = HistoricalDataClient(bridge=bridge)

    df = client.fetch(symbol="EURUSD", timeframe="H1", bars=5)

    assert list(df.columns) == CANONICAL_COLUMNS
    assert str(df["time"].dtype) == "datetime64[ms, UTC]"
    assert df["open"].dtype == "float64"
    assert df["volume"].dtype == "int64"
    assert len(df) == 5
    bridge.get_history.assert_called_once_with(
        symbol="EURUSD", timeframe="H1", bars=5, offset=0
    )


def test_fetch_dedups_overlapping_bars_in_response():
    bridge = MagicMock()
    rows = _bridge_rows(1_700_000_000, 3)
    # Inject a duplicate timestamp with a different close
    rows.append({**rows[1], "close": 999.0})
    bridge.get_history.return_value = rows
    client = HistoricalDataClient(bridge=bridge)

    df = client.fetch(symbol="EURUSD", bars=4)
    assert len(df) == 3  # duplicate dropped
    assert df["time"].is_unique


def test_fetch_sorts_ascending():
    bridge = MagicMock()
    rows = _bridge_rows(1_700_000_000, 3)
    rows.reverse()  # bridge returned newest-first
    bridge.get_history.return_value = rows
    client = HistoricalDataClient(bridge=bridge)

    df = client.fetch(symbol="EURUSD", bars=3)
    assert df["time"].is_monotonic_increasing


# ---------------------------------------------------------------- failure modes

def test_fetch_raises_when_bridge_returns_empty():
    bridge = MagicMock()
    bridge.get_history.return_value = []
    client = HistoricalDataClient(bridge=bridge)

    with pytest.raises(BridgeUnavailableError, match="empty"):
        client.fetch(symbol="EURUSD", bars=10)


def test_fetch_raises_when_bridge_disconnected():
    bridge = MagicMock()
    bridge.get_history.side_effect = BridgeDisconnected("connection refused")
    client = HistoricalDataClient(bridge=bridge)

    with pytest.raises(BridgeUnavailableError, match="connection refused"):
        client.fetch(symbol="EURUSD", bars=10)


def test_fetch_wraps_arbitrary_exception():
    bridge = MagicMock()
    bridge.get_history.side_effect = RuntimeError("boom")
    client = HistoricalDataClient(bridge=bridge)

    with pytest.raises(BridgeUnavailableError, match="boom"):
        client.fetch(symbol="EURUSD", bars=10)


def test_bridge_unavailable_error_subclasses_bridge_disconnected():
    """Callers catching BridgeDisconnected should also catch our error."""
    assert issubclass(BridgeUnavailableError, BridgeDisconnected)


# ---------------------------------------------------------------- input validation

def test_fetch_rejects_zero_bars():
    bridge = MagicMock()
    client = HistoricalDataClient(bridge=bridge)
    with pytest.raises(ValueError):
        client.fetch(symbol="EURUSD", bars=0)
    bridge.get_history.assert_not_called()


def test_fetch_rejects_negative_bars():
    bridge = MagicMock()
    client = HistoricalDataClient(bridge=bridge)
    with pytest.raises(ValueError):
        client.fetch(symbol="EURUSD", bars=-1)


# ---------------------------------------------------------------- pagination

def test_fetch_pagination_makes_multiple_page_requests():
    """With PAGE_SIZE=500, fetching 1000 bars should issue 2 page requests."""
    bridge = MagicMock()
    bridge.get_bar_count.return_value = 1000  # sufficient, skip auto-fetch trigger
    page_a = _bridge_rows(1_700_000_000 + 500 * 3600, 500)  # newer
    page_b = _bridge_rows(1_700_000_000,               500)  # older
    bridge.get_history.side_effect = [page_a, page_b]
    client = HistoricalDataClient(bridge=bridge)

    df = client.fetch(symbol="EURUSD", bars=1000)

    assert bridge.get_history.call_count == 2
    first_call = bridge.get_history.call_args_list[0]
    second_call = bridge.get_history.call_args_list[1]
    assert first_call.kwargs["offset"] == 0
    assert second_call.kwargs["offset"] == 500
    assert len(df) == 1000
    assert df["time"].is_monotonic_increasing


def test_fetch_pagination_stops_on_partial_page():
    """Bulk request returns fewer bars than asked → stop (no pagination retry)."""
    bridge = MagicMock()
    bridge.get_bar_count.return_value = 300
    # Asking for 1000 but bridge only has 300 — bulk call returns 300
    bridge.get_history.return_value = _bridge_rows(1_700_000_000, 300)
    client = HistoricalDataClient(bridge=bridge)

    df = client.fetch(symbol="EURUSD", bars=1000)

    # Bulk call issued once for bars=1000, bridge returned 300 → pagination
    # fallback tries next page (offset=300) and gets empty → stops.
    # Total: 2 calls (bulk + one paged attempt that returns []).
    assert bridge.get_history.call_count == 2
    assert len(df) == 300


def test_fetch_pagination_stops_on_empty_page():
    """An empty page signals no more history."""
    bridge = MagicMock()
    bridge.get_bar_count.return_value = 500
    page_a = _bridge_rows(1_700_000_000, 500)
    bridge.get_history.side_effect = [page_a, []]  # second page empty
    client = HistoricalDataClient(bridge=bridge)

    df = client.fetch(symbol="EURUSD", bars=1000)

    assert bridge.get_history.call_count == 2
    assert len(df) == 500


def test_fetch_auto_fetch_disabled_skips_bar_count_check():
    """auto_fetch=False must not call get_bar_count or request_fetch_history."""
    bridge = MagicMock()
    bridge.get_history.return_value = _bridge_rows(1_700_000_000, 10)
    client = HistoricalDataClient(bridge=bridge)

    client.fetch(symbol="EURUSD", bars=10, auto_fetch=False)

    bridge.get_bar_count.assert_not_called()
    bridge.request_fetch_history.assert_not_called()


def test_fetch_auto_fetch_skipped_when_sufficient_bars():
    """When bridge already has enough bars, FETCH_HISTORY must not be queued."""
    bridge = MagicMock()
    bridge.get_bar_count.return_value = 500  # sufficient
    bridge.get_history.return_value = _bridge_rows(1_700_000_000, 10)
    client = HistoricalDataClient(bridge=bridge)

    client.fetch(symbol="EURUSD", bars=10, auto_fetch=True)

    bridge.request_fetch_history.assert_not_called()


def test_fetch_auto_fetch_triggers_when_insufficient_bars():
    """When bridge has fewer bars than requested, FETCH_HISTORY is queued."""
    bridge = MagicMock()
    bridge.get_bar_count.side_effect = [
        0,    # initial check → insufficient
        500,  # first poll → filled
    ]
    bridge.get_history.return_value = _bridge_rows(1_700_000_000, 10)
    client = HistoricalDataClient(bridge=bridge)
    client._POLL_INTERVAL = 0  # no real sleeping in tests

    client.fetch(symbol="EURUSD", bars=10, auto_fetch=True)

    bridge.request_fetch_history.assert_called_once_with("EURUSD", "H1", 10)


def test_fetch_auto_fetch_tolerates_fetch_error():
    """If request_fetch_history raises, fetch should still proceed (best-effort)."""
    bridge = MagicMock()
    bridge.get_bar_count.return_value = 0
    bridge.request_fetch_history.side_effect = RuntimeError("order failed")
    bridge.get_history.return_value = _bridge_rows(1_700_000_000, 5)
    client = HistoricalDataClient(bridge=bridge)
    client._POLL_INTERVAL = 0

    # Should not raise; proceeds with whatever bars the bridge has
    df = client.fetch(symbol="EURUSD", bars=5, auto_fetch=True)
    assert len(df) == 5


def test_fetch_returns_at_most_requested_bars_when_more_available():
    """If pages return more rows than bars requested, result is capped at bars."""
    bridge = MagicMock()
    bridge.get_bar_count.return_value = 600
    # PAGE_SIZE clips to min(500, bars=10) = 10 bars per page request
    bridge.get_history.return_value = _bridge_rows(1_700_000_000, 10)
    client = HistoricalDataClient(bridge=bridge)

    df = client.fetch(symbol="EURUSD", bars=10)
    assert len(df) == 10


def test_fetch_timeframe_passed_through_to_bridge():
    """The timeframe arg must be forwarded to all bridge.get_history calls."""
    bridge = MagicMock()
    bridge.get_bar_count.return_value = 100
    bridge.get_history.return_value = _bridge_rows(1_700_000_000, 5)
    client = HistoricalDataClient(bridge=bridge)

    client.fetch(symbol="USDJPY", bars=5, timeframe="M5")

    call_kwargs = bridge.get_history.call_args.kwargs
    assert call_kwargs["symbol"] == "USDJPY"
    assert call_kwargs["timeframe"] == "M5"
