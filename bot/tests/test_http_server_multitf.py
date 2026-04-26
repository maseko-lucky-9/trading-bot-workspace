"""Tests for multi-timeframe storage, /history-batch, and pagination in http_server.py."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

import core.bridge.http_server as srv
from core.bridge.http_server import app


@pytest.fixture(autouse=True)
def reset_server_state():
    """Reset all global server state before each test."""
    with srv._lock:
        srv._state["tick"] = {}
        srv._state["account"] = {}
        srv._state["heartbeat"] = 0
        srv._state["positions"] = []
        srv._command_queue.clear()
        srv._result_log.clear()
        srv._tf_bars.clear()
        srv._tf_open_bar.clear()
    yield


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------- /history-batch

def test_history_batch_ingests_bars_and_returns_counts(client):
    bars = [
        {"time": 1700000000, "open": 1.10, "high": 1.11, "low": 1.09, "close": 1.105, "volume": 500},
        {"time": 1700003600, "open": 1.105, "high": 1.115, "low": 1.095, "close": 1.110, "volume": 600},
    ]
    r = client.post("/history-batch", json={"symbol": "EURUSD", "timeframe": "H1", "bars": bars})
    assert r.status_code == 200
    data = r.json()
    assert data["ingested"] == 2
    assert data["total"] == 2


def test_history_batch_serves_real_bars_on_get(client):
    bars = [{"time": 1700000000, "open": 1.10, "high": 1.11, "low": 1.09, "close": 1.105, "volume": 100}]
    client.post("/history-batch", json={"symbol": "EURUSD", "timeframe": "M5", "bars": bars})

    r = client.get("/history?symbol=EURUSD&timeframe=M5&bars=1")
    data = r.json()
    assert data["source"] in ("live", "live+synthetic")
    assert data["bars"][-1]["open"] == pytest.approx(1.10, abs=1e-5)


def test_history_batch_deduplicates_prefer_incoming(client):
    original = [{"time": 1700000000, "open": 1.10, "high": 1.11, "low": 1.09, "close": 1.105, "volume": 100}]
    client.post("/history-batch", json={"symbol": "EURUSD", "timeframe": "H1", "bars": original})
    # Post again with different close for same timestamp
    updated = [{"time": 1700000000, "open": 1.10, "high": 1.12, "low": 1.08, "close": 2.000, "volume": 200}]
    client.post("/history-batch", json={"symbol": "EURUSD", "timeframe": "H1", "bars": updated})

    r = client.get("/history?symbol=EURUSD&timeframe=H1&bars=1")
    bar = r.json()["bars"][-1]
    assert bar["close"] == pytest.approx(2.000, abs=1e-5)  # incoming wins


def test_history_batch_unknown_timeframe_returns_400(client):
    bars = [{"time": 1700000000, "open": 1.1, "high": 1.1, "low": 1.1, "close": 1.1, "volume": 0}]
    r = client.post("/history-batch", json={"symbol": "EURUSD", "timeframe": "W1", "bars": bars})
    assert r.status_code == 400


def test_history_batch_empty_bars_returns_zero(client):
    r = client.post("/history-batch", json={"symbol": "EURUSD", "timeframe": "M5", "bars": []})
    assert r.status_code == 200
    assert r.json() == {"ingested": 0, "total": 0}


def test_history_batch_respects_maxlen(client):
    """Ingesting more bars than _TF_MAX_BARS[D1]=500 should cap at 500 (most recent)."""
    bars = [
        {"time": 1700000000 + i * 86400, "open": 1.1, "high": 1.1, "low": 1.1,
         "close": 1.1, "volume": i}
        for i in range(600)
    ]
    r = client.post("/history-batch", json={"symbol": "EURUSD", "timeframe": "D1", "bars": bars})
    assert r.json()["total"] == 500


def test_history_batch_h1_and_m5_stored_independently(client):
    h1_bars = [{"time": 1700000000, "open": 1.1, "high": 1.1, "low": 1.1, "close": 1.1, "volume": 1}]
    m5_bars = [{"time": 1700000000, "open": 2.2, "high": 2.2, "low": 2.2, "close": 2.2, "volume": 2}]
    client.post("/history-batch", json={"symbol": "EURUSD", "timeframe": "H1", "bars": h1_bars})
    client.post("/history-batch", json={"symbol": "EURUSD", "timeframe": "M5", "bars": m5_bars})

    h1 = client.get("/history?symbol=EURUSD&timeframe=H1&bars=1").json()["bars"][-1]
    m5 = client.get("/history?symbol=EURUSD&timeframe=M5&bars=1").json()["bars"][-1]
    assert h1["close"] == pytest.approx(1.1, abs=1e-5)
    assert m5["close"] == pytest.approx(2.2, abs=1e-5)


# ---------------------------------------------------------------- /history pagination

def _seed_bars(client, symbol, timeframe, count, base_time=1_700_000_000, step=3600):
    """Helper: ingest `count` bars via /history-batch."""
    bars = [
        {"time": base_time + i * step, "open": 1.1 + i * 0.0001,
         "high": 1.1 + i * 0.0001 + 0.0005, "low": 1.1 + i * 0.0001 - 0.0005,
         "close": 1.1 + i * 0.0001 + 0.0002, "volume": i + 1}
        for i in range(count)
    ]
    client.post("/history-batch", json={"symbol": symbol, "timeframe": timeframe, "bars": bars})
    return bars


def test_history_total_available_in_response(client):
    _seed_bars(client, "EURUSD", "H1", 50)
    r = client.get("/history?symbol=EURUSD&timeframe=H1&bars=10").json()
    assert r["total_available"] == 50


def test_history_offset_skips_last_n_bars(client):
    _seed_bars(client, "EURUSD", "H1", 20)
    # Without offset: last 5 bars
    no_offset = client.get("/history?symbol=EURUSD&timeframe=H1&bars=5").json()["bars"]
    # With offset=5: bars 10-14 (5 bars before the last 5)
    with_offset = client.get("/history?symbol=EURUSD&timeframe=H1&bars=5&offset=5").json()["bars"]
    assert len(with_offset) == 5
    assert with_offset[-1]["time"] < no_offset[0]["time"]


def test_history_offset_pagination_mode_no_synthetic_padding(client):
    """With offset > 0, bridge must NOT pad short results with synthetic bars."""
    _seed_bars(client, "EURUSD", "H1", 10)
    # Ask for 20 bars but only 10 available after offset=5 → 5 real bars only
    r = client.get("/history?symbol=EURUSD&timeframe=H1&bars=20&offset=5").json()
    assert r["source"] == "live"
    assert len(r["bars"]) == 5  # only what's available, no synthetic padding


def test_history_from_time_filters_by_timestamp(client):
    _seed_bars(client, "EURUSD", "H1", 20)  # times: T, T+3600, ..., T+19*3600
    cutoff = 1_700_000_000 + 9 * 3600  # bar 10 boundary
    r = client.get(f"/history?symbol=EURUSD&timeframe=H1&bars=20&from_time={cutoff}").json()
    assert all(b["time"] <= cutoff for b in r["bars"])
    assert r["total_available"] == 10


def test_history_from_time_pagination_mode_no_padding(client):
    _seed_bars(client, "EURUSD", "H1", 5)
    cutoff = 1_700_000_000 + 2 * 3600  # only 3 bars qualify
    r = client.get(f"/history?symbol=EURUSD&timeframe=H1&bars=100&from_time={cutoff}").json()
    assert r["source"] == "live"
    assert len(r["bars"]) == 3


def test_history_offset_beyond_available_returns_empty(client):
    _seed_bars(client, "EURUSD", "H1", 10)
    r = client.get("/history?symbol=EURUSD&timeframe=H1&bars=5&offset=15").json()
    assert r["source"] == "live"
    assert r["bars"] == []


def test_history_pagination_ascending_order(client):
    _seed_bars(client, "EURUSD", "H1", 20)
    r = client.get("/history?symbol=EURUSD&timeframe=H1&bars=10&offset=5").json()
    times = [b["time"] for b in r["bars"]]
    assert times == sorted(times)


# ---------------------------------------------------------------- FETCH_HISTORY command shape

def test_fetch_history_command_enqueued_correctly(client):
    """POST /order with FETCH_HISTORY action must be retrievable by EA via GET /command."""
    cmd = {"action": "FETCH_HISTORY", "symbol": "EURUSD", "timeframe": "M5", "count": 5000}
    client.post("/order", json=cmd)
    received = client.get("/command").json()
    assert received["action"] == "FETCH_HISTORY"
    assert received["symbol"] == "EURUSD"
    assert received["timeframe"] == "M5"
    assert received["count"] == 5000


def test_fetch_history_command_does_not_conflict_with_trade_commands(client):
    """Trade commands and FETCH_HISTORY share the same queue — FIFO ordering preserved."""
    client.post("/order", json={"action": "BUY", "symbol": "EURUSD", "volume": 0.01})
    client.post("/order", json={"action": "FETCH_HISTORY", "symbol": "GBPUSD", "timeframe": "H1", "count": 1000})
    assert client.get("/command").json()["action"] == "BUY"
    assert client.get("/command").json()["action"] == "FETCH_HISTORY"
    assert client.get("/command").json()["action"] == "NONE"


# ---------------------------------------------------------------- /history total_available for synthetic fallback

def test_history_synthetic_total_available_is_zero(client):
    r = client.get("/history?symbol=EURUSD&timeframe=M5&bars=10").json()
    assert r["source"] == "synthetic"
    assert r["total_available"] == 0
