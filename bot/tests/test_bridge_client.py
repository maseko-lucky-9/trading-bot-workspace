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
