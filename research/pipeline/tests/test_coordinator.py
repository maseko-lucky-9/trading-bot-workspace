"""Tests for coordinator ranking, clustering, and DSR back-fill."""
from pathlib import Path

import pytest

from research.pipeline import coordinator as coord
from research.pipeline.coordinator import (
    TrialRow,
    cluster_by_type,
    compute_global_dsr,
    render_clusters,
    render_moc,
    render_ranking_table,
)


def _rows():
    return [
        TrialRow(1, "advances-in-financial-machine-learning", "Triple Barrier",
                 "mean_reversion", 1.50, 4.0, 52.0, "PASS", 100),
        TrialRow(2, "tsam", "Carver Trend",
                 "trend_following", 1.10, 5.0, 50.0, "PASS", 60),
        TrialRow(3, "quantitative-trading-ernest-p-chan", "Pairs Z",
                 "pairs_trading", 0.40, 3.0, 53.0, "FAIL", 40),
        TrialRow(4, "naked-forex-walter-petrs", "Naked Cross",
                 "ema_crossover", 0.85, 6.5, 49.0, "FAIL", 80),
    ]


def test_compute_dsr_returns_one_value_per_row():
    rows = _rows()
    dsr = compute_global_dsr(rows)
    assert set(dsr.keys()) == {r.sr_id for r in rows}
    # Best raw Sharpe should have highest DSR
    best = max(rows, key=lambda r: r.sharpe)
    assert dsr[best.sr_id] == max(dsr.values())


def test_ranking_orders_by_dsr_desc():
    rows = _rows()
    dsr = compute_global_dsr(rows)
    table = render_ranking_table(rows, dsr)
    # SR-001 has best Sharpe so it should be first
    first_data_line = table.splitlines()[2]
    assert "SR-001" in first_data_line


def test_clusters_split_by_type():
    clusters = cluster_by_type(_rows())
    assert len(clusters["mean_reversion"]) == 1
    assert len(clusters["trend_following"]) == 1
    assert len(clusters["pairs_trading"]) == 1
    assert len(clusters["ema_crossover"]) == 1


def test_render_moc_includes_required_sections(monkeypatch, tmp_path):
    # checkpoint scan reads from disk; isolate to tmp dir
    from research.pipeline import models
    monkeypatch.setattr(models, "CHECKPOINT_DIR", tmp_path)
    import research.pipeline.checkpoint_io as cio
    monkeypatch.setattr(cio, "CHECKPOINT_DIR", tmp_path)

    rows = _rows()
    dsr = compute_global_dsr(rows)
    moc = render_moc(rows, dsr, "_no novel proposals_")
    assert "# MOC — Strategy Research Pipeline Results" in moc
    assert "## Ranking by DSR" in moc
    assert "## Strategy Clusters" in moc
    assert "## Novel Combinations" in moc
    assert "DSR pool size: 4" in moc


def test_clusters_render_falls_back_when_empty():
    empty = {t: [] for t in ("ema_crossover", "mean_reversion",
                             "trend_following", "pairs_trading")}
    out = render_clusters(empty, {})
    assert "no clusters yet" in out
