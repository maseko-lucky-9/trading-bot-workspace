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
    broker = PaperBroker(
        bridge,
        log_path=tmp_path / "trades.csv",
        state_path=tmp_path / "state" / "paper_broker.json",
    )
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
    broker = PaperBroker(
        bridge,
        log_path=tmp_path / "trades.csv",
        state_path=tmp_path / "state" / "paper_broker.json",
    )
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


# ------------------------------------------------------------------ #
# partial_close delegation                                           #
# ------------------------------------------------------------------ #

def test_partial_close_delegates_to_broker(om):
    manager, _ = om
    t = manager.buy("EURUSD", 1.0)["ticket"]
    result = manager.partial_close(t, 0.5)
    assert result["ticket"] == t
    assert result["remaining_volume"] == pytest.approx(0.5)


def test_partial_close_position_stays_open(om):
    manager, _ = om
    t = manager.buy("EURUSD", 1.0)["ticket"]
    manager.partial_close(t, 0.5)
    assert any(p["ticket"] == t for p in manager.get_positions())


# ------------------------------------------------------------------ #
# set_breakeven                                                       #
# ------------------------------------------------------------------ #

def test_set_breakeven_buy_moves_sl_above_entry(om, bridge):
    from core.execution.paper_broker import PIP_SIZE
    bridge.get_tick.return_value = {"bid": 1.10000, "ask": 1.10020}
    manager, _ = om
    t = manager.buy("EURUSD", 0.01)["ticket"]
    entry = next(p["open_price"] for p in manager.get_positions() if p["ticket"] == t)
    manager.set_breakeven(t, buffer_pips=1.0)
    pos = next(p for p in manager.get_positions() if p["ticket"] == t)
    assert pos["sl"] == pytest.approx(entry + PIP_SIZE, abs=1e-5)


def test_set_breakeven_sell_moves_sl_below_entry(om, bridge):
    from core.execution.paper_broker import PIP_SIZE
    bridge.get_tick.return_value = {"bid": 1.10000, "ask": 1.10020}
    manager, _ = om
    t = manager.sell("EURUSD", 0.01)["ticket"]
    entry = next(p["open_price"] for p in manager.get_positions() if p["ticket"] == t)
    manager.set_breakeven(t, buffer_pips=1.0)
    pos = next(p for p in manager.get_positions() if p["ticket"] == t)
    assert pos["sl"] == pytest.approx(entry - PIP_SIZE, abs=1e-5)


def test_set_breakeven_unknown_ticket_returns_error(om):
    manager, _ = om
    result = manager.set_breakeven(9999999)
    assert result.get("error") is not None
