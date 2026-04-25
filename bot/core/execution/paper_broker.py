"""
PaperBroker — simulates fills against live bridge tick prices.

All fill logic migrated verbatim from OrderManager (US-002).
"""
from __future__ import annotations

import csv
import threading
from datetime import datetime, timezone
from itertools import count
from pathlib import Path

from core.bridge.http_client import MT5BridgeClient

TRADE_CSV_COLUMNS = [
    "ticket",
    "symbol",
    "type",
    "volume",
    "open_price",
    "open_time",
    "close_price",
    "close_time",
    "profit",
    "sl",
    "tp",
]

PIP_VALUE_USD_PER_LOT = 10.0
PIP_SIZE = 0.0001


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class PaperBroker:
    """Simulates paper fills against live bridge tick prices.

    Parameters
    ----------
    bridge : MT5BridgeClient
        Used only to fetch tick prices.
    log_path : Path | None
        CSV journal path; defaults to ``logs/trades.csv`` relative to bot root.
    """

    def __init__(
        self,
        bridge: MT5BridgeClient,
        log_path: Path | None = None,
    ) -> None:
        self.bridge = bridge
        bot_root = Path(__file__).resolve().parents[2]
        self.log_path = log_path or bot_root / "logs" / "trades.csv"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_csv_header()

        self._positions: dict[int, dict] = {}
        self._closed: list[dict] = []
        self._ticket_seq = count(start=1_000_000)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # CSV journal                                                        #
    # ------------------------------------------------------------------ #

    def _ensure_csv_header(self) -> None:
        if not self.log_path.exists() or self.log_path.stat().st_size == 0:
            with self.log_path.open("w", newline="") as f:
                csv.writer(f).writerow(TRADE_CSV_COLUMNS)

    def _journal(self, row: dict) -> None:
        with self.log_path.open("a", newline="") as f:
            csv.writer(f).writerow([row.get(c, "") for c in TRADE_CSV_COLUMNS])

    # ------------------------------------------------------------------ #
    # Pricing                                                            #
    # ------------------------------------------------------------------ #

    def _current_prices(self, symbol: str) -> tuple[float, float]:
        try:
            tick = self.bridge.get_tick(symbol) or {}
            bid = float(tick.get("bid") or 0.0)
            ask = float(tick.get("ask") or 0.0)
            if bid and ask:
                return bid, ask
        except Exception:
            pass
        return 1.10000, 1.10002

    @staticmethod
    def _pnl(side: str, volume: float, open_price: float, close_price: float) -> float:
        delta_pips = (close_price - open_price) / PIP_SIZE
        if side == "SELL":
            delta_pips = -delta_pips
        return round(delta_pips * PIP_VALUE_USD_PER_LOT * volume, 2)

    # ------------------------------------------------------------------ #
    # Broker interface                                                   #
    # ------------------------------------------------------------------ #

    def place_order(
        self,
        symbol: str,
        side: str,
        volume: float,
        sl: float = 0.0,
        tp: float = 0.0,
    ) -> dict:
        bid, ask = self._current_prices(symbol)
        fill = ask if side == "BUY" else bid
        ticket = next(self._ticket_seq)
        now = _utc_now()
        position = {
            "ticket": ticket,
            "symbol": symbol,
            "type": side,
            "volume": float(volume),
            "open_price": fill,
            "open_time": now,
            "close_price": "",
            "close_time": "",
            "profit": 0.0,
            "sl": float(sl),
            "tp": float(tp),
        }
        with self._lock:
            self._positions[ticket] = position
        self._journal({**position, "close_price": "", "close_time": "", "profit": ""})
        return dict(position)

    def close_position(self, ticket: int) -> dict:
        with self._lock:
            pos = self._positions.pop(ticket, None)
        if pos is None:
            raise KeyError(f"unknown ticket {ticket}")
        bid, ask = self._current_prices(pos["symbol"])
        close_price = bid if pos["type"] == "BUY" else ask
        profit = self._pnl(pos["type"], pos["volume"], pos["open_price"], close_price)
        pos["close_price"] = close_price
        pos["close_time"] = _utc_now()
        pos["profit"] = profit
        with self._lock:
            self._closed.append(pos)
        self._journal(pos)
        return dict(pos)

    def get_positions(self) -> list[dict]:
        with self._lock:
            return [dict(p) for p in self._positions.values()]

    def get_closed(self) -> list[dict]:
        with self._lock:
            return [dict(p) for p in self._closed]

    def get_account(self) -> dict:
        try:
            acct = self.bridge.get_account() or {}
            if acct:
                return acct
        except Exception:
            pass
        return {"balance": 10_000.0, "equity": 10_000.0}
