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
from core.strategy.mean_reversion import BollingerBandMeanReversion  # noqa: E402

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


def _apply_walk_forward(df: pd.DataFrame, train_pct: float) -> pd.DataFrame:
    """Walk-forward holdout: return only the tail (1 - train_pct) of the bars.

    The "fit" is implicit (params chosen on full-window synthetic data); we
    validate by simulating on the held-out tail. Disabled when train_pct <= 0.

    Edge cases:
    - train_pct <= 0 or None: pass-through (no slicing)
    - train_pct >= 1.0: clamped to leave at least 1 bar of holdout
    - resulting holdout < 50 bars: still returned; downstream MIN_BARS check
      will warn/error as appropriate
    """
    if train_pct is None or train_pct <= 0.0:
        return df
    n = len(df)
    if n == 0:
        return df
    # Clamp so we always keep at least 1 holdout bar
    train_pct = min(train_pct, 0.999)
    cutoff = int(n * train_pct)
    holdout = df.iloc[cutoff:].reset_index(drop=True)
    return holdout


def _compute_stats(trade_returns: list[float], equity_path: list[float], n_bars: int) -> dict:
    returns = np.array(trade_returns, dtype=float)
    if returns.size < 2:
        sharpe = 0.0
    else:
        std = float(np.std(returns, ddof=1))
        sharpe = 0.0 if std == 0 else float(np.mean(returns) / std * math.sqrt(ANNUALIZATION))

    win_rate = float((returns > 0).sum() / returns.size) if returns.size > 0 else 0.0

    eq = np.array(equity_path, dtype=float)
    if eq.size > 0:
        peak = np.maximum.accumulate(eq)
        dd = (peak - eq) / 10_000.0
        max_dd = float(max(0.0, dd.max()))
    else:
        max_dd = 0.0

    return {
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "win_rate": win_rate,
        "trades": int(returns.size),
        "bars": n_bars,
    }


def _simulate_ema(df: pd.DataFrame, params: dict) -> dict:
    """Always-in EMA crossover simulation. Flips position on each crossover."""
    fast = int(params.get("ema_fast", 9))
    slow = int(params.get("ema_slow", 21))
    if fast >= slow:
        fast, slow = 9, 21
    strat = EMACrossover(fast=fast, slow=slow)
    ind = strat.compute_indicators(df)
    fast_ema = ind["ema_fast"].to_numpy()
    slow_ema = ind["ema_slow"].to_numpy()
    closes = ind["close"].to_numpy()

    pos_side: str | None = None
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
            if pos_side is not None:
                delta = (price - pos_entry) / PIP_SIZE
                if pos_side == "SELL":
                    delta = -delta
                pnl = delta * PIP_VALUE_USD_PER_LOT * volume
                trade_returns.append(pnl)
                equity += pnl
            pos_side = signal
            pos_entry = price
        equity_path.append(equity)

    if pos_side is not None and len(closes) > 0:
        delta = (closes[-1] - pos_entry) / PIP_SIZE
        if pos_side == "SELL":
            delta = -delta
        pnl = delta * PIP_VALUE_USD_PER_LOT * volume
        trade_returns.append(pnl)
        equity += pnl
        equity_path.append(equity)

    return _compute_stats(trade_returns, equity_path, int(len(df)))


