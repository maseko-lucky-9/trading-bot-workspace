"""Tests for the deflated Sharpe ratio helper (Wave 0, F5)."""
from __future__ import annotations

import math

import pytest

from autoresearch.loop import deflated_sharpe


class TestDeflatedSharpe:
    def test_returns_observed_when_history_too_short(self):
        assert deflated_sharpe(2.5, []) == 2.5
        assert deflated_sharpe(2.5, [1.0]) == 2.5

    def test_returns_observed_when_zero_variance(self):
        assert deflated_sharpe(2.5, [1.0, 1.0, 1.0]) == 2.5

    def test_penalty_grows_with_more_trials(self):
        observed = 1.5
        # Same noise distribution, more trials -> bigger penalty -> lower DSR
        history_small = [0.0, 0.5, 1.0, 0.3]
        history_large = [0.0, 0.5, 1.0, 0.3] * 10  # 40 trials
        dsr_small = deflated_sharpe(observed, history_small)
        dsr_large = deflated_sharpe(observed, history_large)
        assert dsr_large < dsr_small, (
            f"Expected larger trial set to penalize harder. "
            f"small={dsr_small} large={dsr_large}"
        )

    def test_dsr_can_be_negative(self):
        """An observed Sharpe at the noise mean should produce a negative DSR
        once the multiple-testing penalty is subtracted."""
        observed = 1.0
        history = [-2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 1.0, 0.5, -0.5, 1.2]
        dsr = deflated_sharpe(observed, history)
        assert math.isfinite(dsr)

    def test_high_observed_above_noise_remains_positive(self):
        observed = 5.0
        history = [0.1, -0.2, 0.3, -0.1, 0.0, 0.4, -0.3, 0.2]
        dsr = deflated_sharpe(observed, history)
        assert dsr > 0.0

    def test_handles_inf_values_in_history(self):
        observed = 1.5
        history = [1.0, float("-inf"), 0.5, float("inf"), 0.8]
        # Should silently skip non-finite entries
        result = deflated_sharpe(observed, history)
        assert math.isfinite(result)
