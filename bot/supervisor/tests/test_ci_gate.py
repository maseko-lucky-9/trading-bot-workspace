"""Tests for ci_gate.py — check evaluation, quiet-period gate, Sharpe comparison, merge."""
import json
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
import sqlite3

from bot.supervisor.checkpoint import (
    bootstrap,
    create_iteration,
    update_iteration,
    insert_autoresearch_run,
)
from bot.supervisor.ci_gate import (
    _all_checks_pass,
    _quiet_period_ok,
    _sharpe_improved_for_combo,
    _global_sharpe_improved,
    process_patching_events,
)


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    bootstrap(db_path=db_path)
    return db_path


def _make_mock_row(
    event_id: int,
    pr_number: int,
    scope: str = "combo",
    combo_slug: str = "EURUSD_M15_mean_reversion",
) -> sqlite3.Row:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE t (id,pr_number,scope,combo_slug)"
    )
    conn.execute("INSERT INTO t VALUES (?,?,?,?)", (event_id, pr_number, scope, combo_slug))
    return conn.execute("SELECT * FROM t").fetchone()


class TestAllChecksPass:
    def test_all_success(self):
        checks = json.dumps([{"name": "test", "state": "SUCCESS"}])
        with patch("bot.supervisor.ci_gate._run", return_value=(0, checks)):
            assert _all_checks_pass(42) is True

    def test_one_failing(self):
        checks = json.dumps([
            {"name": "test", "state": "SUCCESS"},
            {"name": "lint", "state": "FAILURE"},
        ])
        with patch("bot.supervisor.ci_gate._run", return_value=(0, checks)):
            assert _all_checks_pass(42) is False

    def test_no_checks(self):
        with patch("bot.supervisor.ci_gate._run", return_value=(0, "[]")):
            assert _all_checks_pass(42) is False

    def test_gh_cli_error(self):
        with patch("bot.supervisor.ci_gate._run", return_value=(1, "error")):
            assert _all_checks_pass(42) is False


