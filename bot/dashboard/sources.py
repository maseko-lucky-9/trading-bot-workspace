"""Read-only data adapters that back the dashboard's JSON endpoints.

Each public function returns a JSON-serialisable ``dict`` with an
explicit ``"status"`` field (``"ok"`` | ``"unavailable"`` |
``"running"`` | ``"not_running"`` | ``"unreachable"``) so the FastAPI
routes can render gracefully when the bot is killed, the bridge is
down, or any artefact is missing — see ADR ``0020-bot-dashboard``.

Public surface:

* :func:`probe_process` — find the running ``main.py --mode paper`` PID.
* :func:`probe_bridge`  — hit ``<base_url>/ping`` with a tight timeout.
* :func:`read_trades`   — load and split ``logs/trades.csv``.
* :func:`compute_equity_series` — cumulative equity + peak + drawdown.
* :func:`compute_metrics` — Sharpe / DSR / expectancy / win rate / payoff.
* :func:`current_regime` — classify the latest M15 bars as trend/range.

These functions never raise; failures are returned as
``{"status": "unavailable", "error": "..."}``.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

_BOT_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Config helper                                                               #
# --------------------------------------------------------------------------- #


def load_config(config_path: Path | None = None) -> dict:
    """Read ``config.yaml`` once. Returns ``{}`` on any failure."""
    path = config_path or (_BOT_ROOT / "config.yaml")
    try:
        with path.open() as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# Process probe — ports scripts/daily_health_check.sh:42-48                   #
# --------------------------------------------------------------------------- #


def probe_process() -> dict[str, Any]:
    """Locate the running ``python ... main.py --mode paper`` process.

    Returns one of::

        {"status": "running",     "pid": 12345, "etime": "1-02:34:56"}
        {"status": "not_running", "pid": None,  "etime": None}
        {"status": "unavailable", "error": "..."}
    """
    try:
        # pgrep -f matches the full command line; collect candidates then
        # filter to those whose binary is python (excludes shell wrappers).
        result = subprocess.run(
            ["pgrep", "-f", r"main\.py.*--mode paper"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode not in (0, 1):
            return {
                "status": "unavailable",
                "error": f"pgrep exit={result.returncode}",
                "pid": None,
                "etime": None,
            }
        candidates = [p.strip() for p in result.stdout.splitlines() if p.strip()]
        for pid in candidates:
            try:
                comm = subprocess.run(
                    ["ps", "-p", pid, "-o", "comm="],
                    capture_output=True,
                    text=True,
                    timeout=1,
                ).stdout.strip()
            except Exception:
                continue
            if "python" in comm.lower():
                etime = ""
                try:
                    etime = subprocess.run(
                        ["ps", "-p", pid, "-o", "etime="],
                        capture_output=True,
                        text=True,
                        timeout=1,
                    ).stdout.strip()
                except Exception:
                    pass
                return {
                    "status": "running",
                    "pid": int(pid),
                    "etime": etime or None,
                }
        return {"status": "not_running", "pid": None, "etime": None}
    except FileNotFoundError as exc:
        return {"status": "unavailable", "error": f"missing binary: {exc}", "pid": None, "etime": None}
    except subprocess.TimeoutExpired:
        return {"status": "unavailable", "error": "pgrep timeout", "pid": None, "etime": None}
    except Exception as exc:  # pragma: no cover — defensive belt
        return {"status": "unavailable", "error": str(exc), "pid": None, "etime": None}


# --------------------------------------------------------------------------- #
# Bridge probe — ports scripts/detect_bridge.py:34-39                         #
# --------------------------------------------------------------------------- #


def probe_bridge(base_url: str | None = None, timeout: float = 3.0) -> dict[str, Any]:
    """Hit ``<base_url>/ping``. Tight timeout so the page renders fast.

    Returns one of::

        {"status":"ok","pong":True,"ea_connected":True,"latency_ms":12.3,"error":None}
        {"status":"unreachable","pong":None,"ea_connected":None,"latency_ms":None,"error":"..."}
    """
    if base_url is None:
        cfg = load_config()
        base_url = (cfg.get("bridge") or {}).get("base_url", "http://localhost:8080")
    url = f"{base_url.rstrip('/')}/ping"
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            payload = json.loads(resp.read())
        latency_ms = round((time.perf_counter() - started) * 1000.0, 2)
        return {
            "status": "ok",
            "pong": bool(payload.get("pong")),
            "ea_connected": bool(payload.get("ea_connected")),
            "latency_ms": latency_ms,
            "error": None,
        }
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        return {
            "status": "unreachable",
            "pong": None,
            "ea_connected": None,
            "latency_ms": None,
            "error": str(exc),
        }


# --------------------------------------------------------------------------- #
# Trades CSV                                                                  #
# --------------------------------------------------------------------------- #


_TRADE_COLS = [
    "ticket",
    "symbol",
    "type",
    "volume",
    "open_price",
    "open_time",
    "close_price",
    "close_time",
    "profit",
    "sl",
    "tp",
]


def read_trades(path: Path | None = None) -> pd.DataFrame:
    """Read ``logs/trades.csv``. Returns an empty DataFrame on failure.

    The CSV contains both *open* events (``close_time`` empty) and
    *closed* events (``close_time`` non-empty) for the same ticket.
    Callers separate the two via :func:`split_open_closed`.
    """
    path = path or (_BOT_ROOT / "logs" / "trades.csv")
    if not path.exists():
        return pd.DataFrame(columns=_TRADE_COLS)
    try:
        df = pd.read_csv(
            path,
            usecols=lambda c: c in _TRADE_COLS,
            on_bad_lines="skip",
            dtype={"ticket": "Int64", "symbol": "string", "type": "string"},
        )
    except Exception:
        return pd.DataFrame(columns=_TRADE_COLS)
    # Keep column order canonical (read_csv may reorder if usecols is callable)
    keep = [c for c in _TRADE_COLS if c in df.columns]
    return df[keep].copy()


def split_open_closed(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split into (open_positions, closed_trades) by ``close_time`` emptiness."""
    if df.empty or "close_time" not in df.columns:
        return df.iloc[0:0].copy(), df.iloc[0:0].copy()
    closed_mask = df["close_time"].notna() & (df["close_time"].astype(str).str.strip() != "")
    closed = df[closed_mask].copy()
    open_pos = df[~closed_mask].copy()
    return open_pos, closed


