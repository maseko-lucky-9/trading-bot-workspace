"""Unit tests for core.monitoring.position_monitor.

Covers: _diff() purity, _JsonlWriter rotation+cleanup, _Alerter log-only
behaviour (incl. no-Slack source guard), PositionMonitor.poll_once()
end-to-end with a fake broker, and start()/stop() thread lifecycle.
"""
from __future__ import annotations

import inspect
import io
import json
import logging
import os
import time
from pathlib import Path

import pytest

from core.monitoring import position_monitor as pm
from core.monitoring.position_monitor import (
    PositionMonitor,
    _Alerter,
    _JsonlWriter,
    _PosSnap,
    _diff,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _snap(ticket: int, **overrides) -> _PosSnap:
    base = dict(
        ticket=ticket,
        symbol="USDJPY",
        side="buy",
        volume=0.10,
        sl=154.20,
        tp=156.10,
        open_price=154.85,
        open_time="2026-04-26T12:00:00+00:00",
    )
    base.update(overrides)
    return _PosSnap(**base)


class _FakeBroker:
    """In-memory broker stub for poll_once() tests."""

    def __init__(self):
        self.open_positions: list[dict] = []
        self.closed_queue: list[dict] = []  # consumed (cleared) on each get_closed()
        self.raise_on_get_positions: Exception | None = None

    def get_positions(self) -> list[dict]:
        if self.raise_on_get_positions is not None:
            raise self.raise_on_get_positions
        return list(self.open_positions)

    def get_closed(self) -> list[dict]:
        out = list(self.closed_queue)
        self.closed_queue.clear()
        return out


# --------------------------------------------------------------------------- #
# T009: _diff() and _PosSnap                                                  #
# --------------------------------------------------------------------------- #


def test_diff_detects_opened():
    current = {1: _snap(1)}
    previous: dict[int, _PosSnap] = {}
    opened, modified = _diff(current, previous)
    assert opened == [_snap(1)]
    assert modified == []


def test_diff_detects_modified_sl_tp_volume():
    previous = {1: _snap(1, sl=154.20)}
    current = {1: _snap(1, sl=155.00)}
    opened, modified = _diff(current, previous)
    assert opened == []
    assert len(modified) == 1
    snap, changes = modified[0]
    assert snap.sl == 155.00
    assert "sl" in changes
    assert "tp" not in changes
    assert "volume" not in changes


def test_diff_ignores_unchanged():
    previous = {1: _snap(1)}
    current = {1: _snap(1)}
    opened, modified = _diff(current, previous)
    assert opened == []
    assert modified == []


# --------------------------------------------------------------------------- #
# T010: _JsonlWriter rotation + cleanup                                       #
# --------------------------------------------------------------------------- #


def test_jsonl_writer_appends_one_line_per_event(tmp_path):
    path = tmp_path / "p.jsonl"
    w = _JsonlWriter(str(path))
    try:
        for i in range(5):
            w.write({"event": "opened", "ticket": i})
    finally:
        w.close()
    lines = path.read_text().splitlines()
    assert len(lines) == 5
    for i, line in enumerate(lines):
        parsed = json.loads(line)
        assert parsed["ticket"] == i


def test_jsonl_writer_rotates_at_max_bytes(tmp_path):
    path = tmp_path / "p.jsonl"
    # Each event ~50 bytes; 50 events well over 200 bytes
    w = _JsonlWriter(str(path), max_bytes=200, backup_count=5)
    try:
        for i in range(50):
            w.write({"event": "opened", "ticket": i, "pad": "x" * 20})
    finally:
        w.close()
    rotated = list(tmp_path.glob("p.jsonl.*"))
    assert rotated, "expected at least one rotated file"


def test_jsonl_writer_cleans_old_files(tmp_path):
    path = tmp_path / "p.jsonl"
    # Construct the writer FIRST so the __init__ cleanup runs on an empty dir,
    # then create the old rotated file — ensuring the explicit call below is the
    # one that deletes it (not the init).
    w = _JsonlWriter(str(path), retention_days=7)
    try:
        rotated = tmp_path / "p.jsonl.5"
        rotated.write_text("old\n")
        old_ts = time.time() - (10 * 86400)  # 10 days ago
        os.utime(rotated, (old_ts, old_ts))

        removed = w._cleanup_old(force=True)
        assert removed >= 1
    finally:
        w.close()
    assert not rotated.exists()


# --------------------------------------------------------------------------- #
# T011: _Alerter — log-only (no Slack)                                        #
# --------------------------------------------------------------------------- #


def test_alerter_prints_fill_on_close(capsys):
    logger = logging.getLogger("test_alerter_print")
    alerter = _Alerter(alert_loss_usd=50.0, logger=logger)
    event = {
        "ticket": 99,
        "symbol": "USDJPY",
        "profit": 12.34,
        "ts": "2026-04-26T13:00:00+00:00",
    }
    alerter.on_close(event)
    captured = capsys.readouterr()
    assert "[FILL] ticket=99" in captured.out
    assert "symbol=USDJPY" in captured.out
    assert "profit=$12.34" in captured.out


def test_alerter_warns_on_loss_above_threshold(caplog):
    logger = logging.getLogger("test_alerter_warn")
    alerter = _Alerter(alert_loss_usd=50.0, logger=logger)
    event = {"ticket": 7, "symbol": "USDJPY", "profit": -75.0, "ts": "2026-04-26T13:01:00+00:00"}
    with caplog.at_level(logging.WARNING, logger="test_alerter_warn"):
        emitted = alerter.maybe_alert(event)
    assert emitted is True
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "LOSS_ALERT" in msg
    assert "ticket=7" in msg
    assert "profit=$-75.00" in msg


def test_alerter_silent_on_small_loss(caplog):
    logger = logging.getLogger("test_alerter_silent")
    alerter = _Alerter(alert_loss_usd=50.0, logger=logger)
    event = {"ticket": 8, "symbol": "USDJPY", "profit": -25.0}
    with caplog.at_level(logging.WARNING, logger="test_alerter_silent"):
        emitted = alerter.maybe_alert(event)
    assert emitted is False
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and r.name == "test_alerter_silent"]
    assert warnings == []