class TestQuietPeriodOk:
    def test_paper_mode_always_ok(self):
        assert _quiet_period_ok(42, "paper") is True

    def test_live_mode_not_enough_time(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        created_json = json.dumps({"createdAt": recent})
        with patch("bot.supervisor.ci_gate._run", return_value=(0, created_json)):
            assert _quiet_period_ok(42, "live", live_merge_quiet_seconds=86400) is False

    def test_live_mode_enough_time_no_activity(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        created_json = json.dumps({"createdAt": old})
        activity_json = json.dumps({"comments": [], "reviews": []})

        call_count = [0]
        def mock_run(cmd, timeout=60):
            call_count[0] += 1
            if "--json" in cmd and "createdAt" in cmd:
                return (0, created_json)
            if "--json" in cmd and "comments" in cmd:
                return (0, activity_json)
            return (0, "")

        with patch("bot.supervisor.ci_gate._run", side_effect=mock_run):
            assert _quiet_period_ok(42, "live", live_merge_quiet_seconds=86400) is True

    def test_live_mode_human_activity_blocks(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        created_json = json.dumps({"createdAt": old})
        activity_json = json.dumps({
            "comments": [{"author": {"login": "human-user"}}],
            "reviews": [],
        })

        def mock_run(cmd, timeout=60):
            if "createdAt" in cmd:
                return (0, created_json)
            return (0, activity_json)

        with patch("bot.supervisor.ci_gate._run", side_effect=mock_run):
            assert _quiet_period_ok(42, "live") is False


class TestSharpeComparison:
    def test_combo_sharpe_improved(self, db):
        before_ts = "2026-05-01T10:00:00+00:00"
        after_ts = "2026-05-01T12:00:00+00:00"
        pr_ts = "2026-05-01T11:00:00+00:00"

        it1 = create_iteration(db_path=db)
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE iterations SET timestamp=? WHERE id=?", (before_ts, it1))
        conn.commit()
        conn.close()
        insert_autoresearch_run(it1, "EURUSD", "M15", "mean_reversion",
                                sharpe=1.0, dsr=0.8, guard="PASS", db_path=db)

        it2 = create_iteration(db_path=db)
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE iterations SET timestamp=? WHERE id=?", (after_ts, it2))
        conn.commit()
        conn.close()
        insert_autoresearch_run(it2, "EURUSD", "M15", "mean_reversion",
                                sharpe=1.5, dsr=1.1, guard="PASS", db_path=db)

        assert _sharpe_improved_for_combo(
            "EURUSD_M15_mean_reversion", pr_ts, db_path=db
        ) is True

    def test_combo_sharpe_degraded(self, db):
        before_ts = "2026-05-01T10:00:00+00:00"
        after_ts = "2026-05-01T12:00:00+00:00"
        pr_ts = "2026-05-01T11:00:00+00:00"

        it1 = create_iteration(db_path=db)
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE iterations SET timestamp=? WHERE id=?", (before_ts, it1))
        conn.commit(); conn.close()
        insert_autoresearch_run(it1, "EURUSD", "M15", "mean_reversion",
                                sharpe=1.8, db_path=db)

        it2 = create_iteration(db_path=db)
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE iterations SET timestamp=? WHERE id=?", (after_ts, it2))
        conn.commit(); conn.close()
        insert_autoresearch_run(it2, "EURUSD", "M15", "mean_reversion",
                                sharpe=1.2, db_path=db)

        assert _sharpe_improved_for_combo(
            "EURUSD_M15_mean_reversion", pr_ts, db_path=db
        ) is False

    def test_global_sharpe_improved(self, db):
        before_ts = "2026-05-01T10:00:00+00:00"
        after_ts = "2026-05-01T12:00:00+00:00"
        pr_ts = "2026-05-01T11:00:00+00:00"

        it1 = create_iteration(db_path=db)
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE iterations SET timestamp=? WHERE id=?", (before_ts, it1))
        conn.commit(); conn.close()
        for sym in ["EURUSD", "GBPUSD"]:
            insert_autoresearch_run(it1, sym, "M15", "mean_reversion",
                                    sharpe=1.0, db_path=db)

        it2 = create_iteration(db_path=db)
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE iterations SET timestamp=? WHERE id=?", (after_ts, it2))
        conn.commit(); conn.close()
        for sym in ["EURUSD", "GBPUSD"]:
            insert_autoresearch_run(it2, sym, "M15", "mean_reversion",
                                    sharpe=1.4, db_path=db)

        assert _global_sharpe_improved(pr_ts, db_path=db) is True


class TestProcessPatchingEvents:
    def test_merge_on_green_checks_and_sharpe_improvement(self, db, tmp_path):
        before_ts = "2026-05-01T10:00:00+00:00"
        after_ts = "2026-05-01T12:00:00+00:00"
        pr_ts = "2026-05-01T11:00:00+00:00"

        it1 = create_iteration(db_path=db)
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE iterations SET timestamp=? WHERE id=?", (before_ts, it1))
        conn.commit(); conn.close()
        insert_autoresearch_run(it1, "EURUSD", "M15", "mean_reversion", sharpe=1.0, db_path=db)

        it2 = create_iteration(db_path=db)
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE iterations SET timestamp=? WHERE id=?", (after_ts, it2))
        conn.commit(); conn.close()
        insert_autoresearch_run(it2, "EURUSD", "M15", "mean_reversion", sharpe=1.5, db_path=db)

        checks_json = json.dumps([{"name": "test", "state": "SUCCESS"}])
        created_json = json.dumps({"createdAt": pr_ts})
        conflict_json = json.dumps({"mergeable": "MERGEABLE"})

        def mock_run(cmd, timeout=60):
            cmd_str = " ".join(cmd)
            if "pr checks" in cmd_str:
                return (0, checks_json)
            if "createdAt" in cmd_str:
                return (0, created_json)
            if "mergeable" in cmd_str:
                return (0, conflict_json)
            if "pr merge" in cmd_str:
                return (0, "PR merged")
            return (0, "")

        event_row = _make_mock_row(1, 99, scope="combo",
                                    combo_slug="EURUSD_M15_mean_reversion")
        with patch("bot.supervisor.ci_gate._run", side_effect=mock_run):
            updates = process_patching_events(
                [event_row], bot_mode="paper", db_path=db
            )
        assert updates.get(1) == "merged"
