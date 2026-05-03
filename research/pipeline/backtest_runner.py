"""Subprocess wrapper around ``bot/backtest/engine.py``.

Runs one backtest per spec YAML, parses stdout via the regexes already used by
``bot/autoresearch/loop.py``, and appends the raw Sharpe to a global
trial log so the coordinator can compute multiple-testing-corrected DSR
across **all** strategies in the pipeline run.
"""
from __future__ import annotations

import asyncio
import csv
import fcntl
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .models import (
    REPO_ROOT,
    TRIAL_LOG_PATH,
    BacktestResult,
    MappedStrategy,
)


# Reuse the regex contracts from bot/autoresearch/loop.py
_SHARPE_RE = re.compile(r"^SHARPE\s+(-?[0-9.]+)", re.MULTILINE)
_GUARD_RE = re.compile(r"^GUARD\s+(PASS|FAIL)", re.MULTILINE)
_WR_RE = re.compile(r"win_rate=([0-9.]+)%")
_DD_RE = re.compile(r"drawdown=([0-9.]+)%")
_BARS_RE = re.compile(r"bars=([0-9]+)")
_TRADES_RE = re.compile(r"trades=([0-9]+)")


_TRIAL_LOG_HEADER = (
    "sr_id", "book_slug", "strategy_name", "mapped_type",
    "sharpe", "max_dd", "win_rate", "guard", "trades", "timestamp",
)