def test_alerter_no_slack_imports():
    """Enforces design Decision 3: log-only alerts. No network surface."""
    src = inspect.getsource(pm)
    assert "urllib" not in src, "urllib must not appear in position_monitor (Decision 3)"
    assert "SLACK_WEBHOOK_URL" not in src, "SLACK_WEBHOOK_URL must not appear (Decision 3)"
    assert "requests" not in src, "requests must not appear (Decision 3)"
    assert "import http.client" not in src
    assert "urlopen" not in src


# --------------------------------------------------------------------------- #
# T012: PositionMonitor.poll_once() end-to-end                                #
# --------------------------------------------------------------------------- #


def _make_monitor(tmp_path, alert_loss=50.0, logger_name="test_monitor"):
    cfg = {
        "monitoring": {
            "poll_interval_s": 5,
            "log_path": str(tmp_path / "p.jsonl"),
        },
        "risk": {"alert_loss_usd": alert_loss},
    }
    broker = _FakeBroker()
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)
    monitor = PositionMonitor(broker, cfg, logger=logger, stdout=io.StringIO())
    return monitor, broker, cfg


def _read_events(log_path: str) -> list[dict]:
    return [json.loads(line) for line in Path(log_path).read_text().splitlines() if line]


def test_poll_once_records_open_modify_close(tmp_path, caplog):
    monitor, broker, cfg = _make_monitor(tmp_path, logger_name="test_poll_e2e")
    log_path = cfg["monitoring"]["log_path"]
    try:
        # First poll: 1 open
        broker.open_positions = [{
            "ticket": 1001, "symbol": "USDJPY", "side": "buy",
            "volume": 0.10, "sl": 154.20, "tp": 156.10, "open_price": 154.85,
            "open_time": "2026-04-26T12:00:00+00:00",
        }]
        counts = monitor.poll_once()
        assert counts == {"opened": 1, "modified": 0, "closed": 0}

        events = _read_events(log_path)
        assert len(events) == 1
        assert events[0]["event"] == "opened"
        assert events[0]["ticket"] == 1001

        # Second poll: same position, new SL → modified
        broker.open_positions = [{
            "ticket": 1001, "symbol": "USDJPY", "side": "buy",
            "volume": 0.10, "sl": 155.00, "tp": 156.10, "open_price": 154.85,
            "open_time": "2026-04-26T12:00:00+00:00",
        }]
        counts = monitor.poll_once()
        assert counts == {"opened": 0, "modified": 1, "closed": 0}
        events = _read_events(log_path)
        assert events[-1]["event"] == "modified"
        assert "sl" in events[-1]["changes"]

        # Third poll: position closed with profit=-100 → closed event + WARNING
        broker.open_positions = []
        broker.closed_queue = [{
            "ticket": 1001, "symbol": "USDJPY", "side": "buy",
            "volume": 0.10, "profit": -100.0, "close_price": 153.85,
            "open_price": 154.85, "reason": "sl",
        }]
        with caplog.at_level(logging.WARNING, logger="test_poll_e2e"):
            counts = monitor.poll_once()
        assert counts == {"opened": 0, "modified": 0, "closed": 1}
        events = _read_events(log_path)
        assert events[-1]["event"] == "closed"
        assert events[-1]["profit"] == -100.0

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING and r.name == "test_poll_e2e"]
        assert len(warnings) == 1
        assert "LOSS_ALERT" in warnings[0].getMessage()
        assert "ticket=1001" in warnings[0].getMessage()
    finally:
        monitor.stop()


