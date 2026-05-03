"""Tests for ADR rendering (no IO except tmp_path)."""
from pathlib import Path

import pytest

from research.pipeline import adr_writer
from research.pipeline.adr_writer import (
    DSR_PENDING,
    render_adr,
    update_dsr_in_adr,
)
from research.pipeline.models import (
    BacktestResult,
    BookSpec,
    MappedStrategy,
    StrategyCandidate,
)


def _mk_mapped():
    cand = StrategyCandidate(
        name="Test EMA", hypothesis="h",
        entry_rules=["fast crosses slow"], exit_rules=["atr stop"],
        parameters={"fast": 9, "slow": 21},
    )
    mapped = MappedStrategy(
        candidate=cand,
        mapped_type="ema_crossover",
        yaml_params={"strategy": "ema_crossover", "ema_fast": 9, "ema_slow": 21},
    )
    mapped.sr_id = 7
    mapped.spec_path = Path("/tmp/SR-007.yaml")
    return mapped


def _mk_book():
    return BookSpec("test-book", "Test Book", "Author", 2020)


def _mk_result(pass_=True, error=None):
    return BacktestResult(
        sr_id=7, strategy_name="Test EMA", book_slug="test-book",
        mapped_type="ema_crossover",
        sharpe=1.2 if not error else 0.0,
        max_drawdown_pct=3.5,
        win_rate_pct=52.0,
        guard_pass=pass_,
        trades=120,
        bars=8760,
        error=error,
    )


def test_render_pending_dsr():
    md = render_adr(_mk_mapped(), _mk_result(), _mk_book(), dsr=None)
    assert "SR-007" in md
    assert DSR_PENDING in md
    assert "PASS" in md


def test_render_with_dsr():
    md = render_adr(_mk_mapped(), _mk_result(), _mk_book(), dsr=0.42)
    assert "0.4200" in md
    assert "**moderate** confidence" in md


def test_render_failed_backtest_includes_error():
    md = render_adr(
        _mk_mapped(), _mk_result(pass_=False, error="timeout"), _mk_book(),
    )
    assert "Failed" in md
    assert "timeout" in md


def test_update_dsr_replaces_placeholder(tmp_path: Path):
    path = tmp_path / "SR-007.md"
    md = render_adr(_mk_mapped(), _mk_result(), _mk_book(), dsr=None)
    path.write_text(md)
    update_dsr_in_adr(path, 0.7531)
    new = path.read_text()
    assert DSR_PENDING not in new
    assert "0.7531" in new
    assert "**high** confidence" in new
