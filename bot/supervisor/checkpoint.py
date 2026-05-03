"""SQLite state backend for the supervisor loop.

Five tables (WAL mode):
  iterations          — one row per supervisor cycle
  autoresearch_runs   — one row per combo per iteration
  regression_events   — lifecycle tracking for detected regressions
  daily_budget        — daily Claude API patch-attempt counter
  escalation_log      — cooldown tracking per escalation cause
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path(__file__).resolve().parent / "supervisor.db"

_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS iterations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp           TEXT    NOT NULL,
    phase               TEXT    NOT NULL DEFAULT '',
    last_combo_index    INTEGER,
    status              TEXT    NOT NULL DEFAULT 'running',
    combos_promoted     INTEGER NOT NULL DEFAULT 0,
    regressions_detected INTEGER NOT NULL DEFAULT 0,
    escalated           INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS autoresearch_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    iteration_id    INTEGER NOT NULL REFERENCES iterations(id),
    symbol          TEXT    NOT NULL,
    timeframe       TEXT    NOT NULL,
    strategy        TEXT    NOT NULL,
    sharpe          REAL,
    dsr             REAL,
    guard           TEXT,
    max_dd          REAL,
    win_rate        REAL,
    promoted        INTEGER NOT NULL DEFAULT 0,
    snapshot_path   TEXT,
    timestamp       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS regression_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp           TEXT    NOT NULL,
    regression_type     TEXT    NOT NULL,
    scope               TEXT    NOT NULL DEFAULT 'combo',
    combo_slug          TEXT,
    description         TEXT    NOT NULL DEFAULT '',
    evidence_json       TEXT    NOT NULL DEFAULT '{}',
    github_issue_number INTEGER,
    pr_number           INTEGER,
    status              TEXT    NOT NULL DEFAULT 'detected'
);

CREATE TABLE IF NOT EXISTS daily_budget (
    utc_date                TEXT PRIMARY KEY,
    patch_attempts_count    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS escalation_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    cause       TEXT    NOT NULL,
    payload_json TEXT   NOT NULL DEFAULT '{}'
);
"""


def _connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def bootstrap(db_path: Path = DB_PATH) -> None:
    """Create database and apply schema (idempotent)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(db_path)
    try:
        conn.executescript(_DDL)
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# iterations
# ---------------------------------------------------------------------------

def create_iteration(db_path: Path = DB_PATH) -> int:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO iterations (timestamp, status) VALUES (?, 'running')",
            (_now(),),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_iteration(
    iteration_id: int,
    *,
    phase: str = "",
    last_combo_index: Optional[int] = None,
    status: str = "running",
    combos_promoted: Optional[int] = None,
    regressions_detected: Optional[int] = None,
    escalated: Optional[int] = None,
    db_path: Path = DB_PATH,
) -> None:
    sets: list[str] = ["phase = ?", "status = ?"]
    vals: list[Any] = [phase, status]
    if last_combo_index is not None:
        sets.append("last_combo_index = ?")
        vals.append(last_combo_index)
    if combos_promoted is not None:
        sets.append("combos_promoted = ?")
        vals.append(combos_promoted)
    if regressions_detected is not None:
        sets.append("regressions_detected = ?")
        vals.append(regressions_detected)
    if escalated is not None:
        sets.append("escalated = ?")
        vals.append(escalated)
    vals.append(iteration_id)
    conn = _connect(db_path)
    try:
        conn.execute(f"UPDATE iterations SET {', '.join(sets)} WHERE id = ?", vals)
        conn.commit()
    finally:
        conn.close()


def get_latest_iteration(db_path: Path = DB_PATH) -> Optional[sqlite3.Row]:
    conn = _connect(db_path)
    try:
        return conn.execute(
            "SELECT * FROM iterations ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# autoresearch_runs
# ---------------------------------------------------------------------------

def insert_autoresearch_run(
    iteration_id: int,
    symbol: str,
    timeframe: str,
    strategy: str,
    *,
    sharpe: Optional[float] = None,
    dsr: Optional[float] = None,
    guard: Optional[str] = None,
    max_dd: Optional[float] = None,
    win_rate: Optional[float] = None,
    promoted: int = 0,
    snapshot_path: Optional[str] = None,
    db_path: Path = DB_PATH,
) -> int:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """INSERT INTO autoresearch_runs
               (iteration_id, symbol, timeframe, strategy, sharpe, dsr, guard,
                max_dd, win_rate, promoted, snapshot_path, timestamp)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                iteration_id, symbol, timeframe, strategy,
                sharpe, dsr, guard, max_dd, win_rate,
                promoted, snapshot_path, _now(),
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_autoresearch_run(
    run_id: int,
    *,
    promoted: Optional[int] = None,
    snapshot_path: Optional[str] = None,
    db_path: Path = DB_PATH,
) -> None:
    sets: list[str] = []
    vals: list[Any] = []
    if promoted is not None:
        sets.append("promoted = ?")
        vals.append(promoted)
    if snapshot_path is not None:
        sets.append("snapshot_path = ?")
        vals.append(snapshot_path)
    if not sets:
        return
    vals.append(run_id)
    conn = _connect(db_path)
    try:
        conn.execute(f"UPDATE autoresearch_runs SET {', '.join(sets)} WHERE id = ?", vals)
        conn.commit()
    finally:
        conn.close()