def test_poll_once_swallows_broker_exception(tmp_path, caplog):
    monitor, broker, _ = _make_monitor(tmp_path, logger_name="test_poll_swallow")
    broker.raise_on_get_positions = RuntimeError("bridge dead")
    try:
        with caplog.at_level(logging.ERROR, logger="test_poll_swallow"):
            result = monitor.poll_once()
        assert "error" in result
        assert "bridge dead" in result["error"]
        # logger.exception emits at ERROR level
        errors = [r for r in caplog.records if r.levelno == logging.ERROR and r.name == "test_poll_swallow"]
        assert len(errors) >= 1
    finally:
        monitor.stop()


def test_poll_once_dedupes_cumulative_closed_results(tmp_path):
    """CF-1: even if broker returns the same closed ticket twice, we emit once."""
    monitor, broker, cfg = _make_monitor(tmp_path, logger_name="test_dedupe")
    log_path = cfg["monitoring"]["log_path"]
    try:
        closed_payload = {
            "ticket": 2002, "symbol": "USDJPY", "side": "sell",
            "volume": 0.10, "profit": 5.0, "close_price": 154.50,
            "open_price": 154.55,
        }
        # First poll: broker yields the close
        broker.closed_queue = [closed_payload]
        monitor.poll_once()
        # Second poll: broker (mistakenly) yields the same close again
        broker.closed_queue = [closed_payload]
        monitor.poll_once()
        events = _read_events(log_path)
        closed_events = [e for e in events if e["event"] == "closed"]
        assert len(closed_events) == 1, "duplicate close must be suppressed"
    finally:
        monitor.stop()


# --------------------------------------------------------------------------- #
# T013: start() / stop() thread lifecycle                                     #
# --------------------------------------------------------------------------- #


def test_start_creates_daemon_thread(tmp_path):
    monitor, _, _ = _make_monitor(tmp_path, logger_name="test_thread_1")
    # Drop poll_interval to keep test fast
    monitor.poll_interval_s = 0.05
    try:
        monitor.start()
        time.sleep(0.1)
        assert monitor._thread is not None
        assert monitor._thread.daemon is True
        assert monitor._thread.is_alive()
    finally:
        monitor.stop(timeout=1.0)
    # Give it a beat to fully exit
    time.sleep(0.05)
    assert monitor._thread is None or not monitor._thread.is_alive()


def test_start_is_idempotent(tmp_path):
    monitor, _, _ = _make_monitor(tmp_path, logger_name="test_thread_2")
    monitor.poll_interval_s = 0.05
    try:
        monitor.start()
        first = monitor._thread
        monitor.start()  # no-op
        second = monitor._thread
        assert first is second
    finally:
        monitor.stop(timeout=1.0)


def test_stop_is_idempotent(tmp_path):
    monitor, _, _ = _make_monitor(tmp_path, logger_name="test_thread_3")
    # Stop without start: no exception
    monitor.stop(timeout=0.1)
    # Stop twice: no exception
    monitor.start()
    monitor.poll_interval_s = 0.05
    monitor.stop(timeout=1.0)
    monitor.stop(timeout=0.1)