# --------------------------------------------------------------------------- #
# Equity series                                                               #
# --------------------------------------------------------------------------- #


def compute_equity_series(closed_df: pd.DataFrame) -> dict[str, Any]:
    """Build a per-trade equity / peak / drawdown series for charting.

    Equity is the cumulative sum of the ``profit`` column ordered by
    ``close_time``.  Drawdown is expressed as a fraction of peak when
    peak is positive and as the absolute equity gap divided by 1.0
    otherwise (matches :class:`PerformanceTracker`'s convention).
    """
    if closed_df.empty or "profit" not in closed_df.columns:
        return {
            "status": "ok",
            "timestamps": [],
            "equity": [],
            "peak": [],
            "drawdown": [],
            "current_drawdown": 0.0,
            "peak_equity": 0.0,
        }
    df = closed_df.copy()
    if "close_time" in df.columns:
        df["_ct"] = pd.to_datetime(df["close_time"], errors="coerce", utc=True)
        df = df.sort_values("_ct", kind="mergesort")
    profits = pd.to_numeric(df["profit"], errors="coerce").fillna(0.0).to_numpy()
    equity = profits.cumsum()
    peak = pd.Series(equity).cummax().to_numpy()
    base = pd.Series(peak).abs().clip(lower=1.0).to_numpy()
    drawdown = (peak - equity) / base
    drawdown = drawdown.clip(min=0.0)
    timestamps = (
        df["_ct"].dt.strftime("%Y-%m-%dT%H:%M:%SZ").fillna("").tolist()
        if "_ct" in df.columns
        else [""] * len(equity)
    )
    return {
        "status": "ok",
        "timestamps": timestamps,
        "equity": [float(x) for x in equity],
        "peak": [float(x) for x in peak],
        "drawdown": [float(x) for x in drawdown],
        "current_drawdown": float(drawdown[-1]) if len(drawdown) else 0.0,
        "peak_equity": float(peak[-1]) if len(peak) else 0.0,
    }


# --------------------------------------------------------------------------- #
# Metrics + DSR                                                               #
# --------------------------------------------------------------------------- #


