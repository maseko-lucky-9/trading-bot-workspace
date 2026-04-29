"""Tests for RiskManager.preservation_factor (T04)."""
from __future__ import annotations

import pytest

from core.risk.manager import RiskManager


@pytest.fixture
def rm():
    cfg = {
        "risk": {
            "trailing_dd_warn": 0.10,
            "trailing_dd_reduce": 0.15,
            "trailing_dd_halt": 0.20,
        }
    }
    return RiskManager(cfg)


def test_preservation_no_drawdown(rm):
    # equity == peak -> full size
    assert rm.preservation_factor(peak_equity=10_000.0, current_equity=10_000.0) == 1.0


def test_preservation_below_warn_threshold(rm):
    # 5% drawdown — under the 10% warn threshold
    assert rm.preservation_factor(peak_equity=10_000.0, current_equity=9_500.0) == 1.0


def test_preservation_at_warn_tier(rm):
    # exactly 10% drawdown — warn tier
    assert rm.preservation_factor(peak_equity=10_000.0, current_equity=9_000.0) == 0.5


def test_preservation_at_reduce_tier(rm):
    # exactly 15% drawdown — reduce tier
    assert rm.preservation_factor(peak_equity=10_000.0, current_equity=8_500.0) == 0.25


def test_preservation_at_halt_tier(rm):
    # exactly 20% drawdown — halt tier
    assert rm.preservation_factor(peak_equity=10_000.0, current_equity=8_000.0) == 0.0


def test_preservation_deeper_than_halt_clamps_to_zero(rm):
    assert rm.preservation_factor(peak_equity=10_000.0, current_equity=5_000.0) == 0.0


def test_preservation_zero_peak_returns_one(rm):
    # Bot just started, peak not yet established — don't penalise.
    assert rm.preservation_factor(peak_equity=0.0, current_equity=10_000.0) == 1.0


def test_preservation_negative_peak_returns_one(rm):
    # Defensive: nonsensical input shouldn't crash.
    assert rm.preservation_factor(peak_equity=-1.0, current_equity=10_000.0) == 1.0


def test_preservation_equity_above_peak_returns_one(rm):
    # New high water mark — no drawdown.
    assert rm.preservation_factor(peak_equity=10_000.0, current_equity=11_000.0) == 1.0


def test_preservation_does_not_change_size_position_behaviour(rm):
    # Sanity: existing public API unaffected. size_position remains a separate
    # method that we deliberately do not consume preservation_factor inside.
    assert hasattr(rm, "size_position")
    assert hasattr(rm, "preservation_factor")
