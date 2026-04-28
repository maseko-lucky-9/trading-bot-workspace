"""Shared fixtures for the dashboard test suite.

Everything here keeps the tests offline:

* :func:`tmp_trades_csv` writes a deterministic mini-trades.csv to the
  test's tmp_path so :func:`dashboard.sources.read_trades` returns a
  predictable DataFrame without touching the live ``logs/trades.csv``.
* :func:`mock_bridge_ok` / :func:`mock_bridge_unreachable` patch
  ``urllib.request.urlopen`` with a context-manager-shaped fake.
* :func:`mock_pgrep_running` / :func:`mock_pgrep_none` patch
  ``subprocess.run`` so :func:`dashboard.sources.probe_process` returns
  predictable results without actually invoking ``pgrep``/``ps``.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


_CSV_HEADER = (
    "ticket,symbol,type,volume,open_price,open_time,"
    "close_price,close_time,profit,sl,tp\n"
)


def _row(
    ticket: int,
    symbol: str,
    typ: str,
    volume: float,
    open_price: float,
    open_time: str,
    close_price: str,
    close_time: str,
    profit: str,
    sl: float,
    tp: float,
) -> str:
    return (
        f"{ticket},{symbol},{typ},{volume},{open_price},{open_time},"
        f"{close_price},{close_time},{profit},{sl},{tp}\n"
    )


@pytest.fixture
def trades_csv_mixed(tmp_path: Path) -> Path:
    """Three closed wins/losses + two open positions (no close_time)."""
    p = tmp_path / "trades.csv"
    body = _CSV_HEADER
    body += _row(1001, "EURUSD", "BUY", 0.10, 1.1000, "2026-04-26T10:00:00+00:00",
                 "1.1010", "2026-04-26T11:00:00+00:00", "10.0", 1.0950, 1.1100)
    body += _row(1002, "EURUSD", "SELL", 0.10, 1.1010, "2026-04-26T12:00:00+00:00",
                 "1.1005", "2026-04-26T13:00:00+00:00", "5.0", 1.1060, 1.0960)
    body += _row(1003, "GBPUSD", "BUY", 0.05, 1.2500, "2026-04-26T14:00:00+00:00",
                 "1.2480", "2026-04-26T15:00:00+00:00", "-10.0", 1.2450, 1.2600)
    body += _row(1004, "EURUSD", "BUY", 0.10, 1.1015, "2026-04-26T16:00:00+00:00",
                 "", "", "", 1.0965, 1.1115)  # open
    body += _row(1005, "USDJPY", "SELL", 0.01, 159.50, "2026-04-26T17:00:00+00:00",
                 "", "", "", 160.00, 159.00)  # open
    p.write_text(body)
    return p


@pytest.fixture
def trades_csv_empty(tmp_path: Path) -> Path:
    """Header-only CSV — exercises the no-data branches."""
    p = tmp_path / "trades.csv"
    p.write_text(_CSV_HEADER)
    return p


@pytest.fixture
def fake_urlopen_ok():
    """Build a urlopen replacement that returns ``{pong:true,ea_connected:true}``."""

    def factory(payload: dict | None = None):
        payload = payload if payload is not None else {"pong": True, "ea_connected": True}
        body = json.dumps(payload).encode("utf-8")

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def _fake(url, timeout=None):  # noqa: ARG001 — signature must match
            return _Resp(body)

        return _fake

    return factory


@pytest.fixture
def fake_urlopen_unreachable():
    """Build a urlopen replacement that raises ``URLError``."""
    import urllib.error

    def _fake(url, timeout=None):  # noqa: ARG001
        raise urllib.error.URLError("Connection refused")

    return _fake


@pytest.fixture
def fake_subprocess_run_factory():
    """Build a ``subprocess.run`` replacement returning canned outputs.

    Pass a dict mapping ``tuple(args[0])`` -> ``(stdout, returncode)`` and
    the resulting callable will dispatch by the args list it was called with.
    """

    def factory(canned: dict):
        def _run(args, capture_output=False, text=False, timeout=None):  # noqa: ARG001
            key = tuple(args)
            if key in canned:
                stdout, returncode = canned[key]
            else:
                # Try a prefix match — useful for ``ps -p <pid> -o comm=``
                # where pid varies.
                stdout, returncode = "", 1
                for k, v in canned.items():
                    if len(k) <= len(key) and key[: len(k)] == k:
                        stdout, returncode = v
                        break
            mock = MagicMock()
            mock.stdout = stdout
            mock.returncode = returncode
            return mock

        return _run

    return factory
