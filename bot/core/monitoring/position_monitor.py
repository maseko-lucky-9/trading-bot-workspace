"""PositionMonitor — daemon-thread component for live position tracking.

LOG-ONLY alerts: log-file and stdout only; no HTTP, no external services.
A single WARNING log line is emitted when a trade closes with profit < -alert_loss_usd.

Public API:
    PositionMonitor(broker, config, *, clock=None, logger=None, stdout=None)
        .start()              -> spawn daemon thread (idempotent)
        .stop(timeout=2.0)    -> set stop event, join (idempotent)
        .poll_once()          -> single sync pass; returns counts dict
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable


# --------------------------------------------------------------------------- #
# T003: Snapshot dataclass + pure diff function                               #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _PosSnap:
    """Frozen snapshot of an open position at one polling instant."""

    ticket: int
    symbol: str
    side: str          # "buy" | "sell"
    volume: float
    sl: float
    tp: float
    open_price: float
    open_time: str     # ISO-8601 UTC


_MUTABLE_FIELDS = ("sl", "tp", "volume")


def _diff(
    current: dict[int, _PosSnap],
    previous: dict[int, _PosSnap],
) -> tuple[list[_PosSnap], list[tuple[_PosSnap, list[str]]]]:
    """Compute opened + modified events between two snapshots.

    Closed events are NOT derived here — they come from broker.get_closed().

    Returns
    -------
    opened : list of _PosSnap (in current, not in previous)
    modified : list of (current_snap, [changed_field_names])
    """
    opened: list[_PosSnap] = []
    modified: list[tuple[_PosSnap, list[str]]] = []

    for ticket, snap in current.items():
        prev = previous.get(ticket)
        if prev is None:
            opened.append(snap)
            continue
        changed = [f for f in _MUTABLE_FIELDS if getattr(snap, f) != getattr(prev, f)]
        if changed:
            modified.append((snap, changed))
    return opened, modified


# --------------------------------------------------------------------------- #
# T004: NDJSON writer with rotating handler + 7-day cleanup                   #
# --------------------------------------------------------------------------- #


class _JsonlWriter:
    """One-line-per-event NDJSON writer with size-based rotation
    and lazy age-based cleanup.

    Single-producer assumption: only the PositionMonitor poller thread
    writes through this instance, so RotatingFileHandler's lack of
    cross-process safety is not a concern.
    """

    _CLEANUP_INTERVAL_S = 3600.0  # at most once per hour

    def __init__(
        self,
        path: str,
        *,
        max_bytes: int = 10 * 1024 * 1024,
        backup_count: int = 10,
        retention_days: int = 7,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = str(path)
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.retention_days = retention_days
        self._clock = clock
        self._last_cleanup: float = 0.0

        # Ensure parent dir exists
        parent = Path(self.path).parent
        if str(parent) and parent != Path():
            parent.mkdir(parents=True, exist_ok=True)

        # Dedicated logger so we don't pollute root
        self._logger = logging.getLogger(f"position_monitor.jsonl.{id(self)}")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        # Clear any previous handlers (defensive — important for tests that
        # construct multiple writers in one process)
        for h in list(self._logger.handlers):
            self._logger.removeHandler(h)
        handler = RotatingFileHandler(
            self.path,
            maxBytes=self.max_bytes,
            backupCount=self.backup_count,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        self._logger.addHandler(handler)
        self._handler = handler

        # First-run cleanup
        try:
            self._cleanup_old(force=True)
        except Exception:
            # Cleanup is best-effort; never fail construction
            pass

    def write(self, event: dict[str, Any]) -> None:
        """Serialize event as a single JSON line and emit."""
        line = json.dumps(event, separators=(",", ":"), default=str)
        self._logger.info(line)
        # Throttled cleanup
        now = self._clock()
        if now - self._last_cleanup >= self._CLEANUP_INTERVAL_S:
            try:
                self._cleanup_old()
            except Exception:
                pass

    def _cleanup_old(self, *, force: bool = False) -> int:
        """Delete rotated files whose mtime is older than retention_days.

        Returns the number of files removed. Updates self._last_cleanup.
        """
        now = self._clock()
        cutoff = now - (self.retention_days * 86400.0)
        removed = 0
        parent = Path(self.path).parent or Path(".")
        base = Path(self.path).name
        # Match base.1, base.2, ..., or base.<timestamp> rotations
        for entry in parent.iterdir():
            if not entry.is_file():
                continue
            name = entry.name
            if name == base:
                continue  # never delete the active file
            if not name.startswith(base + "."):
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff:
                try:
                    entry.unlink()
                    removed += 1
                except OSError:
                    pass
        self._last_cleanup = now
        return removed

    def close(self) -> None:
        """Release the file handler. Idempotent."""
        try:
            self._handler.close()
            self._logger.removeHandler(self._handler)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# T005: Alerter — log-only, no Slack, no network                              #
# --------------------------------------------------------------------------- #


class _Alerter:
    """Stdout [FILL] line on close + WARNING log line on threshold loss.

    No network I/O. Log and stdout only. The presence of the threshold
    logic in a discrete class keeps it independently testable.
    """

    def __init__(
        self,
        *,
        alert_loss_usd: float,
        logger: logging.Logger,
        stdout=None,
    ) -> None:
        self.alert_loss_usd = float(alert_loss_usd)
        self._logger = logger
        self._stdout = stdout if stdout is not None else sys.stdout

    def on_close(self, event: dict[str, Any]) -> None:
        """Print [FILL] summary then evaluate the alert threshold."""
        ticket = event.get("ticket")
        symbol = event.get("symbol", "")
        profit = event.get("profit", 0.0) or 0.0
        ts = event.get("ts") or event.get("close_time") or ""
        line = f"[FILL] ticket={ticket} symbol={symbol} profit=${profit:.2f} at {ts}"
        print(line, file=self._stdout, flush=True)
        self.maybe_alert(event)

    def maybe_alert(self, event: dict[str, Any]) -> bool:
        """Emit WARNING log if profit is a loss exceeding threshold.

        Returns True if a warning was emitted.
        """
        profit = event.get("profit")
        if profit is None:
            return False
        try:
            profit_f = float(profit)
        except (TypeError, ValueError):
            return False
        if profit_f < -self.alert_loss_usd:
            self._logger.warning(
                "LOSS_ALERT ticket=%s symbol=%s profit=$%.2f",
                event.get("ticket"),
                event.get("symbol", ""),
                profit_f,
            )
            return True
        return False


# --------------------------------------------------------------------------- #
# T006 + T007: PositionMonitor — sync poll + daemon thread                    #
# --------------------------------------------------------------------------- #


class PositionMonitor:
    """Polls broker for open + closed positions; emits NDJSON state-change
    events; alerts on close. Daemon-thread; live-mode only.
    """

    def __init__(
        self,
        broker: Any,
        config: dict | None = None,
        *,
        clock: Callable[[], float] | None = None,
        logger: logging.Logger | None = None,
        stdout=None,
    ) -> None:
        self.broker = broker
        cfg = config or {}
        mon_cfg = cfg.get("monitoring") or {}
        risk_cfg = cfg.get("risk") or {}

        self.poll_interval_s: float = float(mon_cfg.get("poll_interval_s", 5))
        self.log_path: str = str(mon_cfg.get("log_path", "logs/positions.jsonl"))
        self.alert_loss_usd: float = float(risk_cfg.get("alert_loss_usd", 50.0))

        self._clock = clock if clock is not None else time.time
        self._logger = logger if logger is not None else logging.getLogger("position_monitor")

        self._writer = _JsonlWriter(self.log_path, clock=self._clock)
        self._alerter = _Alerter(
            alert_loss_usd=self.alert_loss_usd,
            logger=self._logger,
            stdout=stdout,
        )

        self._last_snapshot: dict[int, _PosSnap] = {}
        # CF-1: defensive dedupe in case broker.get_closed() returns
        # cumulative results rather than only-new since last call.
        self._seen_closed_tickets: set[int] = set()

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ #
    # Snapshot conversion                                                #
    # ------------------------------------------------------------------ #

    def _to_snap(self, raw: dict[str, Any]) -> _PosSnap | None:
        """Convert a broker position dict to _PosSnap. Returns None on bad input."""
        try:
            ticket = int(raw.get("ticket"))
        except (TypeError, ValueError):
            return None
        symbol = str(raw.get("symbol", ""))
        side = str(raw.get("side") or raw.get("type") or "").lower()
        volume = float(raw.get("volume", 0.0) or 0.0)
        sl = float(raw.get("sl", 0.0) or 0.0)
        tp = float(raw.get("tp", 0.0) or 0.0)
        open_price = float(raw.get("open_price", raw.get("price", 0.0)) or 0.0)
        # CF-2: fall back to now() if bridge dict lacks open_time;
        # preserve previously recorded open_time across polls.
        open_time = raw.get("open_time")
        if not open_time:
            prev = self._last_snapshot.get(ticket)
            open_time = prev.open_time if prev else datetime.now(timezone.utc).isoformat()
        return _PosSnap(
            ticket=ticket,
            symbol=symbol,
            side=side,
            volume=volume,
            sl=sl,
            tp=tp,
            open_price=open_price,
            open_time=str(open_time),
        )

    def _now_iso(self) -> str:
        # Use real wall clock for log timestamps, even if injected clock is fake.
        # Tests can monkey-patch datetime if they care.
        return datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------ #
    # Sync poll (testable without a thread)                              #
    # ------------------------------------------------------------------ #

    def poll_once(self) -> dict[str, Any]:
        """Single polling pass. Never raises — exceptions are logged and swallowed.

        Returns counts dict: {"opened": int, "modified": int, "closed": int}
        or {"error": str} on failure.
        """
        try:
            current_raw = self.broker.get_positions() or []
            closed_raw = self.broker.get_closed() or []

            current: dict[int, _PosSnap] = {}
            for raw in current_raw:
                snap = self._to_snap(raw)
                if snap is not None:
                    current[snap.ticket] = snap

            opened, modified = _diff(current, self._last_snapshot)

            for snap in opened:
                self._writer.write({
                    "ts": self._now_iso(),
                    "event": "opened",
                    "ticket": snap.ticket,
                    "symbol": snap.symbol,
                    "side": snap.side,
                    "volume": snap.volume,
                    "sl": snap.sl,
                    "tp": snap.tp,
                    "open_price": snap.open_price,
                    "open_time": snap.open_time,
                    "close_price": None,
                    "profit": None,
                    "reason": None,
                })

            for snap, changes in modified:
                self._writer.write({
                    "ts": self._now_iso(),
                    "event": "modified",
                    "ticket": snap.ticket,
                    "symbol": snap.symbol,
                    "side": snap.side,
                    "volume": snap.volume,
                    "sl": snap.sl,
                    "tp": snap.tp,
                    "open_price": snap.open_price,
                    "open_time": snap.open_time,
                    "changes": changes,
                    "close_price": None,
                    "profit": None,
                    "reason": None,
                })

            # CF-1: dedupe closed events
            new_closed = []
            for raw in closed_raw:
                try:
                    ticket = int(raw.get("ticket"))
                except (TypeError, ValueError):
                    continue
                if ticket in self._seen_closed_tickets:
                    continue
                self._seen_closed_tickets.add(ticket)
                new_closed.append(raw)

            for raw in new_closed:
                profit = raw.get("profit")
                try:
                    profit_f = float(profit) if profit is not None else None
                except (TypeError, ValueError):
                    profit_f = None
                event = {
                    "ts": self._now_iso(),
                    "event": "closed",
                    "ticket": int(raw.get("ticket")),
                    "symbol": str(raw.get("symbol", "")),
                    "side": str(raw.get("side") or raw.get("type") or "").lower(),
                    "volume": float(raw.get("volume", 0.0) or 0.0),
                    "sl": float(raw.get("sl", 0.0) or 0.0),
                    "tp": float(raw.get("tp", 0.0) or 0.0),
                    "open_price": float(raw.get("open_price", 0.0) or 0.0),
                    "close_price": float(raw.get("close_price", raw.get("price", 0.0)) or 0.0),
                    "profit": profit_f,
                    "reason": raw.get("reason"),
                }
                self._writer.write(event)
                self._alerter.on_close(event)

            self._last_snapshot = current
            return {
                "opened": len(opened),
                "modified": len(modified),
                "closed": len(new_closed),
            }
        except Exception as exc:  # noqa: BLE001 — daemon must keep running
            self._logger.exception("PositionMonitor.poll_once failed")
            return {"error": str(exc)}

    # ------------------------------------------------------------------ #
    # Daemon-thread lifecycle                                            #
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Spawn the daemon poller thread. Idempotent — second call is no-op."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="PositionMonitor",
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Signal stop and join the thread. Idempotent."""
        self._stop_event.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=timeout)
        self._thread = None
        try:
            self._writer.close()
        except Exception:
            pass

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.poll_once()
            # Use Event.wait so stop() interrupts sleep promptly
            self._stop_event.wait(self.poll_interval_s)


__all__ = ["PositionMonitor"]