def _ensure_trial_log_header() -> None:
    """Create trial log with header if missing. Atomic; safe to call concurrently."""
    if TRIAL_LOG_PATH.exists():
        return
    TRIAL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = TRIAL_LOG_PATH.with_suffix(".tsv.tmp")
    with tmp.open("w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(_TRIAL_LOG_HEADER)
    try:
        tmp.replace(TRIAL_LOG_PATH)
    except FileExistsError:
        # Lost the race — another process created it first; that's fine.
        tmp.unlink(missing_ok=True)


def append_trial_log(result: BacktestResult) -> None:
    """Append one row to ``research/trial_log.tsv`` with file-level locking."""
    _ensure_trial_log_header()
    row = (
        result.sr_id,
        result.book_slug,
        result.strategy_name,
        result.mapped_type,
        f"{result.sharpe:.6f}",
        f"{result.max_drawdown_pct:.4f}",
        f"{result.win_rate_pct:.4f}",
        "PASS" if result.guard_pass else "FAIL",
        result.trades,
        datetime.now(timezone.utc).isoformat(),
    )
    with TRIAL_LOG_PATH.open("a", newline="") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            csv.writer(fh, delimiter="\t").writerow(row)
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


# --------------------------------------------------------------------------- #
# Stdout parsing                                                              #
# --------------------------------------------------------------------------- #

def _parse_engine_output(stdout: str) -> dict[str, float | int | bool | None]:
    """Pull the numbers out of the backtest engine's stdout contract."""
    sharpe_m = _SHARPE_RE.search(stdout)
    guard_m = _GUARD_RE.search(stdout)
    wr_m = _WR_RE.search(stdout)
    dd_m = _DD_RE.search(stdout)
    bars_m = _BARS_RE.search(stdout)
    trades_m = _TRADES_RE.search(stdout)
    return {
        "sharpe": float(sharpe_m.group(1)) if sharpe_m else None,
        "guard_pass": guard_m and guard_m.group(1) == "PASS",
        "win_rate": float(wr_m.group(1)) if wr_m else 0.0,
        "max_dd": float(dd_m.group(1)) if dd_m else 0.0,
        "bars": int(bars_m.group(1)) if bars_m else 0,
        "trades": int(trades_m.group(1)) if trades_m else 0,
    }


# --------------------------------------------------------------------------- #
# Backtest invocation                                                         #
# --------------------------------------------------------------------------- #

DEFAULT_BARS = 125_000
DEFAULT_TIMEFRAME = "M15"
DEFAULT_SYMBOL = "EURUSD"
DEFAULT_CV = "kfold:5"
DEFAULT_EMBARGO = 96


def _build_command(
    spec_path: Path,
    *,
    symbol: str,
    timeframe: str,
    bars: int,
    cv: str,
    embargo: int,
    guard: bool,
) -> list[str]:
    cmd = [
        sys.executable,
        str(REPO_ROOT / "bot" / "backtest" / "engine.py"),
        "--params", str(spec_path.resolve()),
        "--symbol", symbol,
        "--timeframe", timeframe,
        "--bars", str(bars),
        "--metric", "sharpe",
    ]
    if cv:
        cmd += ["--cv", cv, "--embargo", str(embargo)]
    if guard:
        cmd.append("--guard")
    return cmd


def run_backtest(
    mapped: MappedStrategy,
    book_slug: str,
    *,
    symbol: str = DEFAULT_SYMBOL,
    timeframe: str = DEFAULT_TIMEFRAME,
    bars: int = DEFAULT_BARS,
    cv: str = DEFAULT_CV,
    embargo: int = DEFAULT_EMBARGO,
    guard: bool = True,
    timeout_seconds: int = 600,
) -> BacktestResult:
    """Run the backtest synchronously and append a trial-log row.

    Always returns a ``BacktestResult``; on failure ``error`` is set.
    """
    if mapped.spec_path is None or not mapped.spec_path.exists():
        return BacktestResult(
            sr_id=mapped.sr_id,
            strategy_name=mapped.candidate.name,
            book_slug=book_slug,
            mapped_type=mapped.mapped_type,
            sharpe=0.0, max_drawdown_pct=0.0, win_rate_pct=0.0,
            guard_pass=False, trades=0, bars=0,
            error=f"missing_spec: {mapped.spec_path}",
        )

    cmd = _build_command(
        mapped.spec_path,
        symbol=symbol, timeframe=timeframe, bars=bars,
        cv=cv, embargo=embargo, guard=guard,
    )
    try:
        proc = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return BacktestResult(
            sr_id=mapped.sr_id,
            strategy_name=mapped.candidate.name,
            book_slug=book_slug,
            mapped_type=mapped.mapped_type,
            sharpe=0.0, max_drawdown_pct=0.0, win_rate_pct=0.0,
            guard_pass=False, trades=0, bars=0,
            error=f"timeout_after_{timeout_seconds}s",
        )

    parsed = _parse_engine_output(proc.stdout)
    if parsed["sharpe"] is None:
        snippet = proc.stderr[-300:] or proc.stdout[-300:]
        return BacktestResult(
            sr_id=mapped.sr_id,
            strategy_name=mapped.candidate.name,
            book_slug=book_slug,
            mapped_type=mapped.mapped_type,
            sharpe=0.0, max_drawdown_pct=0.0, win_rate_pct=0.0,
            guard_pass=False, trades=0, bars=0,
            error=f"unparsable_output (rc={proc.returncode}): {snippet!r}",
        )

    result = BacktestResult(
        sr_id=mapped.sr_id,
        strategy_name=mapped.candidate.name,
        book_slug=book_slug,
        mapped_type=mapped.mapped_type,
        sharpe=float(parsed["sharpe"]),
        max_drawdown_pct=float(parsed["max_dd"]),
        win_rate_pct=float(parsed["win_rate"]),
        guard_pass=bool(parsed["guard_pass"]),
        trades=int(parsed["trades"]),
        bars=int(parsed["bars"]),
        error=None,
    )
    append_trial_log(result)
    return result


async def run_backtest_async(
    mapped: MappedStrategy,
    book_slug: str,
    **kwargs,
) -> BacktestResult:
    """Async wrapper that runs the subprocess in a thread."""
    return await asyncio.to_thread(run_backtest, mapped, book_slug, **kwargs)


__all__ = [
    "run_backtest", "run_backtest_async", "append_trial_log",
    "_parse_engine_output",
]
