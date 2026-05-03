"""CI gate for auto-merging patch PRs.

Per iteration, for each regression_event in status=patching with a pr_number:
  1. Check all gh pr checks are successful.
  2. Apply mode-aware quiet-period gate (paper: none; live: 24h + zero human activity).
  3. Compare scope-aware Sharpe (combo or global mean) before vs after PR creation.
  4. If improved: gh pr merge --squash.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_BOT_ROOT = Path(__file__).resolve().parents[1]
_GH_CHECKS_TIMEOUT = 60
_GH_PR_TIMEOUT = 30


def _run(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    result = subprocess.run(
        cmd, cwd=str(_BOT_ROOT), capture_output=True, text=True, timeout=timeout
    )
    return result.returncode, (result.stdout + result.stderr).strip()


def _all_checks_pass(pr_number: int) -> bool:
    rc, out = _run(
        ["gh", "pr", "checks", str(pr_number), "--json", "name,state"],
        timeout=_GH_CHECKS_TIMEOUT,
    )
    if rc != 0:
        log.warning("gh pr checks failed for PR #%d: %s", pr_number, out)
        return False
    try:
        checks = json.loads(out)
    except json.JSONDecodeError:
        return False
    if not checks:
        # No checks configured — not mergeable; avoid blind auto-merge
        log.warning("PR #%d has no CI checks configured", pr_number)
        return False
    return all(c.get("state") == "SUCCESS" for c in checks)


def _has_merge_conflict(pr_number: int) -> bool:
    rc, out = _run(
        ["gh", "pr", "view", str(pr_number), "--json", "mergeable"],
        timeout=_GH_PR_TIMEOUT,
    )
    if rc != 0:
        return False
    try:
        data = json.loads(out)
        return data.get("mergeable") == "CONFLICTING"
    except json.JSONDecodeError:
        return False


def _pr_created_at(pr_number: int) -> Optional[datetime]:
    rc, out = _run(
        ["gh", "pr", "view", str(pr_number), "--json", "createdAt"],
        timeout=_GH_PR_TIMEOUT,
    )
    if rc != 0:
        return None
    try:
        ts = json.loads(out).get("createdAt")
        return datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None
    except Exception:
        return None


def _has_human_activity(pr_number: int) -> bool:
    """Return True if any non-bot comments or reviews exist."""
    rc, out = _run(
        ["gh", "pr", "view", str(pr_number), "--json", "comments,reviews"],
        timeout=_GH_PR_TIMEOUT,
    )
    if rc != 0:
        return False
    try:
        data = json.loads(out)
        for comment in data.get("comments", []):
            author = comment.get("author", {}).get("login", "")
            if not author.endswith("[bot]") and author != "github-actions":
                return True
        for review in data.get("reviews", []):
            author = review.get("author", {}).get("login", "")
            if not author.endswith("[bot]") and author != "github-actions":
                return True
    except Exception:
        pass
    return False


def _quiet_period_ok(
    pr_number: int,
    bot_mode: str,
    live_merge_quiet_seconds: int = 86400,
) -> bool:
    """Return True if the mode-aware quiet period has elapsed."""
    if bot_mode != "live":
        return True

    created_at = _pr_created_at(pr_number)
    if created_at is None:
        return False

    elapsed = (datetime.now(timezone.utc) - created_at).total_seconds()
    if elapsed < live_merge_quiet_seconds:
        log.info(
            "PR #%d quiet period not elapsed (%.0f / %d s)",
            pr_number, elapsed, live_merge_quiet_seconds,
        )
        return False

    if _has_human_activity(pr_number):
        log.info("PR #%d has human activity — skipping auto-merge", pr_number)
        return False

    return True


def _sharpe_improved_for_combo(
    combo_slug: str,
    pr_created_at_str: str,
    db_path: Path,
) -> bool:
    """Compare combo Sharpe in the iteration immediately before vs after PR creation."""
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        parts = combo_slug.split("_", 2)
        if len(parts) != 3:
            return False
        symbol, timeframe, strategy = parts

        before = conn.execute(
            """SELECT r.sharpe FROM autoresearch_runs r
               JOIN iterations i ON r.iteration_id = i.id
               WHERE r.symbol=? AND r.timeframe=? AND r.strategy=?
                 AND i.timestamp < ?
               ORDER BY i.id DESC LIMIT 1""",
            (symbol, timeframe, strategy, pr_created_at_str),
        ).fetchone()

        after = conn.execute(
            """SELECT r.sharpe FROM autoresearch_runs r
               JOIN iterations i ON r.iteration_id = i.id
               WHERE r.symbol=? AND r.timeframe=? AND r.strategy=?
                 AND i.timestamp > ?
               ORDER BY i.id ASC LIMIT 1""",
            (symbol, timeframe, strategy, pr_created_at_str),
        ).fetchone()

        if before is None or after is None:
            log.info("insufficient data for Sharpe comparison on %s", combo_slug)
            return False

        improved = (after["sharpe"] or 0) > (before["sharpe"] or 0)
        log.info(
            "Sharpe comparison %s: before=%.3f after=%.3f improved=%s",
            combo_slug, before["sharpe"] or 0, after["sharpe"] or 0, improved,
        )
        return improved
    finally:
        conn.close()


def _global_sharpe_improved(pr_created_at_str: str, db_path: Path) -> bool:
    """Compare mean Sharpe across all 8 combos before vs after PR creation."""
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        before = conn.execute(
            """SELECT AVG(r.sharpe) as mean_sharpe FROM autoresearch_runs r
               JOIN iterations i ON r.iteration_id = i.id
               WHERE i.timestamp < ?
               ORDER BY i.id DESC LIMIT 8""",
            (pr_created_at_str,),
        ).fetchone()

        after = conn.execute(
            """SELECT AVG(r.sharpe) as mean_sharpe FROM autoresearch_runs r
               JOIN iterations i ON r.iteration_id = i.id
               WHERE i.timestamp > ?
               ORDER BY i.id ASC LIMIT 8""",
            (pr_created_at_str,),
        ).fetchone()

        if before is None or after is None:
            return False

        b = before["mean_sharpe"] or 0
        a = after["mean_sharpe"] or 0
        improved = a > b
        log.info("global Sharpe comparison: before=%.3f after=%.3f improved=%s", b, a, improved)
        return improved
    finally:
        conn.close()


def _gh_pr_merge(pr_number: int) -> bool:
    rc, out = _run(
        ["gh", "pr", "merge", str(pr_number), "--squash", "--auto"],
        timeout=60,
    )
    if rc != 0:
        log.error("gh pr merge failed for #%d: %s", pr_number, out)
        return False
    log.info("PR #%d merged", pr_number)
    return True


def _gh_pr_close(pr_number: int) -> None:
    _run(["gh", "pr", "close", str(pr_number)], timeout=30)


def process_patching_events(
    patching_events: list[sqlite3.Row],
    *,
    bot_mode: str = "paper",
    live_merge_quiet_seconds: int = 86400,
    db_path: Path,
) -> dict[int, str]:
    """Process each patching regression_event. Returns {event_id: new_status}."""
    updates: dict[int, str] = {}

    for event in patching_events:
        event_id = event["id"]
        pr_number = event["pr_number"]
        scope = event["scope"]
        combo_slug = event["combo_slug"]

        log.info("ci_gate: checking PR #%d (event_id=%d)", pr_number, event_id)

        if _has_merge_conflict(pr_number):
            log.warning("PR #%d has merge conflict — closing", pr_number)
            _gh_pr_close(pr_number)
            updates[event_id] = "patch_failed"
            continue

        if not _all_checks_pass(pr_number):
            log.info("PR #%d: checks not all green yet", pr_number)
            continue

        if not _quiet_period_ok(pr_number, bot_mode, live_merge_quiet_seconds):
            continue

        # Get PR creation timestamp for Sharpe comparison
        created_at = _pr_created_at(pr_number)
        if created_at is None:
            continue
        created_at_str = created_at.isoformat()

        if scope == "combo" and combo_slug:
            improved = _sharpe_improved_for_combo(combo_slug, created_at_str, db_path)
        else:
            improved = _global_sharpe_improved(created_at_str, db_path)

        if not improved:
            log.info("PR #%d: Sharpe not improved — skipping merge", pr_number)
            continue

        if _gh_pr_merge(pr_number):
            updates[event_id] = "merged"
        else:
            updates[event_id] = "patch_failed"

    return updates
