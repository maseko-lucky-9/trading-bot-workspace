"""
LiveDataFeed — polls the bridge for the latest tick and caches it.

Polling is synchronous: call poll() each main-loop iteration.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.bridge.http_client import MT5BridgeClient


@dataclass
class Tick:
    symbol: str
    bid: float
    ask: float
    spread: float
    time: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


class LiveDataFeed:
    """Thin wrapper around bridge state for real-time tick access.

    Parameters
    ----------
    bridge : MT5BridgeClient
    spread_window : int
        Number of ticks to keep for average-spread computation.
    """

    def __init__(self, bridge: MT5BridgeClient, spread_window: int = 20) -> None:
        self.bridge = bridge
        self._spread_window = spread_window
        self._cache: dict[str, Tick] = {}
        self._spreads: dict[str, deque] = {}

    def poll(self) -> Tick | None:
        """Fetch current bridge state and update internal cache.

        Returns the Tick for the primary symbol or None if unavailable.
        """
        try:
            state = self.bridge.get_state() or {}
        except Exception:
            return None

        raw = state.get("tick") or {}
        if not raw:
            return None

        symbol = raw.get("symbol", "EURUSD")
        bid = float(raw.get("bid") or 0.0)
        ask = float(raw.get("ask") or 0.0)
        spread = float(raw.get("spread") or (ask - bid) / 0.0001)

        raw_time = raw.get("time")
        if isinstance(raw_time, (int, float)):
            ts = datetime.fromtimestamp(raw_time, tz=timezone.utc)
        else:
            ts = datetime.now(tz=timezone.utc)

        tick = Tick(symbol=symbol, bid=bid, ask=ask, spread=spread, time=ts)
        self._cache[symbol] = tick
        if symbol not in self._spreads:
            self._spreads[symbol] = deque(maxlen=self._spread_window)
        self._spreads[symbol].append(spread)
        return tick

    def latest(self, symbol: str = "EURUSD") -> Tick | None:
        return self._cache.get(symbol)

    def average_spread(self, symbol: str = "EURUSD", n: int | None = None) -> float:
        """Return the average spread over the last *n* ticks (or all cached)."""
        dq = self._spreads.get(symbol)
        if not dq:
            return 0.0
        window = list(dq)[-n:] if n else list(dq)
        return sum(window) / len(window) if window else 0.0
