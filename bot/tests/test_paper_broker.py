"""Tests for PaperBroker."""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.execution.paper_broker import PaperBroker, StaleTickError


@pytest.fixture
def bridge():
    b = MagicMock()
    b.get_tick.return_value = {"bid": 1.10000, "ask": 1.10002}
    b.get_account.return_value = {"balance": 10000.0, "equity": 10000.0}
    return b


@pytest.fixture
def broker(bridge, tmp_path):
    return PaperBroker(
        bridge,
        log_path=tmp_path / "trades.csv",
        state_path=tmp_path / "state" / "paper_broker.json",
    )


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


def test_get_account_fallback_on_bridge_exception(bridge, tmp_path):
    bridge.get_account.side_effect = Exception("bridge exploded")
    broker = PaperBroker(
        bridge,
        log_path=tmp_path / "trades.csv",
        state_path=tmp_path / "state" / "paper_broker.json",
    )
    acct = broker.get_account()
    assert acct["balance"] == pytest.approx(10_000.0)


# ------------------------------------------------------------------ #
# Fail-closed pricing                                                #
# ------------------------------------------------------------------ #

def test_place_order_raises_on_bridge_exception(bridge, tmp_path):
    """Bridge raising must surface as StaleTickError, not a fake fallback fill."""
    bridge.get_tick.side_effect = Exception("bridge exploded")
    broker = PaperBroker(
        bridge,
        log_path=tmp_path / "trades.csv",
        state_path=tmp_path / "state" / "paper_broker.json",
    )
    with pytest.raises(StaleTickError):
        broker.place_order("EURUSD", "BUY", 0.01)
    # No row beyond the header should have been written.
    assert (tmp_path / "trades.csv").read_text().splitlines() == [
        "ticket,symbol,type,volume,open_price,open_time,close_price,close_time,profit,sl,tp"
    ]


def test_place_order_raises_on_empty_tick(bridge, tmp_path):
    bridge.get_tick.return_value = {}
    broker = PaperBroker(
        bridge,
        log_path=tmp_path / "trades.csv",
        state_path=tmp_path / "state" / "paper_broker.json",
    )
    with pytest.raises(StaleTickError):
        broker.place_order("EURUSD", "BUY", 0.01)


def test_place_order_raises_on_symbol_mismatch(bridge, tmp_path):
    """Bridge holds USDJPY tick but bot asks for EURUSD → reject."""
    bridge.get_tick.return_value = {
        "symbol": "USDJPY",
        "bid": 159.878,
        "ask": 159.881,
    }
    broker = PaperBroker(
        bridge,
        log_path=tmp_path / "trades.csv",
        state_path=tmp_path / "state" / "paper_broker.json",
    )
    with pytest.raises(StaleTickError):
        broker.place_order("EURUSD", "BUY", 0.01)


def test_lkg_cache_serves_recent_blip(bridge, tmp_path):
    """A transient bridge blip < 5 s after a good tick must reuse the cache."""
    broker = PaperBroker(
        bridge,
        log_path=tmp_path / "trades.csv",
        state_path=tmp_path / "state" / "paper_broker.json",
    )
    # Prime the cache with a good tick.
    first = broker.place_order("EURUSD", "BUY", 0.01)
    assert first["open_price"] == pytest.approx(1.10002)
    # Bridge now goes silent.
    bridge.get_tick.side_effect = Exception("transient blip")
    # Within the 5 s LKG window: still fills using cached prices.
    second = broker.place_order("EURUSD", "BUY", 0.01)
    assert second["open_price"] == pytest.approx(1.10002)


