"""Tests for the FastAPI bridge server (core/bridge/http_server.py)."""
from __future__ import annotations

import time

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
        srv._h1_bars.clear()
        srv._h1_open_bar.clear()
    yield


@pytest.fixture
def client():
    return TestClient(app)


# ------------------------------------------------------------------ #
# Connectivity                                                        #
# ------------------------------------------------------------------ #

def test_ping_returns_pong(client):
    r = client.get("/ping")
    assert r.status_code == 200
    assert r.json()["pong"] is True


def test_ping_ea_connected_false_without_heartbeat(client):
    r = client.get("/ping")
    assert r.json()["ea_connected"] is False


def test_heartbeat_marks_ea_connected(client):
    client.post("/heartbeat")
    r = client.get("/ping")
    assert r.json()["ea_connected"] is True


# ------------------------------------------------------------------ #
# EA → Server: tick and account pushes                               #
# ------------------------------------------------------------------ #

def test_push_tick_stored_in_state(client):
    payload = {"symbol": "EURUSD", "bid": 1.10000, "ask": 1.10020, "spread": 2.0, "time": 1700000000}
    r = client.post("/tick", json=payload)
    assert r.json()["ok"] is True

    state = client.get("/state").json()
    assert state["tick"]["symbol"] == "EURUSD"
    assert state["tick"]["bid"] == pytest.approx(1.10000)


def test_push_tick_updates_heartbeat(client):
    client.post("/tick", json={"symbol": "EURUSD", "bid": 1.1, "ask": 1.1002, "spread": 2.0, "time": 0})
    r = client.get("/ping")
    assert r.json()["ea_connected"] is True


def test_push_account_stored_in_state(client):
    payload = {
        "balance": 10000.0, "equity": 10000.0, "margin": 100.0,
        "free_margin": 9900.0, "profit": 0.0, "leverage": 100,
        "currency": "USD", "server": "Demo",
    }
    client.post("/account", json=payload)
    state = client.get("/state").json()
    assert state["account"]["balance"] == pytest.approx(10000.0)
    assert state["account"]["currency"] == "USD"


# ------------------------------------------------------------------ #
# Order command flow: bot → server → EA                              #
# ------------------------------------------------------------------ #

def test_send_order_returns_queued(client):
    r = client.post("/order", json={"action": "OPEN", "symbol": "EURUSD", "volume": 0.01})
    assert r.json()["ok"] is True
    assert r.json()["queued"] == 1


def test_get_command_consumes_order(client):
    client.post("/order", json={"action": "OPEN", "symbol": "EURUSD", "volume": 0.01})
    cmd = client.get("/command").json()
    assert cmd["action"] == "OPEN"
    assert cmd["symbol"] == "EURUSD"


def test_get_command_returns_none_when_empty(client):
    cmd = client.get("/command").json()
    assert cmd["action"] == "NONE"


def test_command_queue_fifo(client):
    client.post("/order", json={"action": "OPEN", "symbol": "EURUSD", "volume": 0.01})
    client.post("/order", json={"action": "CLOSE", "ticket": 42})
    assert client.get("/command").json()["action"] == "OPEN"
    assert client.get("/command").json()["action"] == "CLOSE"
    assert client.get("/command").json()["action"] == "NONE"


# ------------------------------------------------------------------ #
# Trade result flow: EA → server → bot                               #
# ------------------------------------------------------------------ #

def test_push_result_retrievable(client):
    client.post("/result", json={"action": "OPEN", "success": True, "ticket": 99})
    results = client.get("/results").json()
    assert len(results) == 1
    assert results[0]["ticket"] == 99


def test_get_results_clears_log(client):
    client.post("/result", json={"action": "OPEN", "success": True, "ticket": 1})
    client.get("/results")
    assert client.get("/results").json() == []


# ------------------------------------------------------------------ #
# History endpoint                                                   #
# ------------------------------------------------------------------ #

def test_get_history_returns_correct_bar_count(client):
    r = client.get("/history?symbol=EURUSD&timeframe=H1&bars=50")
    assert r.status_code == 200
    data = r.json()
    assert data["symbol"] == "EURUSD"
    assert len(data["bars"]) == 50


def test_get_history_bars_have_ohlcv(client):
    r = client.get("/history?symbol=EURUSD&timeframe=H1&bars=10")
    bar = r.json()["bars"][0]
    for key in ("time", "open", "high", "low", "close", "volume"):
        assert key in bar


def test_get_history_high_gte_low(client):
    bars = client.get("/history?symbol=EURUSD&timeframe=H1&bars=20").json()["bars"]
    for bar in bars:
        assert bar["high"] >= bar["low"]


def test_get_history_different_symbols_differ(client):
    eu = client.get("/history?symbol=EURUSD&bars=50").json()["bars"]
    gb = client.get("/history?symbol=GBPUSD&bars=50").json()["bars"]
    assert eu[0]["close"] != gb[0]["close"]


def test_get_history_time_ascending(client):
    bars = client.get("/history?symbol=EURUSD&bars=20").json()["bars"]
    times = [b["time"] for b in bars]
    assert times == sorted(times)