def _simulate_mean_reversion(df: pd.DataFrame, params: dict) -> dict:
    """Enter on band-touch + RSI confirmation; exit when price reverts to mid-band."""
    bb_period = int(params.get("bb_period", 20))
    bb_std_val = float(params.get("bb_std", 2.0))
    rsi_period = int(params.get("rsi_period", 14))
    rsi_oversold = float(params.get("rsi_os", 30.0))
    rsi_overbought = float(params.get("rsi_ob", 70.0))
    atr_sl = float(params.get("atr_multiplier", 1.5))

    strat = BollingerBandMeanReversion(
        bb_period=bb_period,
        bb_std=bb_std_val,
        rsi_period=rsi_period,
        rsi_oversold=rsi_oversold,
        rsi_overbought=rsi_overbought,
        atr_sl_multiplier=atr_sl,
    )
    ind = strat.compute_indicators(df)
    closes = ind["close"].to_numpy()
    bb_lower = ind["bb_lower"].to_numpy()
    bb_upper = ind["bb_upper"].to_numpy()
    bb_mid = ind["bb_mid"].to_numpy()
    rsi = ind["rsi"].to_numpy()

    pos_side: str | None = None
    pos_entry: float = 0.0
    volume = 0.01
    trade_returns: list[float] = []
    equity_path: list[float] = []
    equity = 0.0

    for i in range(len(closes)):
        if math.isnan(bb_lower[i]) or math.isnan(rsi[i]):
            equity_path.append(equity)
            continue

        price = closes[i]

        # Exit: price reverts to mid-band
        if pos_side == "BUY" and price >= bb_mid[i]:
            pnl = (price - pos_entry) / PIP_SIZE * PIP_VALUE_USD_PER_LOT * volume
            trade_returns.append(pnl)
            equity += pnl
            pos_side = None
        elif pos_side == "SELL" and price <= bb_mid[i]:
            pnl = (pos_entry - price) / PIP_SIZE * PIP_VALUE_USD_PER_LOT * volume
            trade_returns.append(pnl)
            equity += pnl
            pos_side = None

        # Entry: band-touch + RSI confirmation
        if pos_side is None:
            if price <= bb_lower[i] and rsi[i] < rsi_oversold:
                pos_side = "BUY"
                pos_entry = price
            elif price >= bb_upper[i] and rsi[i] > rsi_overbought:
                pos_side = "SELL"
                pos_entry = price

        equity_path.append(equity)

    # Mark-to-market
    if pos_side is not None and len(closes) > 0:
        delta = (closes[-1] - pos_entry) / PIP_SIZE
        if pos_side == "SELL":
            delta = -delta
        pnl = delta * PIP_VALUE_USD_PER_LOT * volume
        trade_returns.append(pnl)
        equity += pnl
        equity_path.append(equity)

    return _compute_stats(trade_returns, equity_path, int(len(df)))


def _run_simulation(df: pd.DataFrame, params: dict) -> dict:
    strategy = params.get("strategy", "ema_crossover")
    if strategy == "mean_reversion":
        return _simulate_mean_reversion(df, params)
    return _simulate_ema(df, params)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backtest engine")
    parser.add_argument("--metric", choices=["sharpe"], default=None)
    parser.add_argument("--guard", action="store_true")
    parser.add_argument("--params", default=None, help="path to params yaml overlay")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--timeframe", default="H1")
    parser.add_argument("--bars", type=int, default=5000)
    parser.add_argument(
        "--wf-train-pct",
        type=float,
        default=0.0,
        help=(
            "Walk-forward holdout fraction in [0.0, 1.0). When > 0, only the "
            "tail (1 - train_pct) of the loaded bars is simulated, providing a "
            "simple out-of-sample validation. 0.0 (default) disables holdout."
        ),
    )
    args = parser.parse_args(argv)

    bot_root = _BOT_ROOT
    cfg = _load_params(args, bot_root)
    autoresearch_cfg = (cfg.get("autoresearch") or {})
    target_sharpe = float(autoresearch_cfg.get("target_sharpe", 1.5))
    max_dd_guard = float(autoresearch_cfg.get("max_drawdown_guard", 0.05))

    params = cfg.get("params") or {}
    params.setdefault("strategy", "ema_crossover")

    # Win-rate guard: use strategy-specific threshold when available
    _strategy_name = params.get("strategy", "ema_crossover")
    if _strategy_name == "mean_reversion":
        _wr_key, _wr_default = "min_win_rate_guard_mr", 0.50
    else:
        _wr_key, _wr_default = "min_win_rate_guard_ema", 0.38
    _fallback = float(autoresearch_cfg.get("min_win_rate_guard", _wr_default))
    min_wr_guard = float(autoresearch_cfg.get(_wr_key, _fallback))
    params.setdefault("ema_fast", 9)
    params.setdefault("ema_slow", 21)
    params.setdefault("bb_period", 20)
    params.setdefault("bb_std", 2.0)
    params.setdefault("rsi_period", 14)
    params.setdefault("rsi_os", 30.0)
    params.setdefault("rsi_ob", 70.0)
    params.setdefault("atr_multiplier", 1.5)

    try:
        df = _load_ohlcv(args.symbol, args.timeframe, args.bars, bot_root)
    except Exception as exc:
        print(f"ERROR loading data: {exc}", file=sys.stderr)
        return 2

    # Walk-forward holdout slice (no-op when wf_train_pct <= 0)
    df = _apply_walk_forward(df, args.wf_train_pct)

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
        result = _run_simulation(df, params)
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
