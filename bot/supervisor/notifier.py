"""ntfy notification dispatcher for the supervisor loop.

Posts to the configured ntfy topic with Priority: high.
Respects per-cause cooldown from escalation_log before firing.
Falls back to log-only on persistent HTTP failure — never crashes the loop.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from .checkpoint import (
    DB_PATH,
    get_last_escalation,
    insert_escalation,
)

log = logging.getLogger(__name__)

_NTFY_BASE = "https://ntfy.sh"
_RETRY_COUNT = 3
_RETRY_DELAY_S = 5


def _cooldown_ok(
    cause: str,
    cooldown_seconds: int,
    db_path: Path = DB_PATH,
) -> bool:
    """Return True if enough time has passed since the last escalation for this cause."""
    last = get_last_escalation(cause, db_path=db_path)
    if last is None:
        return True
    last_ts = datetime.fromisoformat(last["timestamp"])
    elapsed = (datetime.now(timezone.utc) - last_ts).total_seconds()
    return elapsed >= cooldown_seconds


def _post_ntfy(topic: str, title: str, body: str) -> bool:
    """POST to ntfy with up to _RETRY_COUNT retries. Returns True on success."""
    url = f"{_NTFY_BASE}/{topic}"
    for attempt in range(_RETRY_COUNT):
        try:
            resp = httpx.post(
                url,
                content=body.encode(),
                headers={
                    "Title": title,
                    "Priority": "high",
                    "Content-Type": "text/plain",
                },
                timeout=10.0,
            )
            resp.raise_for_status()
            return True
        except Exception as exc:
            log.warning("ntfy attempt %d/%d failed: %s", attempt + 1, _RETRY_COUNT, exc)
            if attempt < _RETRY_COUNT - 1:
                import time
                time.sleep(_RETRY_DELAY_S)
    return False


def escalate(
    cause: str,
    title: str,
    body: str,
    payload: dict,
    *,
    topic: str = "prudentia-alerts",
    cooldown_seconds: int = 14400,
    db_path: Path = DB_PATH,
    dry_run: bool = False,
) -> bool:
    """Fire an escalation notification if cooldown allows.

    Records the escalation in escalation_log regardless of HTTP success
    so the cooldown window is enforced even on partial failures.

    Returns True if the notification was sent (or would be sent in dry_run).
    """
    if not _cooldown_ok(cause, cooldown_seconds, db_path=db_path):
        log.info("escalation suppressed (cooldown): cause=%s", cause)
        return False

    insert_escalation(cause, payload, db_path=db_path)

    if dry_run:
        log.info("[dry-run] would escalate cause=%s title=%r", cause, title)
        return True

    ok = _post_ntfy(topic, title, body)
    if ok:
        log.info("escalation sent: cause=%s", cause)
    else:
        log.error("escalation failed (all retries exhausted): cause=%s", cause)
    return ok


# ---------------------------------------------------------------------------
# CLI smoke-test helper
# ---------------------------------------------------------------------------

def _cli_main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="bot.supervisor.notifier")
    parser.add_argument("--escalate", action="store_true",
                        help="Send a test escalation to prudentia-alerts.")
    parser.add_argument("--topic", default="prudentia-alerts")
    parser.add_argument("--cause", default="manual")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)

    if args.escalate:
        ok = escalate(
            cause=args.cause,
            title="Supervisor test escalation",
            body="This is a smoke-test ping from bot.supervisor.notifier.",
            payload={"test": True},
            topic=args.topic,
            cooldown_seconds=0,
        )
        return 0 if ok else 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(_cli_main())
