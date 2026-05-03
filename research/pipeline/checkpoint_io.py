"""Atomic, file-locked checkpoint persistence for the book-research pipeline.

Two artifacts live in ``research/checkpoints/``:

- ``agent_<slug>_state.json``  — per-agent ``AgentCheckpoint``
- ``_sr_counter.json``         — monotonic counter for SR IDs
"""
from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path

from .models import (
    CHECKPOINT_DIR,
    SR_COUNTER_PATH,
    AgentCheckpoint,
)


# --------------------------------------------------------------------------- #
# Per-agent checkpoint                                                        #
# --------------------------------------------------------------------------- #

def _checkpoint_path(book_slug: str) -> Path:
    return CHECKPOINT_DIR / f"agent_{book_slug}_state.json"


def load_checkpoint(book_slug: str, agent_id: int) -> AgentCheckpoint:
    """Load the checkpoint for ``book_slug`` or return a fresh one."""
    path = _checkpoint_path(book_slug)
    if not path.exists():
        cp = AgentCheckpoint(book_slug=book_slug, agent_id=agent_id)
        cp.touch()
        return cp
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return AgentCheckpoint.from_dict(data)
    except (json.JSONDecodeError, TypeError, KeyError) as e:
        # Corrupt checkpoint — start fresh but record the error
        cp = AgentCheckpoint(book_slug=book_slug, agent_id=agent_id)
        cp.error = f"corrupt_checkpoint: {e}"
        cp.touch()
        return cp


def save_checkpoint(cp: AgentCheckpoint) -> None:
    """Atomically persist the checkpoint via tmp-file + rename."""
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    cp.touch()
    path = _checkpoint_path(cp.book_slug)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cp.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def all_checkpoints() -> list[AgentCheckpoint]:
    """Return every checkpoint currently on disk."""
    if not CHECKPOINT_DIR.exists():
        return []
    out: list[AgentCheckpoint] = []
    for p in sorted(CHECKPOINT_DIR.glob("agent_*_state.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append(AgentCheckpoint.from_dict(data))
        except Exception:
            continue
    return out


# --------------------------------------------------------------------------- #
# SR counter (cross-process via fcntl.flock + atomic rename)                  #
# --------------------------------------------------------------------------- #

@contextmanager
def _locked(path: Path):
    """Open ``path`` for r+ and hold an exclusive flock for the duration."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps({"next": 1}), encoding="utf-8")
    fh = path.open("r+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield fh
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


def next_sr_id() -> int:
    """Atomically return the next SR id and bump the counter on disk."""
    with _locked(SR_COUNTER_PATH) as fh:
        fh.seek(0)
        try:
            data = json.loads(fh.read() or "{}")
        except json.JSONDecodeError:
            data = {}
        n = int(data.get("next", 1))
        new = n + 1
        # atomic rename of a tmp file holding the bumped value
        tmp = SR_COUNTER_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"next": new}), encoding="utf-8")
        os.replace(tmp, SR_COUNTER_PATH)
        return n


__all__ = [
    "load_checkpoint", "save_checkpoint", "all_checkpoints", "next_sr_id",
]
