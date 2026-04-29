"""
PaperBroker — simulates fills against live bridge tick prices.

All fill logic migrated verbatim from OrderManager (US-002).

Resilience guarantees (added 2026-04-29):
- Fail-closed pricing: a stale or symbol-mismatched bridge tick raises
  ``StaleTickError`` rather than returning a hard-coded fallback price. A
  short last-known-good cache (``LKG_TTL_SECONDS``) softens transient blips.
- Restart-safe state: ``_ticket_seq`` and ``_positions`` persist to
  ``checkpoints/paper_broker.json`` so bot restarts neither collide ticket
  numbers nor orphan open trades in ``logs/trades.csv``.
"""
from __future__ import annotations

import csv
import json
import os
import threading
import time
from datetime import datetime, timezone
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


class StaleTickError(RuntimeError):
    """Raised when no fresh tick is available for the requested symbol."""


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
    state_path : Path | None
        JSON state file for ticket counter + open positions; defaults to
        ``checkpoints/paper_broker.json`` relative to bot root.
    """

    LKG_TTL_SECONDS = 5.0
    INITIAL_TICKET = 1_000_000

    def __init__(
        self,
        bridge: MT5BridgeClient,
        log_path: Path | None = None,
        state_path: Path | None = None,
    ) -> None:
        self.bridge = bridge
        bot_root = Path(__file__).resolve().parents[2]
        self.log_path = log_path or bot_root / "logs" / "trades.csv"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_csv_header()

        self.state_path = state_path or bot_root / "checkpoints" / "paper_broker.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

        self._positions: dict[int, dict] = {}
        self._closed: list[dict] = []
        self._ticket_seq_value = self.INITIAL_TICKET
        self._last_tick: dict[str, tuple[float, float, float]] = {}
        self._lock = threading.Lock()

        self._load_state()

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
    # Persistence                                                        #
    # ------------------------------------------------------------------ #

    def _save_state(self) -> None:
        payload = {
            "ticket_seq": self._ticket_seq_value,
            "positions": {str(k): v for k, v in self._positions.items()},
        }
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload))
        os.replace(tmp, self.state_path)

    def _load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text())
            self._ticket_seq_value = int(data["ticket_seq"])
            self._positions = {int(k): v for k, v in (data.get("positions") or {}).items()}
        except Exception as exc:
            print(
                f"WARN: paper_broker state file unreadable ({exc!r}); "
                f"re-seeding from {self.log_path.name}",
            )
            self._ticket_seq_value = self._reseed_ticket_seq_from_csv()
            self._positions = {}

    def _reseed_ticket_seq_from_csv(self) -> int:
        if not self.log_path.exists():
            return self.INITIAL_TICKET
        try:
            with self.log_path.open() as f:
                reader = csv.DictReader(f)
                tickets = [int(row["ticket"]) for row in reader if row.get("ticket")]
            return (max(tickets) + 1) if tickets else self.INITIAL_TICKET
        except Exception:
            return self.INITIAL_TICKET

    def _next_ticket(self) -> int:
        # Caller must hold self._lock.
        t = self._ticket_seq_value
        self._ticket_seq_value += 1
        return t

    # ------------------------------------------------------------------ #
    # Pricing                                                            #
    # ------------------------------------------------------------------ #

    def _current_prices(self, symbol: str) -> tuple[float, float]:
        """Return (bid, ask) for ``symbol``.

        Raises ``StaleTickError`` when:
        - the bridge raises or returns an empty tick; OR
        - the tick reports a different ``symbol`` than requested; OR
        - bid/ask are zero/missing,

        unless a cached last-known-good tick for ``symbol`` is younger than
        ``LKG_TTL_SECONDS``, in which case it is reused.
        """
        try:
            tick = self.bridge.get_tick(symbol) or {}
        except Exception:
            tick = {}
        # Tick may not carry an explicit 'symbol' field (e.g. older bridge
        # responses, test fixtures). When absent, trust the request.
        tick_symbol = tick.get("symbol") or symbol
        bid = float(tick.get("bid") or 0.0)
        ask = float(tick.get("ask") or 0.0)
        if bid and ask and tick_symbol == symbol:
            self._last_tick[symbol] = (bid, ask, time.time())
            return bid, ask

        cached = self._last_tick.get(symbol)
        if cached is not None and (time.time() - cached[2]) <= self.LKG_TTL_SECONDS:
            return cached[0], cached[1]

        raise StaleTickError(
            f"no fresh tick for {symbol} "
            f"(bridge_symbol={tick.get('symbol')!r}, bid={bid}, ask={ask})"
        )

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
        now = _utc_now()
        with self._lock:
            ticket = self._next_ticket()
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
            self._positions[ticket] = position
            self._save_state()
        self._journal({**position, "close_price": "", "close_time": "", "profit": ""})
        return dict(position)

    def close_position(self, ticket: int) -> dict:
        with self._lock:
            pos = self._positions.pop(ticket, None)
        if pos is None:
            raise KeyError(f"unknown ticket {ticket}")
        try:
            bid, ask = self._current_prices(pos["symbol"])
        except StaleTickError:
            with self._lock:
                self._positions[ticket] = pos
            raise
        close_price = bid if pos["type"] == "BUY" else ask
        profit = self._pnl(pos["type"], pos["volume"], pos["open_price"], close_price)
        pos["close_price"] = close_price
        pos["close_time"] = _utc_now()
        pos["profit"] = profit
        with self._lock:
            self._closed.append(pos)
            self._save_state()
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