def test_lkg_cache_expires(bridge, tmp_path, monkeypatch):
    broker = PaperBroker(
        bridge,
        log_path=tmp_path / "trades.csv",
        state_path=tmp_path / "state" / "paper_broker.json",
    )
    broker.place_order("EURUSD", "BUY", 0.01)
    bridge.get_tick.side_effect = Exception("blip")
    # Advance the clock past LKG_TTL_SECONDS.
    real_time = time.time
    fake_now = real_time() + broker.LKG_TTL_SECONDS + 1.0
    monkeypatch.setattr(time, "time", lambda: fake_now)
    with pytest.raises(StaleTickError):
        broker.place_order("EURUSD", "BUY", 0.01)


def test_close_rolls_back_position_on_stale_tick(broker, bridge):
    """If close_position can't get a fresh tick, the position must be retained."""
    opened = broker.place_order("EURUSD", "BUY", 0.01)
    # Force the LKG cache to expire so the next tick fetch is fail-closed.
    broker._last_tick.clear()
    bridge.get_tick.side_effect = Exception("bridge exploded")
    with pytest.raises(StaleTickError):
        broker.close_position(opened["ticket"])
    # Position must still be open.
    assert any(p["ticket"] == opened["ticket"] for p in broker.get_positions())


# ------------------------------------------------------------------ #
# Restart safety                                                     #
# ------------------------------------------------------------------ #

def test_ticket_counter_persists_across_restart(bridge, tmp_path):
    log = tmp_path / "trades.csv"
    state = tmp_path / "state" / "paper_broker.json"
    broker_a = PaperBroker(bridge, log_path=log, state_path=state)
    a1 = broker_a.place_order("EURUSD", "BUY", 0.01)
    a2 = broker_a.place_order("EURUSD", "BUY", 0.01)
    assert a2["ticket"] == a1["ticket"] + 1

    # Simulate restart: new instance against the same files.
    broker_b = PaperBroker(bridge, log_path=log, state_path=state)
    b1 = broker_b.place_order("EURUSD", "BUY", 0.01)
    assert b1["ticket"] == a2["ticket"] + 1


def test_open_positions_reload_across_restart(bridge, tmp_path):
    log = tmp_path / "trades.csv"
    state = tmp_path / "state" / "paper_broker.json"
    broker_a = PaperBroker(bridge, log_path=log, state_path=state)
    opened = broker_a.place_order("EURUSD", "BUY", 0.01)

    broker_b = PaperBroker(bridge, log_path=log, state_path=state)
    positions = broker_b.get_positions()
    assert len(positions) == 1
    assert positions[0]["ticket"] == opened["ticket"]
    # Closing through the new instance must succeed without KeyError.
    closed = broker_b.close_position(opened["ticket"])
    assert closed["close_price"] != ""


def test_corrupt_state_file_reseeds_from_csv_max_ticket(bridge, tmp_path, capsys):
    log = tmp_path / "trades.csv"
    state = tmp_path / "state" / "paper_broker.json"
    # Seed CSV with a known max ticket.
    state.parent.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        "ticket,symbol,type,volume,open_price,open_time,close_price,close_time,profit,sl,tp\n"
        "1234567,EURUSD,BUY,0.01,1.1,2026-04-01T00:00:00+00:00,,,,0,0\n"
    )
    state.write_text("{ this is not valid json")
    broker = PaperBroker(bridge, log_path=log, state_path=state)
    captured = capsys.readouterr()
    assert "WARN" in captured.out
    new = broker.place_order("EURUSD", "BUY", 0.01)
    assert new["ticket"] == 1234568


def test_state_file_atomic_write_no_partial_left_behind(bridge, tmp_path):
    state = tmp_path / "state" / "paper_broker.json"
    broker = PaperBroker(
        bridge,
        log_path=tmp_path / "trades.csv",
        state_path=state,
    )
    broker.place_order("EURUSD", "BUY", 0.01)
    # No leftover .tmp file from atomic replace.
    assert not (state.parent / (state.name + ".tmp")).exists()
    # Persisted JSON parses cleanly.
    payload = json.loads(state.read_text())
    assert "ticket_seq" in payload
    assert "positions" in payload
