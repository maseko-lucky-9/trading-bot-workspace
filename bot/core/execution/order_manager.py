"""
OrderManager (US-002, US-010).

Thin routing layer that delegates all broker-specific logic to a broker
object (PaperBroker or LiveBroker). Public API is unchanged.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.performance.tracker import PerformanceTracker


class OrderManager:
    """Routes buy/sell/close through the supplied broker.

    Parameters
    ----------
    config : dict
        Parsed config.yaml.
    broker : PaperBroker | LiveBroker
        Handles all fill / order-routing logic.
    tracker : PerformanceTracker | None
        When supplied, ``close()`` calls ``tracker.record_trade()``
        automatically so performance metrics accumulate.
    """

    def __init__(
        self,
        config: dict,
        broker,
        tracker: "PerformanceTracker | None" = None,
    ) -> None:
        self.config = config or {}
        self.broker = broker
        self._tracker = tracker

    # ------------------------------------------------------------------ #
    # Public order API                                                   #
    # ------------------------------------------------------------------ #

    def buy(
        self,
        symbol: str,
        volume: float,
        sl: float = 0.0,
        tp: float = 0.0,
    ) -> dict:
        return self.broker.place_order(symbol, "BUY", volume, sl=sl, tp=tp)

    def sell(
        self,
        symbol: str,
        volume: float,
        sl: float = 0.0,
        tp: float = 0.0,
    ) -> dict:
        return self.broker.place_order(symbol, "SELL", volume, sl=sl, tp=tp)

    def close(self, ticket: int) -> dict:
        closed = self.broker.close_position(ticket)
        if self._tracker is not None:
            self._tracker.record_trade(closed)
        return closed

    def partial_close(self, ticket: int, fraction: float) -> dict:
        """Close a fraction of an open position without recording a full trade."""
        return self.broker.partial_close(ticket, fraction)

    def set_breakeven(self, ticket: int, buffer_pips: float = 1.0) -> dict:
        """Move the stop-loss to entry price + buffer_pips (BUY) or - buffer_pips (SELL).

        Reads the position's open_price and type from the broker to compute
        the new SL.  Falls back gracefully if the position is not found.
        """
        from core.execution.paper_broker import PIP_SIZE

        positions = {p["ticket"]: p for p in self.broker.get_positions()}
        pos = positions.get(ticket)
        if pos is None:
            return {"ticket": ticket, "sl": None, "error": "position not found"}
        entry = float(pos.get("open_price", 0.0))
        side = pos.get("type", "BUY")
        pip_offset = buffer_pips * PIP_SIZE
        new_sl = entry + pip_offset if side == "BUY" else entry - pip_offset
        return self.broker.modify_sl(ticket, round(new_sl, 5))

    def get_positions(self) -> list[dict]:
        return self.broker.get_positions()

    def get_closed(self) -> list[dict]:
        return self.broker.get_closed()
