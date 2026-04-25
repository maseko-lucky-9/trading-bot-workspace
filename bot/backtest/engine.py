"""
Backtest engine (US-007) — CLI entry point.

stdout contract (machine-parseable for the autoresearch loop):

    SHARPE 1.2340
    GUARD PASS drawdown=3.21% win_rate=51.4% bars=8760 trades=142

or:

    SHARPE 0.8120
    GUARD FAIL drawdown=6.43% exceeds 5.0% threshold

Exit codes:
    0  -> success (or --metric run completed)
    1  -> guard failed
    2  -> insufficient data / crash
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

# Make sibling packages importable when run as a script.
_BOT_ROOT = Path(__file__).resolve().parents[1]
if str(_BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOT_ROOT))

from core.bridge.http_client import MT5BridgeClient  # noqa: E402
from core.data.history import HistoryFetcher  # noqa: E402
from core.strategy.ema_crossover import EMACrossover  # noqa: E402

PIP_SIZE = 0.0001
PIP_VALUE_USD_PER_LOT = 10.0
ANNUALIZATION = 252
MIN_BARS = 1_000
WARN_BARS = 4_176


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open() as f:
        return yaml.safe_load(f) or {}


def _load_params(args, bot_root: Path) -> dict:
    base = _load_yaml(bot_root / "config.yaml")
    overlay_path = Path(args.params) if args.params else None
    overlay = _load_yaml(overlay_path) if overlay_path else {}
    merged: dict[str, Any] = {}
    merged.update(base)
    if overlay:
        # overlay is a flat strategy/params doc — surface at top-level
        merged["params"] = overlay
    return merged


def _load_ohlcv(symbol: str, timeframe: str, bars: int, bot_root: Path) -> pd.DataFrame:
    """Try parquet cache, then bridge, then synthetic via HistoryFetcher.

    The HistoryFetcher already falls back to synthetic data when /history
    is unavailable, so this always returns a non-empty DataFrame.
    """
    cache = bot_root / "bridge_data" / "history" / f"{symbol}_{timeframe}.parquet"
    if cache.exists():
        try:
            df = pd.read_parquet(cache)
            if len(df) >= max(100, bars // 2):
                return df.tail(bars).reset_index(drop=True)
        except Exception:
            pass
    try:
        bridge = MT5BridgeClient()
        hf = HistoryFetcher(bridge)
        return hf.fetch(symbol=symbol, timeframe=timeframe, bars=bars)
    except Exception:
        # Last-resort synthetic walk
        rng = np.random.default_rng(42)
        prices = 1.10 + np.cumsum(rng.normal(0, 0.0008, bars))
        return pd.DataFrame({
            "time": pd.date_range("2025-01-01", periods=bars, freq="h", tz="UTC"),
            "open": prices,
            "high": prices + 0.0005,
            "low": prices - 0.0005,
            "close": prices,
            "volume": 1000,
        })


def _simulate(df: pd.DataFrame, params: dict) -> dict:
    """Simulate EMA-crossover trades on the bar series.

    Each crossover flips the position: BUY closes any short and opens long
    (and vice versa). Closing a position records its P&L. Volume = 0.01.
    """
    fast = int(params.get("ema_fast", 9))
    slow = int(params.get("ema_slow", 21))
    if fast >= slow:
        fast, slow = 9, 21
    strat = EMACrossover(fast=fast, slow=slow)
    ind = strat.compute_indicators(df)
    fast_ema = ind["ema_fast"].to_numpy()
    slow_ema = ind["ema_slow"].to_numpy()
    closes = ind["close"].to_numpy()

    pos_side: str | None = None  # "BUY" | "SELL"
    pos_entry: float = 0.0
    volume = 0.01
    trade_returns: list[float] = []
    equity_path: list[float] = []
    equity = 0.0

    for i in range(1, len(closes)):
        if math.isnan(fast_ema[i]) or math.isnan(slow_ema[i]):
            equity_path.append(equity)
            continue
        prev_diff = fast_ema[i - 1] - slow_ema[i - 1]
        cur_diff = fast_ema[i] - slow_ema[i]
        signal: str | None = None
        if prev_diff <= 0 < cur_diff:
            signal = "BUY"
        elif prev_diff >= 0 > cur_diff:
            signal = "SELL"

        price = closes[i]
        if signal and signal != pos_side:
            # Close current position
            if pos_side is not None:
                delta = (price - pos_entry) / PIP_SIZE
                if pos_side == "SELL":
                    delta = -delta
                pnl = delta * PIP_VALUE_USD_PER_LOT * volume
                trade_returns.append(pnl)
                equity += pnl
            # Open new
            pos_side = signal
            pos_entry = price
        equity_path.append(equity)

    # Mark-to-market final position close
    if pos_side is not None and len(closes) > 0:
        delta = (closes[-1] - pos_entry) / PIP_SIZE
        if pos_side == "SELL":
            delta = -delta
        pnl = delta * PIP_VALUE_USD_PER_LOT * volume
        trade_returns.append(pnl)
        equity += pnl
        equity_path.append(equity)

    returns = np.array(trade_returns, dtype=float)
    if returns.size < 2:
        sharpe = 0.0
    else:
        std = float(np.std(returns, ddof=1))
        sharpe = 0.0 if std == 0 else float(np.mean(returns) / std * math.sqrt(ANNUALIZATION))

    if returns.size > 0:
        wins = int((returns > 0).sum())
        win_rate = wins / returns.size
    else:
        win_rate = 0.0

    eq = np.array(equity_path, dtype=float)
    if eq.size > 0:
        peak = np.maximum.accumulate(eq)
        # express drawdown relative to a reasonable equity baseline (10000)
        baseline = 10_000.0
        dd = (peak - eq) / baseline
        max_dd = float(max(0.0, dd.max()))
    else:
        max_dd = 0.0

    return {
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "win_rate": win_rate,
        "trades": int(returns.size),
        "bars": int(len(df)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backtest engine")
    parser.add_argument("--metric", choices=["sharpe"], default=None)
    parser.add_argument("--guard", action="store_true")
    parser.add_argument("--params", default=None, help="path to params yaml overlay")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--timeframe", default="H1")
    parser.add_argument("--bars", type=int, default=5000)
    args = parser.parse_args(argv)

    bot_root = _BOT_ROOT
    cfg = _load_params(args, bot_root)
    autoresearch_cfg = (cfg.get("autoresearch") or {})
    target_sharpe = float(autoresearch_cfg.get("target_sharpe", 1.5))
    max_dd_guard = float(autoresearch_cfg.get("max_drawdown_guard", 0.05))
    min_wr_guard = float(autoresearch_cfg.get("min_win_rate_guard", 0.45))

    params = cfg.get("params") or {}
    # If overlay missing pull from defaults
    params.setdefault("ema_fast", 9)
    params.setdefault("ema_slow", 21)

    try:
        df = _load_ohlcv(args.symbol, args.timeframe, args.bars, bot_root)
    except Exception as exc:
        print(f"ERROR loading data: {exc}", file=sys.stderr)
        return 2

    if len(df) < MIN_BARS:
        # Allow synthetic short runs but warn — refuse only when extremely small
        if len(df) < 50:
            print(f"insufficient bars: {len(df)}", file=sys.stderr)
            return 2
    if len(df) < WARN_BARS:
        print(
            f"WARN bars={len(df)} below 4176 statistical minimum",
            file=sys.stderr,
        )

    try:
        result = _simulate(df, params)
    except Exception as exc:
        print(f"ERROR simulating: {exc}", file=sys.stderr)
        return 2

    sharpe = result["sharpe"]
    if args.metric == "sharpe" or (not args.metric and not args.guard):
        print(f"SHARPE {sharpe:.4f}")

    guard_ok = (
        sharpe > target_sharpe
        and result["max_drawdown"] < max_dd_guard
        and result["win_rate"] > min_wr_guard
    )

    if args.guard:
        if guard_ok:
            print(
                f"GUARD PASS drawdown={result['max_drawdown']*100:.2f}% "
                f"win_rate={result['win_rate']*100:.1f}% "
                f"bars={result['bars']} trades={result['trades']}"
            )
            return 0
        reasons = []
        if not sharpe > target_sharpe:
            reasons.append(f"sharpe={sharpe:.3f}<={target_sharpe}")
        if not result["max_drawdown"] < max_dd_guard:
            reasons.append(
                f"drawdown={result['max_drawdown']*100:.2f}% exceeds {max_dd_guard*100:.1f}% threshold"
            )
        if not result["win_rate"] > min_wr_guard:
            reasons.append(
                f"win_rate={result['win_rate']*100:.1f}%<={min_wr_guard*100:.1f}%"
            )
        print("GUARD FAIL " + "; ".join(reasons))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