def get_recent_runs_for_combo(
    symbol: str,
    timeframe: str,
    strategy: str,
    n: int = 5,
    db_path: Path = DB_PATH,
) -> list[sqlite3.Row]:
    conn = _connect(db_path)
    try:
        return conn.execute(
            """SELECT * FROM autoresearch_runs
               WHERE symbol=? AND timeframe=? AND strategy=?
               ORDER BY id DESC LIMIT ?""",
            (symbol, timeframe, strategy, n),
        ).fetchall()
    finally:
        conn.close()


def get_last_promoted_run(
    symbol: str,
    timeframe: str,
    strategy: str,
    db_path: Path = DB_PATH,
) -> Optional[sqlite3.Row]:
    conn = _connect(db_path)
    try:
        return conn.execute(
            """SELECT * FROM autoresearch_runs
               WHERE symbol=? AND timeframe=? AND strategy=? AND promoted=1
               ORDER BY id DESC LIMIT 1""",
            (symbol, timeframe, strategy),
        ).fetchone()
    finally:
        conn.close()


def get_mean_sharpe_before(timestamp: str, db_path: Path = DB_PATH) -> Optional[float]:
    """Mean Sharpe across all combos from the iteration immediately before *timestamp*."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            """SELECT AVG(r.sharpe) as mean_sharpe
               FROM autoresearch_runs r
               JOIN iterations i ON r.iteration_id = i.id
               WHERE i.timestamp < ?
               ORDER BY i.id DESC
               LIMIT 8""",
            (timestamp,),
        ).fetchone()
        return row["mean_sharpe"] if row else None
    finally:
        conn.close()


def get_mean_sharpe_after(timestamp: str, db_path: Path = DB_PATH) -> Optional[float]:
    """Mean Sharpe across all combos from the iteration immediately after *timestamp*."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            """SELECT AVG(r.sharpe) as mean_sharpe
               FROM autoresearch_runs r
               JOIN iterations i ON r.iteration_id = i.id
               WHERE i.timestamp > ?
               ORDER BY i.id ASC
               LIMIT 8""",
            (timestamp,),
        ).fetchone()
        return row["mean_sharpe"] if row else None
    finally:
        conn.close()


def count_consecutive_no_promotions(db_path: Path = DB_PATH) -> int:
    """Count trailing iterations with combos_promoted == 0."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT combos_promoted FROM iterations ORDER BY id DESC LIMIT 30"
        ).fetchall()
        count = 0
        for row in rows:
            if row["combos_promoted"] == 0:
                count += 1
            else:
                break
        return count
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# regression_events
# ---------------------------------------------------------------------------

