"""Tests for checkpoint.py — SQLite write/read cycle and WAL integrity."""
import pytest
from pathlib import Path

from bot.supervisor.checkpoint import (
    bootstrap,
    create_iteration,
    update_iteration,
    get_latest_iteration,
    insert_autoresearch_run,
    get_recent_runs_for_combo,
    insert_regression_event,
    get_open_regression_events,
    regression_already_detected,
    update_regression_event,
    get_patch_attempts_today,
    increment_patch_attempts,
    insert_escalation,
    get_last_escalation,
    count_consecutive_no_promotions,
)


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test_supervisor.db"
    bootstrap(db_path=db_path)
    return db_path


def test_bootstrap_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    bootstrap(db_path=db_path)
    bootstrap(db_path=db_path)  # second call must not raise
    assert db_path.exists()


def test_iteration_create_and_update(db):
    it_id = create_iteration(db_path=db)
    assert it_id > 0

    update_iteration(it_id, phase="phase2", status="running", last_combo_index=3, db_path=db)
    row = get_latest_iteration(db_path=db)
    assert row["id"] == it_id
    assert row["phase"] == "phase2"
    assert row["last_combo_index"] == 3
    assert row["status"] == "running"


def test_iteration_complete(db):
    it_id = create_iteration(db_path=db)
    update_iteration(it_id, phase="complete", status="complete",
                     combos_promoted=2, regressions_detected=1, escalated=0, db_path=db)
    row = get_latest_iteration(db_path=db)
    assert row["status"] == "complete"
    assert row["combos_promoted"] == 2
    assert row["regressions_detected"] == 1


def test_autoresearch_run_insert_and_query(db):
    it_id = create_iteration(db_path=db)
    insert_autoresearch_run(it_id, "EURUSD", "M15", "mean_reversion",
                            sharpe=1.5, dsr=1.1, guard="PASS", max_dd=0.03, win_rate=0.55,
                            db_path=db)
    insert_autoresearch_run(it_id, "EURUSD", "M15", "mean_reversion",
                            sharpe=1.3, dsr=0.9, guard="FAIL", db_path=db)
    rows = get_recent_runs_for_combo("EURUSD", "M15", "mean_reversion", n=5, db_path=db)
    assert len(rows) == 2
    assert rows[0]["sharpe"] == pytest.approx(1.3)  # most recent first


def test_regression_event_lifecycle(db):
    event_id = insert_regression_event(
        "circuit_breaker_false_positive",
        "Test description",
        {"peak": 11000, "bridge": 10000},
        scope="global",
        db_path=db,
    )
    assert event_id > 0

    events = get_open_regression_events(db_path=db)
    assert len(events) == 1
    assert events[0]["regression_type"] == "circuit_breaker_false_positive"

    assert regression_already_detected("circuit_breaker_false_positive", None, db_path=db)
    assert not regression_already_detected("visited_set_cycling", None, db_path=db)

    update_regression_event(event_id, status="issue_filed",
                            github_issue_number=42, db_path=db)
    events = get_open_regression_events(db_path=db)
    assert events[0]["github_issue_number"] == 42
    assert events[0]["status"] == "issue_filed"


def test_regression_event_merged_excluded_from_open(db):
    event_id = insert_regression_event(
        "visited_set_cycling", "desc", {}, scope="combo",
        combo_slug="EURUSD_M15_mean_reversion", db_path=db,
    )
    update_regression_event(event_id, status="merged", db_path=db)
    open_events = get_open_regression_events(db_path=db)
    assert len(open_events) == 0


def test_daily_budget(db):
    assert get_patch_attempts_today(db_path=db) == 0
    count = increment_patch_attempts(db_path=db)
    assert count == 1
    count = increment_patch_attempts(db_path=db)
    assert count == 2
    assert get_patch_attempts_today(db_path=db) == 2


def test_escalation_cooldown(db):
    esc_id = insert_escalation("drawdown", {"dd": 0.06}, db_path=db)
    assert esc_id > 0
    last = get_last_escalation("drawdown", db_path=db)
    assert last["cause"] == "drawdown"
    assert get_last_escalation("dsr_degrade", db_path=db) is None


def test_resume_detection(db):
    it_id = create_iteration(db_path=db)
    update_iteration(it_id, phase="phase2", last_combo_index=4,
                     status="running", db_path=db)
    row = get_latest_iteration(db_path=db)
    assert row["status"] == "running"
    assert row["last_combo_index"] == 4


def test_count_consecutive_no_promotions(db):
    for _ in range(3):
        it_id = create_iteration(db_path=db)
        update_iteration(it_id, status="complete", combos_promoted=0, db_path=db)
    it_id = create_iteration(db_path=db)
    update_iteration(it_id, status="complete", combos_promoted=1, db_path=db)
    for _ in range(2):
        it_id = create_iteration(db_path=db)
        update_iteration(it_id, status="complete", combos_promoted=0, db_path=db)
    count = count_consecutive_no_promotions(db_path=db)
    assert count == 2
