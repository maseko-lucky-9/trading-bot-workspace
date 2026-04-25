"""Tests for LiveDataFeed."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.data.feed import LiveDataFeed, Tick


@pytest.fixture
def bridge():
    b = MagicMock()
    b.get_state.return_value = {
        "tick": {"symbol": "EURUSD", "bid": 1.10000, "ask": 1.10002, "spread": 2.0, "time": 1700000000},
        "account": {},
    }
    return b


@pytest.fixture
def feed(bridge):
    return LiveDataFeed(bridge, spread_window=5)


def test_poll_returns_populated_tick(feed):
    tick = feed.poll()
    assert isinstance(tick, Tick)
    assert tick.symbol == "EURUSD"
    assert tick.bid == 1.10000
    assert tick.ask == 1.10002


def test_latest_returns_cached_tick(feed):
    feed.poll()
    tick = feed.latest("EURUSD")
    assert tick is not None
    assert tick.symbol == "EURUSD"


def test_latest_returns_none_before_poll(bridge):
    feed = LiveDataFeed(bridge)
    assert feed.latest("EURUSD") is None


def test_average_spread_converges(bridge):
    feed = LiveDataFeed(bridge, spread_window=10)
    for _ in range(5):
        feed.poll()
    avg = feed.average_spread("EURUSD")
    assert avg == pytest.approx(2.0)


def test_average_spread_uses_n_window(bridge):
    b = MagicMock()
    spreads = [1.0, 2.0, 3.0, 4.0, 5.0]
    call_count = 0

    def state_side_effect():
        nonlocal call_count
        s = spreads[min(call_count, len(spreads) - 1)]
        call_count += 1
        return {"tick": {"symbol": "EURUSD", "bid": 1.1, "ask": 1.1 + s * 0.0001, "spread": s}}

    b.get_state.side_effect = state_side_effect
    feed = LiveDataFeed(b, spread_window=10)
    for _ in range(5):
        feed.poll()
    # Last 3 of [1,2,3,4,5] average to 4.0
    avg = feed.average_spread("EURUSD", n=3)
    assert avg == pytest.approx(4.0)


def test_poll_returns_none_on_bridge_error(bridge):
    bridge.get_state.side_effect = Exception("timeout")
    feed = LiveDataFeed(bridge)
    assert feed.poll() is None


def test_tick_mid_property(feed):
    tick = feed.poll()
    assert tick.mid == pytest.approx((1.10000 + 1.10002) / 2)


def test_poll_returns_none_when_state_has_empty_tick(bridge):
    bridge.get_state.return_value = {"tick": {}, "account": {}, "connected": False}
    feed = LiveDataFeed(bridge)
    assert feed.poll() is None


def test_average_spread_returns_zero_for_unknown_symbol(bridge):
    feed = LiveDataFeed(bridge)
    assert feed.average_spread("UNKNOWN") == pytest.approx(0.0)
