"""Tests for LiveBroker."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.execution.live_broker import LiveBroker, LiveModeNotEnabled


@pytest.fixture
def connected_bridge():
    b = MagicMock()
    b.is_connected.return_value = True
    b.get_account.return_value = {"balance": 5000.0, "equity": 5000.0}
    b.get_state.return_value = {"positions": []}
    b.get_results.return_value = []
    b.send_order.return_value = {"ok": True, "ticket": 42}
    return b


def test_raises_when_ea_not_connected():
    b = MagicMock()
    b.is_connected.return_value = False
    with pytest.raises(LiveModeNotEnabled, match="EA not connected"):
        LiveBroker(b)


def test_raises_when_equity_below_minimum():
    b = MagicMock()
    b.is_connected.return_value = True
    b.get_account.return_value = {"balance": 500.0, "equity": 500.0}
    with pytest.raises(LiveModeNotEnabled, match="equity"):
        LiveBroker(b)


def test_place_order_delegates_to_bridge(connected_bridge):
    broker = LiveBroker(connected_bridge)
    result = broker.place_order("EURUSD", "BUY", 0.01, sl=1.09, tp=1.12)
    connected_bridge.send_order.assert_called_once()
    call_args = connected_bridge.send_order.call_args[0][0]
    # EA expects action="BUY"/"SELL" directly — not "OPEN" + "type" field
    assert call_args["action"] == "BUY"
    assert call_args["symbol"] == "EURUSD"
    assert "type" not in call_args
    assert result["ticket"] == 42


def test_close_position_sends_close_command(connected_bridge):
    broker = LiveBroker(connected_bridge)
    broker.close_position(99)
    connected_bridge.send_order.assert_called_with({"action": "CLOSE", "ticket": 99})


def test_get_positions_reads_bridge_state(connected_bridge):
    connected_bridge.get_state.return_value = {"positions": [{"ticket": 1}]}
    broker = LiveBroker(connected_bridge)
    positions = broker.get_positions()
    assert positions == [{"ticket": 1}]


def test_min_equity_config_override():
    b = MagicMock()
    b.is_connected.return_value = True
    b.get_account.return_value = {"balance": 1500.0, "equity": 1500.0}
    # Override min_equity to 2000 — should raise
    with pytest.raises(LiveModeNotEnabled):
        LiveBroker(b, config={"risk": {"min_equity": 2000.0}})


def test_get_closed_delegates_to_bridge(connected_bridge):
    connected_bridge.get_results.return_value = [{"ticket": 99, "profit": 15.0}]
    broker = LiveBroker(connected_bridge)
    results = broker.get_closed()
    assert len(results) == 1
    assert results[0]["ticket"] == 99


def test_get_account_delegates_to_bridge(connected_bridge):
    connected_bridge.get_account.return_value = {"balance": 7500.0, "equity": 7500.0}
    broker = LiveBroker(connected_bridge)
    acct = broker.get_account()
    assert acct["balance"] == pytest.approx(7500.0)
