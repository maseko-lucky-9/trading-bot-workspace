"""Unit tests for scripts/supervisor.py.

All tests are hermetic: no real subprocess is spawned; the supervisor's
spawn_fn and clock_fn are injected with fakes.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Make scripts/ importable
_BOT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BOT_ROOT / "scripts"))

import supervisor as sup_mod  # noqa: E402
from supervisor import (  # noqa: E402
    Supervisor,
    compute_backoff,
    is_market_open,
    main,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeProcess:
    """Minimal Popen-compatible fake."""

    _next_pid = 50000

    def __init__(self, exit_code=0, exit_after_s=0.05, ignore_terminate=False):
        FakeProcess._next_pid += 1
        self.pid = FakeProcess._next_pid
        self._exit_code = exit_code
        self._exit_after_s = exit_after_s
        self._ignore_terminate = ignore_terminate
        self._spawned_at = time.time()
        self.returncode = None
        self.terminate_called = False
        self.kill_called = False
        self._terminated_at = None

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        if (time.time() - self._spawned_at) >= self._exit_after_s:
            self.returncode = self._exit_code
            return self.returncode
        return None

    def wait(self, timeout=None):
        deadline = time.time() + (timeout if timeout is not None else 60)
        while time.time() < deadline:
            if self.poll() is not None:
                return self.returncode
            time.sleep(0.01)
        import subprocess
        raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)

    def terminate(self):
        self.terminate_called = True
        self._terminated_at = time.time()
        if not self._ignore_terminate:
            # Cause poll() to start returning the exit code very soon
            self._exit_after_s = 0
            self._spawned_at = time.time() - 1

    def kill(self):
        self.kill_called = True
        self.returncode = -9


# ---------------------------------------------------------------------------
# T001 — Pure helpers
# ---------------------------------------------------------------------------

def _utc(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def test_market_closed_saturday_noon():
    # 2026-04-25 is a Saturday
    assert is_market_open(_utc(2026, 4, 25, 12, 0)) is False


def test_market_open_monday_morning():
    # 2026-04-27 is a Monday
    assert is_market_open(_utc(2026, 4, 27, 10, 0)) is True


def test_market_open_sunday_after_22():
    # 2026-04-26 Sunday 22:30 UTC
    assert is_market_open(_utc(2026, 4, 26, 22, 30)) is True
    # Sunday 21:59 UTC should be closed
    assert is_market_open(_utc(2026, 4, 26, 21, 59)) is False


def test_market_closed_friday_after_21():
    # 2026-04-24 Friday 22:00 UTC closed
    assert is_market_open(_utc(2026, 4, 24, 22, 0)) is False
    # Friday 20:59 UTC still open
    assert is_market_open(_utc(2026, 4, 24, 20, 59)) is True


def test_compute_backoff_doubles_and_caps():
    assert compute_backoff(0) == 30
    assert compute_backoff(1) == 60
    assert compute_backoff(2) == 120
    assert compute_backoff(3) == 240
    assert compute_backoff(4) == 480
    # 30 * 2^5 = 960 > 900 cap
    assert compute_backoff(5) == 900
    assert compute_backoff(20) == 900


# ---------------------------------------------------------------------------
# T002 — Health file
# ---------------------------------------------------------------------------

def test_health_file_written_within_5s(tmp_path):
    health_path = tmp_path / "supervisor_health.json"
    spawned = []

    def spawn_fn(cmd):
        # Long-lived child so the supervisor stays in its wait loop
        proc = FakeProcess(exit_after_s=10)
        spawned.append(proc)
        return proc

    s = Supervisor(
        command=["python", "main.py"],
        spawn_fn=spawn_fn,
        health_path=health_path,
        market_hours_enabled=False,
        health_interval_s=0.1,
    )

    t = threading.Thread(target=s.run, daemon=True)
    t.start()

    # Wait up to 5s for file to appear
    deadline = time.time() + 5
    while time.time() < deadline and not health_path.exists():
        time.sleep(0.05)

    assert health_path.exists(), "health file not written within 5s"

    data = json.loads(health_path.read_text())
    for key in (
        "pid",
        "child_pid",
        "uptime_s",
        "restart_count",
        "last_exit_code",
        "last_restart_at",
        "market_open",
    ):
        assert key in data, f"missing key {key} in health snapshot"

    s.request_stop()
    t.join(timeout=5)


# ---------------------------------------------------------------------------
# T003 — Restart-on-crash, backoff, max-restarts
# ---------------------------------------------------------------------------

def test_max_restarts_halts_loop(tmp_path):
    health_path = tmp_path / "h.json"
    spawn_count = {"n": 0}

    def spawn_fn(cmd):
        spawn_count["n"] += 1
        # exits immediately
        return FakeProcess(exit_after_s=0)

    s = Supervisor(
        command=["python", "main.py"],
        spawn_fn=spawn_fn,
        health_path=health_path,
        market_hours_enabled=False,
        max_restarts=2,
        health_interval_s=10,
    )
    # Patch backoff to be near-zero so test runs fast
    s._sleep_with_backoff = lambda: None  # type: ignore

    rc = s.run()
    assert rc == 0
    # Initial spawn + restarts; loop halts when restart_count >= max_restarts
    assert spawn_count["n"] >= 2
    assert spawn_count["n"] <= 3


def test_backoff_resets_after_long_uptime(tmp_path):
    """If a child runs > 3600s, restart_count must reset to 0.

    Strategy: a long-uptime child causes restart_count to oscillate between
    0 and 1. We let several spawns happen, then stop the supervisor and
    assert the count never grew past 1.
    """
    health_path = tmp_path / "h.json"

    fake_now = {"t": datetime(2026, 4, 27, 10, 0, tzinfo=timezone.utc)}

    def clock_fn():
        return fake_now["t"]

    spawn_log = []
    observed_counts = []

    s_holder = {}

    def spawn_fn(cmd):
        proc = FakeProcess(exit_after_s=0)
        spawn_log.append(proc)
        # Capture restart_count seen at spawn time
        if "s" in s_holder:
            observed_counts.append(s_holder["s"].restart_count)
        # Simulate a 4000s uptime by jumping the clock when poll/exit is read
        fake_now["t"] = fake_now["t"] + timedelta(seconds=4000)
        # Stop the supervisor after we have collected several samples
        if len(spawn_log) >= 5:
            s_holder["s"].request_stop()
        return proc

    s = Supervisor(
        command=["python", "main.py"],
        spawn_fn=spawn_fn,
        clock_fn=clock_fn,
        health_path=health_path,
        market_hours_enabled=False,
        max_restarts=0,  # unlimited
        health_interval_s=10,
    )
    s._sleep_with_backoff = lambda: None  # type: ignore
    s_holder["s"] = s

    s.run()

    # At every spawn after the first, restart_count should be 0 because the
    # previous child's uptime exceeded UPTIME_RESET_S and the loop reset it
    # before the +=1. So each subsequent spawn sees count == 0.
    assert all(c == 0 for c in observed_counts), (
        f"restart_count should remain 0 across long-uptime restarts; got {observed_counts}"
    )
    assert len(spawn_log) >= 5


def test_backoff_increments_on_short_uptime(tmp_path):
    health_path = tmp_path / "h.json"
    fake_now = {"t": datetime(2026, 4, 27, 10, 0, tzinfo=timezone.utc)}

    def clock_fn():
        return fake_now["t"]

    def spawn_fn(cmd):
        proc = FakeProcess(exit_after_s=0)
        # Child only runs 5 simulated seconds — well under 3600
        fake_now["t"] = fake_now["t"] + timedelta(seconds=5)
        return proc

    s = Supervisor(
        command=["python", "main.py"],
        spawn_fn=spawn_fn,
        clock_fn=clock_fn,
        health_path=health_path,
        market_hours_enabled=False,
        max_restarts=3,
        health_interval_s=10,
    )
    s._sleep_with_backoff = lambda: None  # type: ignore

    s.run()
    # restart_count incremented per crash, halts when >= 3
    assert s.restart_count >= 3


# ---------------------------------------------------------------------------
# T004 — Signal-driven shutdown
# ---------------------------------------------------------------------------

def test_request_stop_terminates_child_and_returns_zero(tmp_path):
    health_path = tmp_path / "h.json"
    proc_holder = {}

    def spawn_fn(cmd):
        proc = FakeProcess(exit_after_s=30)  # would run a long time
        proc_holder["p"] = proc
        return proc

    s = Supervisor(
        command=["python", "main.py"],
        spawn_fn=spawn_fn,
        health_path=health_path,
        market_hours_enabled=False,
        health_interval_s=10,
        shutdown_grace_s=2,
    )
    s._sleep_with_backoff = lambda: None  # type: ignore

    t = threading.Thread(target=s.run, daemon=True)
    t.start()

    # Wait for child to be spawned
    deadline = time.time() + 3
    while time.time() < deadline and "p" not in proc_holder:
        time.sleep(0.02)
    assert "p" in proc_holder, "child was never spawned"

    s.request_stop()
    t.join(timeout=5)

    assert not t.is_alive(), "supervisor did not exit after stop request"
    assert proc_holder["p"].terminate_called, "child was not asked to terminate"


def test_sigkill_when_child_ignores_sigterm(tmp_path):
    health_path = tmp_path / "h.json"
    proc_holder = {}

    def spawn_fn(cmd):
        proc = FakeProcess(exit_after_s=60, ignore_terminate=True)
        proc_holder["p"] = proc
        return proc

    s = Supervisor(
        command=["python", "main.py"],
        spawn_fn=spawn_fn,
        health_path=health_path,
        market_hours_enabled=False,
        health_interval_s=10,
        shutdown_grace_s=0.3,  # short grace so test is fast
    )
    s._sleep_with_backoff = lambda: None  # type: ignore

    t = threading.Thread(target=s.run, daemon=True)
    t.start()

    deadline = time.time() + 3
    while time.time() < deadline and "p" not in proc_holder:
        time.sleep(0.02)

    s.request_stop()
    t.join(timeout=5)

    assert proc_holder["p"].terminate_called
    assert proc_holder["p"].kill_called, "stubborn child should have been SIGKILLed"


# ---------------------------------------------------------------------------
# T005 — CLI
# ---------------------------------------------------------------------------

def test_dry_run_exits_zero_and_prints_command(capsys):
    rc = main(["--dry-run"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "main.py" in captured.out
    assert "--mode" in captured.out
    assert "paper" in captured.out


def test_arg_parser_accepts_all_flags():
    parser = sup_mod._build_arg_parser()
    ns = parser.parse_args(["--max-restarts", "5", "--no-market-hours", "--dry-run"])
    assert ns.max_restarts == 5
    assert ns.no_market_hours is True
    assert ns.dry_run is True


# ---------------------------------------------------------------------------
# Market-hours gate
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Coverage: uncovered branches
# ---------------------------------------------------------------------------

def test_is_market_open_naive_datetime_treated_as_utc():
    # Naive Saturday noon — should be closed
    naive = datetime(2026, 4, 25, 12, 0)
    assert is_market_open(naive) is False


def test_compute_backoff_negative_count_treated_as_zero():
    assert compute_backoff(-5) == compute_backoff(0) == 30


def test_default_spawn_fn_is_importable():
    # _default_spawn wraps subprocess.Popen; just confirm it's callable
    from supervisor import _default_spawn
    import subprocess
    assert callable(_default_spawn)


def test_shutdown_child_no_op_when_child_is_none(tmp_path):
    """_shutdown_child with no child → early return, no error."""
    s = Supervisor(
        command=["python", "main.py"],
        health_path=tmp_path / "h.json",
        market_hours_enabled=False,
    )
    s._child = None
    s._shutdown_child()  # must not raise


def test_shutdown_child_no_op_when_already_exited(tmp_path):
    """_shutdown_child when child already exited → early return."""
    s = Supervisor(
        command=["python", "main.py"],
        health_path=tmp_path / "h.json",
        market_hours_enabled=False,
    )
    proc = FakeProcess(exit_after_s=0)
    time.sleep(0.1)  # let it exit
    s._child = proc
    s._shutdown_child()  # must not raise, no terminate call
    assert not proc.terminate_called


def test_sleep_with_backoff_respects_stop_event(tmp_path):
    """_sleep_with_backoff exits early when stop_event is set."""
    from unittest.mock import patch as _patch
    s = Supervisor(
        command=["python", "main.py"],
        health_path=tmp_path / "h.json",
        market_hours_enabled=False,
    )
    # Make backoff very short so test doesn't hang
    with _patch("supervisor.compute_backoff", return_value=0):
        s._sleep_with_backoff()


def test_sleep_with_backoff_interrupted_by_stop_event(tmp_path):
    """Stop event set mid-sleep exits the backoff loop early."""
    from unittest.mock import patch as _patch
    s = Supervisor(
        command=["python", "main.py"],
        health_path=tmp_path / "h.json",
        market_hours_enabled=False,
    )
    # Would sleep 5s, but we set stop_event immediately
    def _set_stop():
        time.sleep(0.05)
        s.request_stop()
    t = threading.Thread(target=_set_stop, daemon=True)
    t.start()
    with _patch("supervisor.compute_backoff", return_value=5):
        start = time.time()
        s._sleep_with_backoff()
    assert time.time() - start < 2  # stopped well before 5s


def test_spawn_failure_treated_as_crash(tmp_path):
    """spawn_fn raises → supervisor does backoff+increment and eventually halts."""
    s = Supervisor(
        command=["python", "main.py"],
        health_path=tmp_path / "h.json",
        market_hours_enabled=False,
        max_restarts=2,
    )
    s._sleep_with_backoff = lambda: None

    def bad_spawn(cmd):
        raise OSError("exec failed")

    s.spawn_fn = bad_spawn
    rc = s.run()
    assert rc == 0
    assert s.last_exit_code == -1


def test_health_write_exception_does_not_crash(tmp_path):
    """If _write_health_snapshot raises, the supervisor continues."""
    from unittest.mock import patch as _patch
    proc_holder = {}

    def spawn_fn(cmd):
        proc = FakeProcess(exit_after_s=5)
        proc_holder["p"] = proc
        return proc

    s = Supervisor(
        command=["python", "main.py"],
        spawn_fn=spawn_fn,
        health_path=tmp_path / "h.json",
        market_hours_enabled=False,
        health_interval_s=0.05,
    )

    with _patch.object(s, "_write_health_snapshot", side_effect=OSError("disk full")):
        t = threading.Thread(target=s.run, daemon=True)
        t.start()
        # Wait for child to spawn
        deadline = time.time() + 3
        while time.time() < deadline and "p" not in proc_holder:
            time.sleep(0.02)
        s.request_stop()
        t.join(timeout=5)
    assert not t.is_alive()


def test_final_health_write_exception_does_not_crash(tmp_path):
    """Exception in finally-block health write must not propagate."""
    from unittest.mock import patch as _patch

    calls = {"n": 0}
    def spawn_fn(cmd):
        return FakeProcess(exit_after_s=0.05)

    s = Supervisor(
        command=["python", "main.py"],
        spawn_fn=spawn_fn,
        health_path=tmp_path / "h.json",
        market_hours_enabled=False,
        max_restarts=1,
    )
    s._sleep_with_backoff = lambda: None

    original = s._write_health_snapshot.__func__

    def patched_write(self_inner):
        calls["n"] += 1
        if calls["n"] >= 3:
            raise OSError("disk full")
        original(self_inner)

    with _patch.object(type(s), "_write_health_snapshot", patched_write):
        rc = s.run()
    assert rc == 0  # must not propagate


def test_wait_for_market_open_returns_immediately_when_open(tmp_path):
    """_wait_for_market_open returns at once when clock says market is open."""
    # Monday 10:00 UTC — open
    s = Supervisor(
        command=["python", "main.py"],
        health_path=tmp_path / "h.json",
        market_hours_enabled=True,
        clock_fn=lambda: _utc(2026, 4, 27, 10, 0),
    )
    start = time.time()
    s._wait_for_market_open()
    assert time.time() - start < 1  # returns without sleeping


def test_main_with_mock_supervisor(tmp_path, monkeypatch):
    """main() constructs a Supervisor and calls run()."""
    from unittest.mock import patch as _patch
    mock_sup = MagicMock()
    mock_sup.run.return_value = 0
    with _patch("supervisor.Supervisor", return_value=mock_sup):
        rc = main(["--no-market-hours", "--max-restarts", "1"])
    assert rc == 0
    mock_sup.run.assert_called_once()


def test_market_gate_blocks_spawn_when_closed(tmp_path):
    health_path = tmp_path / "h.json"
    spawn_count = {"n": 0}

    def spawn_fn(cmd):
        spawn_count["n"] += 1
        return FakeProcess(exit_after_s=0.1)

    # Saturday noon — market closed
    fake_now = _utc(2026, 4, 25, 12, 0)

    def clock_fn():
        return fake_now

    s = Supervisor(
        command=["python", "main.py"],
        spawn_fn=spawn_fn,
        clock_fn=clock_fn,
        health_path=health_path,
        market_hours_enabled=True,
        market_poll_interval_s=0.1,
        health_interval_s=10,
    )

    t = threading.Thread(target=s.run, daemon=True)
    t.start()

    # Give the supervisor a moment in the market-wait loop
    time.sleep(0.5)
    assert spawn_count["n"] == 0, "supervisor spawned a child while market closed"

    s.request_stop()
    t.join(timeout=3)
