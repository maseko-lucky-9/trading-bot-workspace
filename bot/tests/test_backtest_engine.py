"""Smoke tests for the backtest CLI (US-007)."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_BOT_ROOT = Path(__file__).resolve().parents[1]
_ENGINE = _BOT_ROOT / "backtest" / "engine.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_ENGINE), *args],
        cwd=str(_BOT_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_metric_sharpe_prints_sharpe_line():
    proc = _run("--metric", "sharpe", "--bars", "300")
    assert proc.returncode == 0
    assert re.search(r"^SHARPE\s+-?[0-9.]+", proc.stdout, flags=re.MULTILINE)


def test_guard_prints_guard_line():
    proc = _run("--guard", "--bars", "300")
    assert re.search(r"^GUARD\s+(PASS|FAIL)", proc.stdout, flags=re.MULTILINE)
    assert proc.returncode in (0, 1)
