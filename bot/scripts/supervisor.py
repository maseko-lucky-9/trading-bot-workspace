"""
supervisor.py — unattended process supervisor for the MT5 trading bot.

Spawns `python main.py --mode paper` as a managed subprocess and:
  * restarts on crash with exponential backoff (30s → 900s, 2x),
  * resets the backoff counter when the child has run > 3600s,
  * writes a health snapshot to bridge_data/supervisor_health.json every 30s,
  * gates start/restart on forex market hours (Sun 22:00 → Fri 21:00 UTC),
  * shuts the child down gracefully on SIGTERM/SIGINT (10s grace, then SIGKILL).

Usage:
    python scripts/supervisor.py [--max-restarts N] [--dry-run] [--no-market-hours]

The Supervisor class accepts injected `spawn_fn` and `clock_fn` callables so the
full lifecycle is unit-testable without spawning a real child process.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

BOT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HEALTH_PATH = BOT_ROOT / "bridge_data" / "supervisor_health.json"
DEFAULT_COMMAND = [sys.executable, str(BOT_ROOT / "main.py"), "--mode", "paper"]

BACKOFF_BASE_S = 30
BACKOFF_MULT = 2
BACKOFF_CAP_S = 900
UPTIME_RESET_S = 3600
HEALTH_WRITE_INTERVAL_S = 30
MARKET_POLL_INTERVAL_S = 60
SHUTDOWN_GRACE_S = 10


# ---------------------------------------------------------------------------
# Pure helpers (T001)
# ---------------------------------------------------------------------------

def is_market_open(now_utc: datetime) -> bool:
    """Forex market is open Sunday 22:00 UTC → Friday 21:00 UTC.

    weekday(): Monday=0 ... Sunday=6.
    Open conditions:
      * Sunday (6) and hour >= 22
      * Monday (0) - Thursday (3) any time
      * Friday (4) and hour < 21
    Closed:
      * Saturday (5) all day
      * Sunday before 22:00
      * Friday from 21:00 onwards
    """
    if now_utc.tzinfo is None:
        # treat naive as UTC
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    wd = now_utc.weekday()
    hour = now_utc.hour
    if wd == 5:  # Saturday
        return False
    if wd == 6:  # Sunday
        return hour >= 22
    if wd == 4:  # Friday
        return hour < 21
    # Monday-Thursday
    return True


def compute_backoff(
    restart_count: int,
    base: int = BACKOFF_BASE_S,
    mult: int = BACKOFF_MULT,
    cap: int = BACKOFF_CAP_S,
) -> int:
    """Exponential backoff: base * mult^restart_count, capped at cap.

    restart_count is 0-indexed (first restart -> base).
    """
    if restart_count < 0:
        restart_count = 0
    delay = base * (mult ** restart_count)
    return min(delay, cap)


# ---------------------------------------------------------------------------
# Supervisor class (T002 - T004)
# ---------------------------------------------------------------------------

def _default_spawn(cmd):
    return subprocess.Popen(cmd)


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


class Supervisor:
    """Manages the bot subprocess lifecycle.

    Parameters
    ----------
    command:
        Argv list to spawn (e.g. ["python", "main.py", "--mode", "paper"]).
    spawn_fn:
        Callable taking the command list and returning a process-like object
        (must expose `pid`, `poll()`, `wait(timeout)`, `terminate()`, `kill()`,
        `returncode`). Defaults to `subprocess.Popen`.
    clock_fn:
        Callable returning a tz-aware UTC datetime. Defaults to `datetime.now(UTC)`.
    health_path:
        Path to the health JSON file.
    max_restarts:
        Stop after this many restarts. 0 means unlimited.
    market_hours_enabled:
        If True (default), block start/restart while the forex market is closed.
    health_interval_s / market_poll_interval_s / shutdown_grace_s:
        Tunable timings (overridable for tests).
    """

    def __init__(
        self,
        command: Optional[list] = None,
        spawn_fn: Callable = _default_spawn,
        clock_fn: Callable[[], datetime] = _default_clock,
        health_path: Path = DEFAULT_HEALTH_PATH,
        max_restarts: int = 0,
        market_hours_enabled: bool = True,
        health_interval_s: float = HEALTH_WRITE_INTERVAL_S,
        market_poll_interval_s: float = MARKET_POLL_INTERVAL_S,
        shutdown_grace_s: float = SHUTDOWN_GRACE_S,
    ) -> None:
        self.command = list(command) if command else list(DEFAULT_COMMAND)
        self.spawn_fn = spawn_fn
        self.clock_fn = clock_fn
        self.health_path = Path(health_path)
        self.max_restarts = max_restarts
        self.market_hours_enabled = market_hours_enabled
        self.health_interval_s = health_interval_s
        self.market_poll_interval_s = market_poll_interval_s
        self.shutdown_grace_s = shutdown_grace_s

        self.pid = os.getpid()
        self._stop_event = threading.Event()
        self._health_thread: Optional[threading.Thread] = None

        # Mutable state observed by the health thread
        self._child = None
        self._child_started_at: Optional[datetime] = None
        self.restart_count = 0
        self.last_exit_code: Optional[int] = None
        self.last_restart_at: Optional[datetime] = None

    # ------------------------------------------------------------------ health
    def _build_health_snapshot(self) -> dict:
        now = self.clock_fn()
        if self._child is not None and self._child_started_at is not None:
            uptime = max(0, int((now - self._child_started_at).total_seconds()))
            child_pid = getattr(self._child, "pid", None)
        else:
            uptime = 0
            child_pid = None
        return {
            "pid": self.pid,
            "child_pid": child_pid,
            "uptime_s": uptime,
            "restart_count": self.restart_count,
            "last_exit_code": self.last_exit_code,
            "last_restart_at": (
                self.last_restart_at.isoformat() if self.last_restart_at else None
            ),
            "market_open": is_market_open(now),
        }

    def _write_health_snapshot(self) -> None:
        snapshot = self._build_health_snapshot()
        self.health_path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: temp file in same dir + os.replace
        fd, tmp_path = tempfile.mkstemp(
            prefix=".supervisor_health.", suffix=".tmp", dir=str(self.health_path.parent)
        )
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(snapshot, fh, indent=2)
            os.replace(tmp_path, self.health_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _health_loop(self) -> None:
        # First write immediately so the file exists < 5s of start.
        try:
            self._write_health_snapshot()
        except Exception:
            pass
        while not self._stop_event.wait(self.health_interval_s):
            try:
                self._write_health_snapshot()
            except Exception:
                # Never let health-writer crash the supervisor
                pass

    # ----------------------------------------------------------- market gating
    def _wait_for_market_open(self) -> None:
        if not self.market_hours_enabled:
            return
        while not self._stop_event.is_set():
            if is_market_open(self.clock_fn()):
                return
            # Sleep in bounded chunks so stop_event is responsive
            self._stop_event.wait(self.market_poll_interval_s)

    # --------------------------------------------------------------- shutdown
    def _shutdown_child(self) -> None:
        child = self._child
        if child is None:
            return
        if child.poll() is not None:
            return
        try:
            child.terminate()
        except Exception:
            pass
        try:
            child.wait(timeout=self.shutdown_grace_s)
        except subprocess.TimeoutExpired:
            try:
                child.kill()
            except Exception:
                pass
            try:
                child.wait(timeout=2)
            except Exception:
                pass
        except Exception:
            pass

    def request_stop(self) -> None:
        """Idempotent stop request — used by signal handlers and tests."""
        self._stop_event.set()

    def _install_signal_handlers(self) -> None:
        def _handler(signum, frame):  # noqa: ARG001
            self.request_stop()

        # Only install in the main thread; tests may already own the handlers.
        try:
            signal.signal(signal.SIGTERM, _handler)
            signal.signal(signal.SIGINT, _handler)
        except (ValueError, OSError):
            # ValueError: not in main thread (some test runners)
            pass

    # -------------------------------------------------------------- main loop
    def run(self) -> int:
        self._install_signal_handlers()
        self._health_thread = threading.Thread(
            target=self._health_loop, name="supervisor-health", daemon=True
        )
        self._health_thread.start()

        try:
            while not self._stop_event.is_set():
                # Market gate
                self._wait_for_market_open()
                if self._stop_event.is_set():
                    break

                # Spawn child
                self._child_started_at = self.clock_fn()
                self.last_restart_at = self._child_started_at
                try:
                    self._child = self.spawn_fn(self.command)
                except Exception:
                    # Treat spawn failure as a crash for backoff purposes
                    self._child = None
                    self.last_exit_code = -1
                    self._sleep_with_backoff()
                    self.restart_count += 1
                    if self.max_restarts and self.restart_count >= self.max_restarts:
                        break
                    continue

                # Wait for child to exit (poll so we can react to stop_event)
                while not self._stop_event.is_set():
                    rc = self._child.poll()
                    if rc is not None:
                        self.last_exit_code = rc
                        break
                    time.sleep(0.2)

                if self._stop_event.is_set():
                    self._shutdown_child()
                    # Capture final exit code
                    rc = self._child.poll() if self._child is not None else None
                    if rc is not None:
                        self.last_exit_code = rc
                    break

                # Child exited on its own — decide whether to backoff or reset
                child_uptime_s = (
                    self.clock_fn() - self._child_started_at
                ).total_seconds()
                if child_uptime_s > UPTIME_RESET_S:
                    self.restart_count = 0
                else:
                    self.restart_count += 1
                self._child = None
                self._child_started_at = None

                if self.max_restarts and self.restart_count >= self.max_restarts:
                    break

                self._sleep_with_backoff()
        finally:
            self._stop_event.set()
            if self._health_thread is not None:
                self._health_thread.join(timeout=2)
            try:
                self._write_health_snapshot()
            except Exception:
                pass

        return 0

    def _sleep_with_backoff(self) -> None:
        delay = compute_backoff(self.restart_count)
        # Sleep in chunks so a stop request is responsive
        end = time.time() + delay
        while time.time() < end and not self._stop_event.is_set():
            self._stop_event.wait(min(1.0, end - time.time()))


# ---------------------------------------------------------------------------
# CLI (T005)
# ---------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="supervisor",
        description="Unattended supervisor for the MT5 paper-trading bot.",
    )
    p.add_argument(
        "--max-restarts",
        type=int,
        default=0,
        help="Halt after N restarts (0 = unlimited, default 0).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the command that would be run, then exit.",
    )
    p.add_argument(
        "--no-market-hours",
        action="store_true",
        help="Skip the forex market-hours gate.",
    )
    return p


def main(argv: Optional[list] = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    if args.dry_run:
        print("supervisor would run:", " ".join(DEFAULT_COMMAND))
        return 0

    sup = Supervisor(
        command=DEFAULT_COMMAND,
        max_restarts=args.max_restarts,
        market_hours_enabled=not args.no_market_hours,
    )
    return sup.run()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
