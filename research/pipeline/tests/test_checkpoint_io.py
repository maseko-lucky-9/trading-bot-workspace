"""Tests for checkpoint persistence + SR counter."""
import json
from pathlib import Path

import pytest

from research.pipeline import models
from research.pipeline.checkpoint_io import (
    load_checkpoint,
    next_sr_id,
    save_checkpoint,
)
from research.pipeline.models import AgentCheckpoint


@pytest.fixture(autouse=True)
def _isolated_checkpoint_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "CHECKPOINT_DIR", tmp_path)
    monkeypatch.setattr(models, "SR_COUNTER_PATH", tmp_path / "_sr_counter.json")
    # patch in checkpoint_io as well (it imported the values directly)
    import research.pipeline.checkpoint_io as cio
    monkeypatch.setattr(cio, "CHECKPOINT_DIR", tmp_path)
    monkeypatch.setattr(cio, "SR_COUNTER_PATH", tmp_path / "_sr_counter.json")
    yield


def test_checkpoint_round_trip():
    cp = AgentCheckpoint(book_slug="test", agent_id=0, status="in_progress")
    cp.mark_phase_done("read")
    cp.n_chunks = 7
    save_checkpoint(cp)

    loaded = load_checkpoint("test", 0)
    assert loaded.status == "in_progress"
    assert loaded.phases_complete == ["read"]
    assert loaded.n_chunks == 7


def test_load_missing_returns_pending():
    cp = load_checkpoint("never-seen", 5)
    assert cp.status == "pending"
    assert cp.phases_complete == []


def test_corrupt_checkpoint_recovers():
    path = models.CHECKPOINT_DIR / "agent_corrupt_state.json"
    path.write_text("not json{{{")
    cp = load_checkpoint("corrupt", 1)
    assert cp.status == "pending"
    assert cp.error and "corrupt_checkpoint" in cp.error


def test_sr_counter_monotonic():
    assert next_sr_id() == 1
    assert next_sr_id() == 2
    assert next_sr_id() == 3


def test_sr_counter_persists_across_call_sites():
    # First a few IDs
    [next_sr_id() for _ in range(5)]
    # Counter file shows next=6
    raw = json.loads(models.SR_COUNTER_PATH.read_text())
    assert raw == {"next": 6}
