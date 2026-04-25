"""Tests for PaperBroker."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.execution.paper_broker import PaperBroker


@pytest.fixture
def bridge():
    b = MagicMock()
    b.get_tick.return_value = {"bid": 1.10000, "ask": 1.10002}
    b.get_account.return_value = {"balance": 10000.0, "equity": 10000.0}
    return b


@pytest.fixture
def broker(bridge, tmp_path):
    return PaperBroker(bridge, log_path=tmp_path / "trades.csv")


def test_place_order_returns_ticket(broker):
    result = broker.place_order("EURUSD", "BUY", 0.01)
    assert isinstance(result["ticket"], int)
    assert result["symbol"] == "EURUSD"
    assert result["type"] == "BUY"


def test_close_records_pnl(broker):
    opened = broker.place_order("EURUSD", "BUY", 0.01)
    closed = broker.close_position(opened["ticket"])
    assert isinstance(closed["profit"], float)
    assert closed["close_price"] != ""


def test_get_account_reflects_bridge(broker, bridge):
    acct = broker.get_account()
    assert acct["balance"] == 10000.0


def test_csv_row_count_matches_operations(broker, tmp_path):
    csv_path = tmp_path / "trades.csv"
    t1 = broker.place_order("EURUSD", "BUY", 0.01)
    t2 = broker.place_order("EURUSD", "SELL", 0.01)
    broker.close_position(t1["ticket"])
    lines = csv_path.read_text().strip().splitlines()
    # 1 header + 2 opens + 1 close = 4 lines
    assert len(lines) == 4


def test_close_unknown_ticket_raises(broker):
    with pytest.raises(KeyError):
        broker.close_position(9999999)


def test_get_positions_empty_after_close(broker):
    t = broker.place_order("EURUSD", "BUY", 0.01)
    assert len(broker.get_positions()) == 1
    broker.close_position(t["ticket"])
    assert len(broker.get_positions()) == 0


def test_get_closed_accumulates(broker):
    t1 = broker.place_order("EURUSD", "BUY", 0.01)
    t2 = broker.place_order("EURUSD", "SELL", 0.01)
    broker.close_position(t1["ticket"])
    broker.close_position(t2["ticket"])
    assert len(broker.get_closed()) == 2


# ------------------------------------------------------------------ #
# Spread fill-price tests                                            #
# ------------------------------------------------------------------ #

def test_buy_fills_at_ask(broker, bridge):
    bridge.get_tick.return_value = {"bid": 1.10000, "ask": 1.10020}
    result = broker.place_order("EURUSD", "BUY", 0.01)
    assert result["open_price"] == pytest.approx(1.10020)


def test_sell_fills_at_bid(broker, bridge):
    bridge.get_tick.return_value = {"bid": 1.10000, "ask": 1.10020}
    result = broker.place_order("EURUSD", "SELL", 0.01)
    assert result["open_price"] == pytest.approx(1.10000)


def test_buy_closes_at_bid(broker, bridge):
    bridge.get_tick.return_value = {"bid": 1.10000, "ask": 1.10020}
    opened = broker.place_order("EURUSD", "BUY", 0.01)
    closed = broker.close_position(opened["ticket"])
    assert closed["close_price"] == pytest.approx(1.10000)


def test_sell_closes_at_ask(broker, bridge):
    bridge.get_tick.return_value = {"bid": 1.10000, "ask": 1.10020}
    opened = broker.place_order("EURUSD", "SELL", 0.01)
    closed = broker.close_position(opened["ticket"])
    assert closed["close_price"] == pytest.approx(1.10020)


def test_round_trip_buy_no_movement_loses_spread(broker, bridge):
    """With no market movement a BUY round-trip costs the spread."""
    bridge.get_tick.return_value = {"bid": 1.10000, "ask": 1.10020}
    t = broker.place_order("EURUSD", "BUY", 1.0)
    closed = broker.close_position(t["ticket"])
    # Opened at 1.10020, closed at 1.10000 → -2 pips → -$20 for 1 lot
    assert closed["profit"] == pytest.approx(-20.0, abs=0.01)


def test_round_trip_sell_no_movement_loses_spread(broker, bridge):
    bridge.get_tick.return_value = {"bid": 1.10000, "ask": 1.10020}
    t = broker.place_order("EURUSD", "SELL", 1.0)
    closed = broker.close_position(t["ticket"])
    # Opened at 1.10000, closed at 1.10020 → -2 pips → -$20 for 1 lot
    assert closed["profit"] == pytest.approx(-20.0, abs=0.01)


def test_buy_profits_when_market_rises(broker, bridge):
    bridge.get_tick.return_value = {"bid": 1.10000, "ask": 1.10020}
    t = broker.place_order("EURUSD", "BUY", 1.0)
    # Market moves up 10 pips
    bridge.get_tick.return_value = {"bid": 1.10100, "ask": 1.10120}
    closed = broker.close_position(t["ticket"])
    # Opened at 1.10020, closed at 1.10100 → +8 pips net → +$80
    assert closed["profit"] == pytest.approx(80.0, abs=0.01)


def test_sell_profits_when_market_falls(broker, bridge):
    bridge.get_tick.return_value = {"bid": 1.10000, "ask": 1.10020}
    t = broker.place_order("EURUSD", "SELL", 1.0)
    # Market moves down 10 pips
    bridge.get_tick.return_value = {"bid": 1.09900, "ask": 1.09920}
    closed = broker.close_position(t["ticket"])
    # Opened at 1.10000, closed at 1.09920 → +8 pips net → +$80
    assert closed["profit"] == pytest.approx(80.0, abs=0.01)


def test_current_prices_fallback_on_bridge_exception(bridge, tmp_path):
    bridge.get_tick.side_effect = Exception("bridge exploded")
    broker = PaperBroker(bridge, log_path=tmp_path / "trades.csv")
    result = broker.place_order("EURUSD", "BUY", 0.01)
    assert result["open_price"] == pytest.approx(1.10002)


def test_get_account_fallback_on_bridge_exception(bridge, tmp_path):
    bridge.get_account.side_effect = Exception("bridge exploded")
    broker = PaperBroker(bridge, log_path=tmp_path / "trades.csv")
    acct = broker.get_account()
    assert acct["balance"] == pytest.approx(10_000.0)
