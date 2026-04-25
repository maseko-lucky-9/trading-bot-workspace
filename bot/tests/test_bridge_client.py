"""Unit tests for MT5BridgeClient (US-001)."""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from core.bridge.http_client import MT5BridgeClient


def _mock_transport(handler):
    return httpx.MockTransport(handler)


def test_ping_true_when_bridge_responds():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ping":
            return httpx.Response(200, json={"pong": True, "ea_connected": True, "time": 1})
        return httpx.Response(404)

    client = MT5BridgeClient()
    client._client = httpx.Client(transport=_mock_transport(handler), base_url="http://x")
    assert client.ping() is True
    assert client.is_connected() is True


def test_ping_false_on_connection_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = MT5BridgeClient()
    client._client = httpx.Client(transport=_mock_transport(handler), base_url="http://x")
    assert client.ping() is False


def test_get_tick_returns_dict():
    payload = {
        "tick": {"symbol": "EURUSD", "bid": 1.1, "ask": 1.10002, "spread": 2.0, "time": 1},
        "account": {},
        "connected": True,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/state":
            return httpx.Response(200, json=payload)
        return httpx.Response(404)

    client = MT5BridgeClient()
    client._client = httpx.Client(transport=_mock_transport(handler), base_url="http://x")
    tick = client.get_tick("EURUSD")
    assert tick["symbol"] == "EURUSD"
    assert tick["bid"] == 1.1


def test_send_order_posts_and_returns_payload():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/order":
            import json
            captured["body"] = json.loads(request.content.decode())
            return httpx.Response(200, json={"ok": True, "queued": 1})
        return httpx.Response(404)

    client = MT5BridgeClient()
    client._client = httpx.Client(transport=_mock_transport(handler), base_url="http://x")
    out = client.send_order({"action": "OPEN", "symbol": "EURUSD"})
    assert out["ok"] is True
    assert captured["body"]["action"] == "OPEN"


def test_is_connected_false_when_ea_not_connected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"pong": True, "ea_connected": False, "time": 1})

    client = MT5BridgeClient()
    client._client = httpx.Client(transport=_mock_transport(handler), base_url="http://x")
    assert client.is_connected() is False


def test_get_account_returns_account_dict():
    state = {
        "tick": {},
        "account": {"balance": 5000.0, "equity": 5000.0},
        "connected": True,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/state":
            return httpx.Response(200, json=state)
        return httpx.Response(404)

    client = MT5BridgeClient()
    client._client = httpx.Client(transport=_mock_transport(handler), base_url="http://x")
    acct = client.get_account()
    assert acct["balance"] == pytest.approx(5000.0)


def test_get_state_returns_full_dict():
    state = {"tick": {"bid": 1.1}, "account": {}, "connected": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/state":
            return httpx.Response(200, json=state)
        return httpx.Response(404)

    client = MT5BridgeClient()
    client._client = httpx.Client(transport=_mock_transport(handler), base_url="http://x")
    result = client.get_state()
    assert "tick" in result
    assert "connected" in result


def test_get_history_returns_bar_list():
    bars = [{"time": i, "open": 1.1, "high": 1.11, "low": 1.09, "close": 1.10, "volume": 100}
            for i in range(5)]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/history":
            return httpx.Response(200, json={"symbol": "EURUSD", "timeframe": "H1", "bars": bars})
        return httpx.Response(404)

    client = MT5BridgeClient()
    client._client = httpx.Client(transport=_mock_transport(handler), base_url="http://x")
    result = client.get_history("EURUSD", "H1", 5)
    assert len(result) == 5
    assert result[0]["close"] == pytest.approx(1.10)


def test_get_results_returns_empty_on_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("gone")

    client = MT5BridgeClient()
    client._client = httpx.Client(transport=_mock_transport(handler), base_url="http://x")
    assert client.get_results() == []


def test_get_tick_raises_bridge_disconnected_when_no_tick():
    """Empty tick in state should raise BridgeDisconnected."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/state":
            return httpx.Response(200, json={"tick": {}, "account": {}, "connected": True})
        return httpx.Response(404)

    from core.bridge.http_client import BridgeDisconnected
    client = MT5BridgeClient()
    client._client = httpx.Client(transport=_mock_transport(handler), base_url="http://x")
    with pytest.raises(BridgeDisconnected):
        client.get_tick("EURUSD")


def test_retry_exhaustion_reraises_connect_error():
    """After 3 ConnectError attempts _get should re-raise (not swallow)."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        raise httpx.ConnectError("unreachable")

    client = MT5BridgeClient()
    client._client = httpx.Client(transport=_mock_transport(handler), base_url="http://x")
    with pytest.raises(httpx.ConnectError):
        client.get_state()
    assert call_count == 3


def test_post_retry_exhaustion_reraises_connect_error():
    """After 3 ConnectError attempts _post should re-raise (not swallow)."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        raise httpx.ConnectError("unreachable")

    client = MT5BridgeClient()
    client._client = httpx.Client(transport=_mock_transport(handler), base_url="http://x")
    with pytest.raises(httpx.ConnectError):
        client.send_order({"action": "OPEN", "symbol": "EURUSD"})
    assert call_count == 3


def test_is_connected_returns_false_on_exception():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("gone")

    client = MT5BridgeClient()
    client._client = httpx.Client(transport=_mock_transport(handler), base_url="http://x")
    assert client.is_connected() is False


def test_close_does_not_raise():
    MT5BridgeClient().close()


def test_context_manager_enter_returns_self():
    client = MT5BridgeClient()
    with client as c:
        assert c is client