def _phi(z: float) -> float:
    """Standard-normal CDF (no SciPy required)."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _compute_dsr(
    sharpe: float,
    n_trades: int,
    skew: float = 0.0,
    kurt: float = 3.0,
    sr_benchmark: float = 0.0,
) -> float:
    """Bailey & López de Prado Deflated Sharpe Ratio (closed form).

    DSR = Φ( (sharpe - sr_benchmark) * sqrt(n-1)
            / sqrt(1 - skew*sharpe + (kurt-1)/4 * sharpe**2) )

    Returns 0.0 when ``n_trades < 2`` or the variance term goes
    non-positive.
    """
    if n_trades < 2 or not math.isfinite(sharpe):
        return 0.0
    denom_sq = 1.0 - skew * sharpe + ((kurt - 1.0) / 4.0) * (sharpe ** 2)
    if denom_sq <= 0.0 or not math.isfinite(denom_sq):
        return 0.0
    z = (sharpe - sr_benchmark) * math.sqrt(n_trades - 1) / math.sqrt(denom_sq)
    if not math.isfinite(z):
        return 0.0
    return float(_phi(z))


def compute_metrics(closed_df: pd.DataFrame) -> dict[str, Any]:
    """Compute the metrics tile values via :class:`PerformanceTracker`.

    Returns ``{"status":"ok", ...}`` on success or
    ``{"status":"unavailable", "error":"..."}`` on any failure.
    """
    try:
        # Local import: avoids importing the bot's strategy stack at
        # module import time (keeps the dashboard cold-start fast even
        # when core.performance has heavy transitive deps).
        from core.performance.tracker import PerformanceTracker

        tracker = PerformanceTracker()
        if not closed_df.empty:
            for row in closed_df.to_dict(orient="records"):
                tracker.record_trade(
                    {
                        "profit": float(row.get("profit", 0.0) or 0.0),
                        "open_time": row.get("open_time"),
                        "close_time": row.get("close_time"),
                    }
                )
        summary = tracker.summary()
        sharpe = float(summary.get("sharpe", 0.0))
        n = int(summary.get("trade_count", 0))
        return {
            "status": "ok",
            "sharpe": sharpe,
            "dsr": _compute_dsr(sharpe, n),
            "expectancy": float(summary.get("expectancy", 0.0)),
            "win_rate": float(summary.get("win_rate", 0.0)),
            "payoff_ratio": float(summary.get("payoff_ratio", 0.0)),
            "profit_factor": float(summary.get("profit_factor", 0.0)),
            "max_drawdown": float(summary.get("max_drawdown", 0.0)),
            "avg_r_multiple": float(summary.get("avg_r_multiple", 0.0)),
            "trade_count": n,
        }
    except Exception as exc:
        return {"status": "unavailable", "error": str(exc)}


# --------------------------------------------------------------------------- #
# Regime                                                                      #
# --------------------------------------------------------------------------- #


_REGIME_LABELS = {0: "trend", 1: "range"}


def current_regime(
    config: dict | None = None,
    parquet_path: Path | None = None,
    bars: int = 200,
) -> dict[str, Any]:
    """Classify the most recent M15 bars as trend / range.

    Reads the bridge's parquet cache **read-only**.  Returns
    ``{"status":"unavailable","label":"unknown",...}`` if the parquet
    is missing or unreadable — we never block the dashboard on regime.
    """
    cfg = config if config is not None else load_config()
    symbol = ((cfg.get("bot") or {}).get("instruments") or ["EURUSD"])[0]
    timeframe = (cfg.get("bot") or {}).get("timeframe", "M15")
    parquet_path = parquet_path or (
        _BOT_ROOT / "bridge_data" / "history" / f"{symbol}_{timeframe}.parquet"
    )
    if not Path(parquet_path).exists():
        return {
            "status": "unavailable",
            "label": "unknown",
            "regime_id": None,
            "symbol": symbol,
            "timeframe": timeframe,
            "error": "parquet_missing",
        }
    try:
        df = pd.read_parquet(parquet_path)
        if "timestamp" in df.columns:
            df = df.sort_values("timestamp")
        elif "time" in df.columns:
            df = df.sort_values("time")
        df = df.tail(bars)
        if len(df) < 30:
            return {
                "status": "unavailable",
                "label": "unknown",
                "regime_id": None,
                "symbol": symbol,
                "timeframe": timeframe,
                "error": f"too_few_bars: {len(df)}",
            }
        from core.regime.detector import RegimeDetector

        detector = RegimeDetector.from_config(cfg) if cfg else RegimeDetector(method="vol", window=20)
        regime_id = int(detector.current_regime(df))
        return {
            "status": "ok",
            "label": _REGIME_LABELS.get(regime_id, "unknown"),
            "regime_id": regime_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "bars_used": int(len(df)),
            "error": None,
        }
    except Exception as exc:
        return {
            "status": "unavailable",
            "label": "unknown",
            "regime_id": None,
            "symbol": symbol,
            "timeframe": timeframe,
            "error": str(exc),
        }