def insert_regression_event(
    regression_type: str,
    description: str,
    evidence: dict,
    *,
    scope: str = "combo",
    combo_slug: Optional[str] = None,
    db_path: Path = DB_PATH,
) -> int:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """INSERT INTO regression_events
               (timestamp, regression_type, scope, combo_slug, description, evidence_json, status)
               VALUES (?,?,?,?,?,?,'detected')""",
            (_now(), regression_type, scope, combo_slug,
             description, json.dumps(evidence)),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_regression_event(
    event_id: int,
    *,
    status: Optional[str] = None,
    github_issue_number: Optional[int] = None,
    pr_number: Optional[int] = None,
    db_path: Path = DB_PATH,
) -> None:
    sets: list[str] = []
    vals: list[Any] = []
    if status is not None:
        sets.append("status = ?")
        vals.append(status)
    if github_issue_number is not None:
        sets.append("github_issue_number = ?")
        vals.append(github_issue_number)
    if pr_number is not None:
        sets.append("pr_number = ?")
        vals.append(pr_number)
    if not sets:
        return
    vals.append(event_id)
    conn = _connect(db_path)
    try:
        conn.execute(f"UPDATE regression_events SET {', '.join(sets)} WHERE id = ?", vals)
        conn.commit()
    finally:
        conn.close()


def get_open_regression_events(db_path: Path = DB_PATH) -> list[sqlite3.Row]:
    conn = _connect(db_path)
    try:
        return conn.execute(
            "SELECT * FROM regression_events WHERE status NOT IN ('merged','closed')"
        ).fetchall()
    finally:
        conn.close()


def get_patching_regression_events(db_path: Path = DB_PATH) -> list[sqlite3.Row]:
    conn = _connect(db_path)
    try:
        return conn.execute(
            "SELECT * FROM regression_events WHERE status='patching' AND pr_number IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()


def regression_already_detected(regression_type: str, combo_slug: Optional[str],
                                 db_path: Path = DB_PATH) -> bool:
    conn = _connect(db_path)
    try:
        if combo_slug:
            row = conn.execute(
                """SELECT id FROM regression_events
                   WHERE regression_type=? AND combo_slug=?
                   AND status NOT IN ('merged','closed') LIMIT 1""",
                (regression_type, combo_slug),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT id FROM regression_events
                   WHERE regression_type=? AND combo_slug IS NULL
                   AND status NOT IN ('merged','closed') LIMIT 1""",
                (regression_type,),
            ).fetchone()
        return row is not None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# daily_budget
# ---------------------------------------------------------------------------

def increment_patch_attempts(db_path: Path = DB_PATH) -> int:
    """Atomically increment today's patch count. Returns new count."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = _connect(db_path)
    try:
        conn.execute(
            """INSERT INTO daily_budget (utc_date, patch_attempts_count) VALUES (?,1)
               ON CONFLICT(utc_date) DO UPDATE SET patch_attempts_count=patch_attempts_count+1""",
            (today,),
        )
        conn.commit()
        row = conn.execute(
            "SELECT patch_attempts_count FROM daily_budget WHERE utc_date=?", (today,)
        ).fetchone()
        return row["patch_attempts_count"]
    finally:
        conn.close()


def get_patch_attempts_today(db_path: Path = DB_PATH) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT patch_attempts_count FROM daily_budget WHERE utc_date=?", (today,)
        ).fetchone()
        return row["patch_attempts_count"] if row else 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# escalation_log
# ---------------------------------------------------------------------------

def insert_escalation(cause: str, payload: dict, db_path: Path = DB_PATH) -> int:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO escalation_log (timestamp, cause, payload_json) VALUES (?,?,?)",
            (_now(), cause, json.dumps(payload)),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_last_escalation(cause: str, db_path: Path = DB_PATH) -> Optional[sqlite3.Row]:
    conn = _connect(db_path)
    try:
        return conn.execute(
            "SELECT * FROM escalation_log WHERE cause=? ORDER BY id DESC LIMIT 1",
            (cause,),
        ).fetchone()
    finally:
        conn.close()
