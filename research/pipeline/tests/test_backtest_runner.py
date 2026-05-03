"""Tests for backtest_runner stdout parsing."""
from research.pipeline.backtest_runner import _parse_engine_output


def test_parse_pass():
    out = (
        "SHARPE 1.2340\n"
        "GUARD PASS drawdown=3.21% win_rate=51.4% bars=8760 trades=142\n"
    )
    p = _parse_engine_output(out)
    assert p["sharpe"] == 1.234
    assert p["guard_pass"] is True
    assert p["max_dd"] == 3.21
    assert p["win_rate"] == 51.4
    assert p["bars"] == 8760
    assert p["trades"] == 142


def test_parse_fail_no_metadata():
    out = "SHARPE 0.8120\nGUARD FAIL drawdown=6.43% exceeds threshold\n"
    p = _parse_engine_output(out)
    assert p["sharpe"] == 0.812
    assert p["guard_pass"] is False
    assert p["max_dd"] == 6.43
    assert p["win_rate"] == 0.0
    assert p["bars"] == 0


def test_parse_garbage_returns_none_sharpe():
    p = _parse_engine_output("oops the engine crashed\n")
    assert p["sharpe"] is None