# ------------------------------------------------------------------ #
# State endpoint                                                     #
# ------------------------------------------------------------------ #

def test_state_connected_reflects_heartbeat_age(client):
    state = client.get("/state").json()
    assert state["connected"] is False

    client.post("/heartbeat")
    state = client.get("/state").json()
    assert state["connected"] is True


# ------------------------------------------------------------------ #
# Real H1 history accumulation                                       #
# ------------------------------------------------------------------ #

def _tick_with_ohlcv(client, symbol="EURUSD", bar_time=1_700_000_000,
                     h1_open=1.1000, h1_high=1.1050, h1_low=1.0950, h1_close=1.1020,
                     bid=1.1020, ask=1.1022):
    """Push a tick that carries H1 OHLCV data."""
    # bar_time must be H1-aligned
    aligned = bar_time - (bar_time % 3600)
    tick_time = aligned + 60  # 1 minute into the bar
    return client.post("/tick", json={
        "symbol": symbol, "bid": bid, "ask": ask,
        "spread": 2.0, "time": tick_time, "volume": 1000,
        "h1_open": h1_open, "h1_high": h1_high,
        "h1_low": h1_low, "h1_close": h1_close,
    })


def test_history_source_synthetic_before_live_ticks(client):
    r = client.get("/history?symbol=EURUSD&timeframe=H1&bars=10")
    assert r.json()["source"] == "synthetic"


def test_history_source_live_after_tick_push(client):
    # After pushing a tick with OHLCV, source should include live data.
    # When bars > live_count the endpoint pads with synthetic, so accept
    # both "live" (bars <= live_count) and "live+synthetic" (padded).
    _tick_with_ohlcv(client)
    r = client.get("/history?symbol=EURUSD&timeframe=H1&bars=10")
    assert r.json()["source"] in ("live", "live+synthetic")


def test_history_live_bar_ohlcv_matches_pushed_tick(client):
    _tick_with_ohlcv(client, h1_open=1.10, h1_high=1.11, h1_low=1.09, h1_close=1.105)
    data = client.get("/history?symbol=EURUSD&timeframe=H1&bars=10").json()
    bar = data["bars"][-1]
    assert bar["open"]  == pytest.approx(1.10, abs=1e-5)
    assert bar["high"]  == pytest.approx(1.11, abs=1e-5)
    assert bar["low"]   == pytest.approx(1.09, abs=1e-5)
    assert bar["close"] == pytest.approx(1.105, abs=1e-5)


def test_history_high_updated_on_new_tick_same_bar(client):
    """Second tick in same H1 bar should update high if new high is greater."""
    bar_time = 1_700_000_000 - (1_700_000_000 % 3600)
    _tick_with_ohlcv(client, bar_time=bar_time, h1_high=1.1050)
    # Push again with higher high in same bar
    client.post("/tick", json={
        "symbol": "EURUSD", "bid": 1.108, "ask": 1.1082,
        "spread": 2.0, "time": bar_time + 120, "volume": 500,
        "h1_open": 1.10, "h1_high": 1.1090, "h1_low": 1.09, "h1_close": 1.108,
    })
    data = client.get("/history?symbol=EURUSD&timeframe=H1&bars=5").json()
    bar = data["bars"][-1]
    assert bar["high"] == pytest.approx(1.1090, abs=1e-5)


def test_history_new_bar_seals_previous(client):
    """Tick in a new H1 bar should seal the previous bar into the buffer."""
    bar1_time = 1_700_000_000 - (1_700_000_000 % 3600)
    bar2_time = bar1_time + 3600
    _tick_with_ohlcv(client, bar_time=bar1_time, h1_close=1.105)
    # Push tick in next bar
    client.post("/tick", json={
        "symbol": "EURUSD", "bid": 1.106, "ask": 1.1062,
        "spread": 2.0, "time": bar2_time + 60, "volume": 200,
        "h1_open": 1.105, "h1_high": 1.107, "h1_low": 1.104, "h1_close": 1.106,
    })
    data = client.get("/history?symbol=EURUSD&timeframe=H1&bars=10").json()
    # Both bars must appear in the result (padded with synthetic when needed).
    # Check the last 2 bars match the expected live-bar times.
    assert len(data["bars"]) == 10  # always returns exactly bars= count
    assert data["bars"][-2]["time"] == bar1_time
    assert data["bars"][-1]["time"] == bar2_time


def test_history_bars_capped_at_requested_count(client):
    _tick_with_ohlcv(client)
    data = client.get("/history?symbol=EURUSD&timeframe=H1&bars=1").json()
    assert len(data["bars"]) <= 1


def test_history_no_ohlcv_tick_leaves_source_synthetic(client):
    """A tick without H1 OHLCV fields should not create real history."""
    client.post("/tick", json={
        "symbol": "EURUSD", "bid": 1.10, "ask": 1.1002,
        "spread": 2.0, "time": 1_700_000_000, "volume": 0,
    })
    r = client.get("/history?symbol=EURUSD&timeframe=H1&bars=5")
    assert r.json()["source"] == "synthetic"
