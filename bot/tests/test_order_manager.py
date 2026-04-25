"""Tests for OrderManager → broker and tracker wiring."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.execution.order_manager import OrderManager
from core.execution.paper_broker import PaperBroker
from core.performance.tracker import PerformanceTracker


@pytest.fixture
def bridge():
    b = MagicMock()
    b.get_tick.return_value = {"bid": 1.10000, "ask": 1.10020}
    b.get_state.return_value = {"positions": [], "account": {}}
    return b


@pytest.fixture
def om(bridge, tmp_path):
    broker = PaperBroker(bridge, log_path=tmp_path / "trades.csv")
    tracker = PerformanceTracker()
    return OrderManager({}, broker, tracker=tracker), tracker


def test_buy_returns_ticket(om):
    manager, _ = om
    result = manager.buy("EURUSD", 0.01)
    assert "ticket" in result
    assert result["ticket"] > 0


def test_close_flows_to_tracker(om):
    manager, tracker = om
    ticket = manager.buy("EURUSD", 0.01)["ticket"]
    manager.close(ticket)
    assert tracker.trade_count == 1


def test_tracker_accumulates_multiple_trades(om):
    manager, tracker = om
    for _ in range(3):
        t = manager.buy("EURUSD", 0.01)["ticket"]
        manager.close(t)
    assert tracker.trade_count == 3


def test_close_without_tracker_does_not_raise(bridge, tmp_path):
    broker = PaperBroker(bridge, log_path=tmp_path / "trades.csv")
    manager = OrderManager({}, broker)  # no tracker
    ticket = manager.buy("EURUSD", 0.01)["ticket"]
    result = manager.close(ticket)
    assert "ticket" in result


def test_get_positions_reflects_open_trades(om):
    manager, _ = om
    assert manager.get_positions() == []
    manager.buy("EURUSD", 0.01)
    assert len(manager.get_positions()) == 1


def test_get_closed_reflects_closed_trades(om):
    manager, _ = om
    ticket = manager.buy("EURUSD", 0.01)["ticket"]
    manager.close(ticket)
    assert len(manager.get_closed()) == 1


def test_sell_returns_ticket(om):
    manager, _ = om
    result = manager.sell("EURUSD", 0.01)
    assert isinstance(result["ticket"], int)
    assert result["type"] == "SELL"
