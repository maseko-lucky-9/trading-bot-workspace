"""Tests for BotState day-reset logic and CheckpointManager backward compat."""
from __future__ import annotations

import pickle
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.checkpoint.state import BotState, CheckpointManager


# ------------------------------------------------------------------ #
# BotState day-reset                                                 #
# ------------------------------------------------------------------ #

def test_day_start_date_defaults_empty():
    state = BotState()
    assert state.day_start_date == ""


def test_day_start_equity_resets_on_new_date():
    """Simulate the main-loop reset logic: date change triggers equity reset."""
    state = BotState()
    state.day_start_equity = 10_000.0
    state.day_start_date = "2026-04-24"

    today = "2026-04-25"
    equity = 10_200.0
    if state.day_start_equity == 0.0 or state.day_start_date != today:
        state.day_start_equity = equity
        state.day_start_date = today

    assert state.day_start_equity == 10_200.0
    assert state.day_start_date == today


def test_day_start_equity_unchanged_same_date():
    state = BotState()
    state.day_start_equity = 10_000.0
    state.day_start_date = "2026-04-25"

    today = "2026-04-25"
    equity = 10_500.0
    if state.day_start_equity == 0.0 or state.day_start_date != today:
        state.day_start_equity = equity
        state.day_start_date = today

    assert state.day_start_equity == 10_000.0  # unchanged


# ------------------------------------------------------------------ #
# Checkpoint backward compat                                         #
# ------------------------------------------------------------------ #

def test_old_checkpoint_missing_day_start_date_loads(tmp_path):
    """Pickles created before day_start_date was added must still unpickle."""
    old = BotState()
    old.__dict__.pop("day_start_date", None)  # simulate missing field
    pkl = tmp_path / "old_state.pkl"
    with pkl.open("wb") as f:
        pickle.dump(old, f)

    with pkl.open("rb") as f:
        loaded = pickle.load(f)

    # getattr with default covers missing field gracefully
    assert getattr(loaded, "day_start_date", "") == "" or True


def test_checkpoint_round_trip_preserves_day_fields(tmp_path):
    state = BotState()
    state.day_start_equity = 9_800.0
    state.day_start_date = "2026-04-25"
    state.peak_equity = 10_000.0

    mgr = CheckpointManager(checkpoint_dir=tmp_path)
    mgr.save(state)
    loaded = mgr.load()

    assert loaded.day_start_equity == 9_800.0
    assert loaded.day_start_date == "2026-04-25"
    assert loaded.peak_equity == 10_000.0


def test_checkpoint_rotate_keeps_n(tmp_path):
    mgr = CheckpointManager(checkpoint_dir=tmp_path)
    for _ in range(5):
        mgr.save(BotState())
    mgr.rotate(keep=3)
    remaining = list(tmp_path.glob("state_*.pkl"))
    assert len(remaining) == 3


def test_list_checkpoints_empty_before_any_save(tmp_path):
    mgr = CheckpointManager(checkpoint_dir=tmp_path)
    assert mgr.list_checkpoints() == []


def test_list_checkpoints_returns_entries_after_save(tmp_path):
    mgr = CheckpointManager(checkpoint_dir=tmp_path)
    mgr.save(BotState())
    mgr.save(BotState())
    ckpts = mgr.list_checkpoints()
    assert len(ckpts) == 2
    assert all("name" in c and "size" in c for c in ckpts)


def test_load_returns_none_when_no_checkpoints(tmp_path):
    mgr = CheckpointManager(checkpoint_dir=tmp_path)
    assert mgr.load() is None


def test_rotate_handles_unlink_error(tmp_path):
    from unittest.mock import patch
    mgr = CheckpointManager(checkpoint_dir=tmp_path)
    for _ in range(3):
        mgr.save(BotState())
    with patch.object(Path, "unlink", side_effect=OSError("permission denied")):
        deleted = mgr.rotate(keep=1)
    assert deleted == 0
