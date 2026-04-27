"""
Backtest engine — CLI entry point.

stdout contract (machine-parseable for the autoresearch loop):

    SHARPE 1.2340
    GUARD PASS drawdown=3.21% win_rate=51.4% bars=8760 trades=142

or:

    SHARPE 0.8120
    GUARD FAIL drawdown=6.43% exceeds 5.0% threshold

Exit codes:
    0  -> success (or --metric run completed)
    1  -> guard failed
    2  -> insufficient data / crash / synthetic-data refused

Wave 0 changes:
- F17 — simulator drives ``Strategy.generate_signal`` on a windowed df instead of
  reimplementing entry logic inline.
- F18 — ``RiskManager.size_position`` and ``check_circuit_breakers`` are wired
  in; positions are sized off simulated equity (``backtest.starting_equity``).
- F1  — per-symbol spread + slippage costs deducted on every closed trade.
- F2  — Sharpe / Sortino / Calmar are computed from a daily-resampled equity
  curve, not per-trade dollar P&L.
- F3  — drawdown is ``(peak_equity - equity) / peak_equity``.
- F7  — entry price is the *next* bar's open, not the signal bar's close.
- F4  — ``--cv kfold:N --embargo M`` runs purged k-fold; ``--wf-train-pct``
  remains as a deprecated alias.
- F13 — synthetic data refused with exit 2 unless ``--allow-synthetic``.
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, field
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
from core.filters.news import NewsBlackout  # noqa: E402
from core.filters.session import SessionFilter  # noqa: E402
from core.regime.detector import RegimeDetector  # noqa: E402
from core.risk.manager import RiskManager, LOT_STEP  # noqa: E402
from core.strategy.base import Signal  # noqa: E402
from core.strategy.ema_crossover import EMACrossover  # noqa: E402
from core.strategy.mean_reversion import BollingerBandMeanReversion  # noqa: E402
from core.strategy.meta_labeller import MetaLabeller  # noqa: E402

PIP_SIZE = 0.0001
PIP_SIZE_JPY = 0.01
PIP_VALUE_USD_PER_LOT = 10.0
ANNUALIZATION = 252
MIN_BARS = 1_000
WARN_BARS = 4_176
DEFAULT_STARTING_EQUITY = 10_000.0


# ---------------------------------------------------------------------------
# Cost model
# ---------------------------------------------------------------------------

@dataclass
class SymbolCosts:
    """Per-symbol cost model. All amounts in pips of the symbol."""
    spread_pips: float = 1.0
    slippage_pips: float = 0.5  # extra pips against you on a stop-out fill
    commission_per_lot: float = 0.0  # USD per side per standard lot

    @classmethod
    def from_config(cls, config: dict, symbol: str) -> "SymbolCosts":
        costs = (config.get("backtest") or {}).get("costs") or {}
        sym_costs = costs.get(symbol) or costs.get("default") or {}
        return cls(
            spread_pips=float(sym_costs.get("spread_pips", _default_spread(symbol))),
            slippage_pips=float(sym_costs.get("slippage_pips", 0.5)),
            commission_per_lot=float(sym_costs.get("commission_per_lot", 0.0)),
        )


def _default_spread(symbol: str) -> float:
    s = symbol.upper()
    if "JPY" in s:
        return 1.5
    return 1.0


def _pip_size_for(symbol: str) -> float:
    return PIP_SIZE_JPY if "JPY" in symbol.upper() else PIP_SIZE


# ---------------------------------------------------------------------------
# Strategy / config plumbing
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open() as f:
        return yaml.safe_load(f) or {}


def _load_params(args, bot_root: Path) -> dict:
    config_path = Path(args.config) if getattr(args, "config", None) else bot_root / "config.yaml"
    base = _load_yaml(config_path)
    overlay_path = Path(args.params) if args.params else None
    overlay = _load_yaml(overlay_path) if overlay_path else {}
    merged: dict[str, Any] = {}
    merged.update(base)
    if overlay:
        merged["params"] = overlay
    return merged


def _build_strategy(params: dict):
    """Build the live strategy instance the simulator drives.

    Caller-side validation: EMA fast >= slow falls back to defaults silently
    (matches old engine behaviour; the autoresearch loop relies on this).
    """
    name = params.get("strategy", "ema_crossover")
    if name == "mean_reversion":
        return BollingerBandMeanReversion(
            bb_period=int(params.get("bb_period", 20)),
            bb_std=float(params.get("bb_std", 2.0)),
            rsi_period=int(params.get("rsi_period", 14)),
            rsi_oversold=float(params.get("rsi_os", 30.0)),
            rsi_overbought=float(params.get("rsi_ob", 70.0)),
            atr_sl_multiplier=float(params.get("atr_multiplier", 1.5)),
            atr_tp_multiplier=float(params.get("atr_tp_multiplier", 2.0)),
        )
    fast = int(params.get("ema_fast", 9))
    slow = int(params.get("ema_slow", 21))
    if fast >= slow:
        fast, slow = 9, 21
    return EMACrossover(
        fast=fast,
        slow=slow,
        atr_sl_multiplier=float(params.get("atr_multiplier", 1.5)),
        atr_tp_multiplier=float(params.get("atr_tp_multiplier", 3.0)),
    )


# ---------------------------------------------------------------------------
# Data loading (with synthetic-fail support, F13)
# ---------------------------------------------------------------------------

@dataclass
class _LoadResult:
    df: pd.DataFrame
    source: str  # "cache" | "bridge" | "synthetic"


def _load_ohlcv(symbol: str, timeframe: str, bars: int, bot_root: Path) -> pd.DataFrame:
    """Load OHLCV bars; legacy entry point that returns a DataFrame.

    New code should use :func:`_load_ohlcv_with_source` to also receive the
    source label so synthetic data can be refused (F13).
    """
    return _load_ohlcv_with_source(symbol, timeframe, bars, bot_root).df


def _load_ohlcv_with_source(symbol: str, timeframe: str, bars: int,
                             bot_root: Path) -> _LoadResult:
    """Load OHLCV bars and report which source produced them."""
    cache = bot_root / "bridge_data" / "history" / f"{symbol}_{timeframe}.parquet"
    if cache.exists():
        try:
            df = pd.read_parquet(cache)
            if len(df) >= max(100, bars // 2):
                return _LoadResult(df.tail(bars).reset_index(drop=True), "cache")
        except Exception:
            pass
    try:
        bridge = MT5BridgeClient()
        hf = HistoryFetcher(bridge)
        df, source = hf.fetch_with_source(symbol=symbol, timeframe=timeframe, bars=bars)
        return _LoadResult(df, source)
    except Exception:
        rng = np.random.default_rng(42)
        prices = 1.10 + np.cumsum(rng.normal(0, 0.0008, bars))
        df = pd.DataFrame({
            "time": pd.date_range("2025-01-01", periods=bars, freq="h", tz="UTC"),
            "open": prices,
            "high": prices + 0.0005,
            "low": prices - 0.0005,
            "close": prices,
            "volume": 1000,
        })
        return _LoadResult(df, "synthetic")


# ---------------------------------------------------------------------------
# Cross-validation slicing (F4)
# ---------------------------------------------------------------------------

def _apply_walk_forward(df: pd.DataFrame, train_pct: float) -> pd.DataFrame:
    """Tail-holdout. Deprecated alias for ``--cv kfold:N --embargo M``.

    Kept for backwards compatibility with the autoresearch loop and existing
    tests. New code should use :func:`_purged_kfold_indexes`.
    """
    if train_pct is None or train_pct <= 0.0:
        return df
    n = len(df)
    if n == 0:
        return df
    train_pct = min(train_pct, 0.999)
    cutoff = int(n * train_pct)
    return df.iloc[cutoff:].reset_index(drop=True)


def _purged_kfold_indexes(
    n: int,
    n_splits: int,
    embargo: int,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Purged k-fold splits with embargo.

    Returns a list of (train_idx, test_idx) for each fold. The test fold is a
    contiguous block; the embargo removes ``embargo`` bars on each side of the
    test block from the train set so ATR/EMA leakage is bounded.

    Reference: López de Prado, *Advances in Financial Machine Learning*, Ch. 7.
    """
    if n_splits < 2 or n < n_splits * (embargo * 2 + 2):
        # Not enough data for a meaningful k-fold; return a single fold using
        # the last 1/n_splits as test.
        cutoff = int(n * (1 - 1 / max(n_splits, 2)))
        return [(np.arange(0, max(0, cutoff - embargo)), np.arange(cutoff, n))]

    fold_size = n // n_splits
    splits: list[tuple[np.ndarray, np.ndarray]] = []
    for k in range(n_splits):
        test_start = k * fold_size
        test_end = (k + 1) * fold_size if k < n_splits - 1 else n
        test_idx = np.arange(test_start, test_end)
        train_lo = max(0, test_start - embargo)
        train_hi = min(n, test_end + embargo)
        train_idx = np.concatenate([
            np.arange(0, train_lo),
            np.arange(train_hi, n),
        ])
        splits.append((train_idx, test_idx))
    return splits


# ---------------------------------------------------------------------------
# Trade accounting (F1, F3, F7)
# ---------------------------------------------------------------------------

def _gross_pnl_usd(side: str, entry: float, exit_price: float,
                   volume: float, pip_size: float) -> float:
    """Direction-adjusted gross P&L in USD before costs."""
    delta_pips = (exit_price - entry) / pip_size
    if side == "SELL":
        delta_pips = -delta_pips
    return delta_pips * PIP_VALUE_USD_PER_LOT * volume


def _trade_costs_usd(volume: float, costs: SymbolCosts, is_stop: bool) -> float:
    """Per-round-trip costs in USD: spread (always) + slippage (on stops) +
    commission (both sides)."""
    spread = costs.spread_pips * PIP_VALUE_USD_PER_LOT * volume
    slippage = costs.slippage_pips * PIP_VALUE_USD_PER_LOT * volume if is_stop else 0.0
    commission = 2 * costs.commission_per_lot * volume
    return spread + slippage + commission


# ---------------------------------------------------------------------------
# Performance stats (F2, F3)
# ---------------------------------------------------------------------------

def _compute_stats(
    trades: list[dict] | None = None,
    equity_curve: list[dict] | None = None,
    n_bars: int = 0,
    starting_equity: float = DEFAULT_STARTING_EQUITY,
) -> dict:
    """Compute Sharpe/Sortino/Calmar from a per-bar equity curve.

    The Sharpe formula here is the literature-standard:
        Sharpe = mean(daily_returns) / std(daily_returns) * sqrt(252)
    where daily returns are derived from a per-bar equity series resampled to
    daily frequency.

    ``trades`` is a list of {profit, ...} dicts; used for win-rate. The
    equity_curve is a list of {time, equity} dicts. When equity_curve is
    empty/None, falls back to per-trade returns (legacy behaviour) so existing
    tests that only have trades still work.
    """
    trades = trades or []
    equity_curve = equity_curve or []

    # Detect legacy positional call: equity_curve is a list of floats (no
    # 'time' field). In that case, fall back to per-trade-PnL Sharpe.
    has_time_series = bool(
        equity_curve
        and isinstance(equity_curve[0], dict)
        and "time" in equity_curve[0]
    )

    if has_time_series:
        eq_df = pd.DataFrame(equity_curve)
        eq_df["time"] = pd.to_datetime(eq_df["time"], utc=True)
        eq_df = eq_df.sort_values("time").set_index("time")
        # Resample to daily; forward-fill (positions held across days)
        daily = eq_df["equity"].resample("1D").last().ffill().dropna()
        if len(daily) >= 2:
            returns = daily.pct_change().dropna()
            if len(returns) >= 2:
                std = float(returns.std(ddof=1))
                sharpe = 0.0 if std == 0 else float(
                    returns.mean() / std * math.sqrt(ANNUALIZATION)
                )
                downside = returns[returns < 0]
                d_std = float(downside.std(ddof=1)) if len(downside) >= 2 else 0.0
                sortino = (
                    0.0 if d_std == 0
                    else float(returns.mean() / d_std * math.sqrt(ANNUALIZATION))
                )
            else:
                sharpe = 0.0
                sortino = 0.0
        else:
            sharpe = 0.0
            sortino = 0.0

        eq = eq_df["equity"].to_numpy(dtype=float)
        peak = np.maximum.accumulate(eq)
        # F3: peak-equity denominator (not literal $10k)
        with np.errstate(divide="ignore", invalid="ignore"):
            dd_series = np.where(peak > 0, (peak - eq) / peak, 0.0)
        max_dd = float(max(0.0, np.nanmax(dd_series))) if dd_series.size else 0.0
        calmar = (
            0.0 if max_dd == 0
            else float(sharpe / max_dd) if sharpe > 0 else 0.0
        )
    else:
        # Legacy fallback: per-trade dollar P&L with √252 scaling. Retained so
        # existing unit tests against ``_compute_stats(trade_returns, ...)``
        # continue to function. New code paths populate equity_curve.
        returns = np.array([t.get("profit", 0.0) for t in trades], dtype=float) \
            if trades and isinstance(trades[0], dict) \
            else np.array(trades or [], dtype=float)
        if returns.size < 2:
            sharpe = 0.0
        else:
            std = float(np.std(returns, ddof=1))
            sharpe = 0.0 if std == 0 else float(
                np.mean(returns) / std * math.sqrt(ANNUALIZATION)
            )
        sortino = 0.0
        max_dd = 0.0
        calmar = 0.0

    profits = [float(t.get("profit", 0.0)) if isinstance(t, dict) else float(t)
               for t in trades]
    if profits:
        wins = sum(1 for p in profits if p > 0)
        win_rate = float(wins / len(profits))
    else:
        win_rate = 0.0

    return {
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "max_drawdown": max_dd,
        "win_rate": win_rate,
        "trades": len(profits) if profits else 0,
        "bars": int(n_bars),
    }


# Legacy alias kept for tests that imported _compute_stats with positional args.
def _compute_stats_legacy(trade_returns: list[float],
                          equity_path: list[float],
                          n_bars: int) -> dict:
    """Legacy entry point used by existing unit tests."""
    return _compute_stats(
        trades=[{"profit": r} for r in trade_returns],
        equity_curve=None,
        n_bars=n_bars,
    )


# ---------------------------------------------------------------------------
# Event-driven simulator (F17, F18, F7)
# ---------------------------------------------------------------------------

@dataclass
class _Position:
    side: str
    entry: float
    sl: float
    tp: float
    volume: float
    entry_idx: int
    entry_time: Any
    meta_features: Any = None   # np.ndarray captured at entry for MetaLabeller
    meta_prob: float = 0.0      # P(profit) from MetaLabeller at entry; 0 if unused


@dataclass
class _SimState:
    equity: float
    peak_equity: float
    day_start_equity: float
    last_day: Any = None
    positions: list[_Position] = field(default_factory=list)
    closed: list[dict] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)


def _close_position(
    state: _SimState,
    pos: _Position,
    exit_price: float,
    exit_time: Any,
    pip_size: float,
    costs: SymbolCosts,
    reason: str,
    is_stop: bool,
) -> None:
    gross = _gross_pnl_usd(pos.side, pos.entry, exit_price, pos.volume, pip_size)
    cost = _trade_costs_usd(pos.volume, costs, is_stop=is_stop)
    pnl = gross - cost
    state.equity += pnl
    state.closed.append({
        "side": pos.side,
        "entry": pos.entry,
        "exit": exit_price,
        "volume": pos.volume,
        "profit": pnl,
        "gross": gross,
        "cost": cost,
        "entry_time": pos.entry_time,
        "exit_time": exit_time,
        "reason": reason,
        "meta_prob": pos.meta_prob,
    })


def _mark_to_market(state: _SimState, mark: float, pip_size: float) -> float:
    """Equity including unrealised P&L of open positions (no costs deducted —
    costs are charged only when a position closes)."""
    mtm = state.equity
    for pos in state.positions:
        mtm += _gross_pnl_usd(pos.side, pos.entry, mark, pos.volume, pip_size)
    return mtm


def _run_event_loop(
    df: pd.DataFrame,
    params: dict,
    config: dict,
    symbol: str,
) -> dict:
    """Drive the live strategy + RiskManager across a bar series.

    Implements F17 (live ``generate_signal``), F18 (RiskManager wired),
    F7 (next-bar-open entry), F1 (per-trade costs), F3 (peak-equity DD).
    """
    strat = _build_strategy(params)
    risk = RiskManager(config)
    starting_equity = float(
        (config.get("backtest") or {}).get("starting_equity", DEFAULT_STARTING_EQUITY)
    )
    costs = SymbolCosts.from_config(config, symbol)
    pip_size = _pip_size_for(symbol)
    session_filter = SessionFilter.from_config(config)
    news_filter = NewsBlackout.from_config(config, bot_root=_BOT_ROOT)

    # Regime detection — pre-compute for full df (deterministic for vol method)
    regime_cfg = (config.get("filters") or {}).get("regime") or {}
    regime_enabled = bool(regime_cfg.get("enabled", True))
    strategy_regime_map: dict = regime_cfg.get("strategy_regime_map") or {}
    allowed_regimes: list[int] | None = None
    if regime_enabled and strat.name in strategy_regime_map:
        allowed_regimes = [int(r) for r in strategy_regime_map[strat.name]]
    regimes: pd.Series | None = None
    if regime_enabled:
        regimes = RegimeDetector.from_config(config).detect(df)

    # Meta-labeller — optional probability-of-profit wrapper
    ml_cfg = (config.get("filters") or {}).get("meta_labeller") or \
             config.get("meta_labeller") or {}
    ml_enabled = bool(ml_cfg.get("enabled", False))
    if ml_enabled:
        strat = MetaLabeller.from_config(config, base_strategy=strat)

    state = _SimState(
        equity=starting_equity,
        peak_equity=starting_equity,
        day_start_equity=starting_equity,
    )

    # Minimum bars before strategy can produce a signal (enough for indicators
    # to warm up). EMACrossover needs slow+atr_period+2; MR needs bb+atr+2.
    warmup = 50
    base_strat = strat.base_strategy if isinstance(strat, MetaLabeller) else strat
    if isinstance(base_strat, EMACrossover):
        warmup = max(base_strat.slow, base_strat.atr_period) + 5
    elif isinstance(base_strat, BollingerBandMeanReversion):
        warmup = max(base_strat.bb_period, base_strat.rsi_period, base_strat.atr_period) + 5

    n = len(df)
    if n < warmup + 2:
        return _compute_stats(
            trades=state.closed,
            equity_curve=state.equity_curve,
            starting_equity=starting_equity,
            n_bars=n,
        )

    times = df["time"].values if "time" in df.columns else None

    for i in range(warmup, n - 1):  # n-1 because F7 needs bar i+1's open
        bar = df.iloc[i]
        next_bar = df.iloc[i + 1]
        bar_time = bar["time"] if "time" in df.columns else i
        next_open = float(next_bar["open"])

        # 1. Day boundary -> reset day-start equity baseline
        if isinstance(bar_time, pd.Timestamp):
            today = bar_time.date()
            if state.last_day != today:
                state.day_start_equity = state.equity
                state.last_day = today

        # 2. Process exits (SL/TP) on this bar's H/L
        high = float(bar["high"])
        low = float(bar["low"])
        for pos in list(state.positions):
            exit_price = None
            is_stop = False
            if pos.side == "BUY":
                if low <= pos.sl:
                    exit_price = pos.sl
                    is_stop = True
                elif high >= pos.tp:
                    exit_price = pos.tp
                    is_stop = False  # TP hit isn't a slippage-prone stop
            else:  # SELL
                if high >= pos.sl:
                    exit_price = pos.sl
                    is_stop = True
                elif low <= pos.tp:
                    exit_price = pos.tp
                    is_stop = False
            if exit_price is not None:
                _close_position(
                    state, pos, exit_price, bar_time, pip_size, costs,
                    reason=("sl_hit" if is_stop else "tp_hit"),
                    is_stop=is_stop,
                )
                state.positions.remove(pos)
                # Feed outcome to MetaLabeller so it can retrain incrementally
                if isinstance(strat, MetaLabeller) and pos.meta_features is not None:
                    strat.record_outcome(pos.meta_features, state.closed[-1]["profit"])

        # 3. Circuit-breaker check (F18)
        account = {"balance": state.equity, "equity": state.equity}
        ok, _reason = risk.check_circuit_breakers(
            account=account,
            recent_closed=state.closed,
            peak_equity=state.peak_equity,
            day_start_equity=state.day_start_equity,
        )

        # 4. Generate signal on the closed-bar window
        window = df.iloc[: i + 1]
        signal = strat.generate_signal(window)

        # Annotate signal with current regime (available for callers and logs)
        current_regime: int | None = int(regimes.iloc[i]) if regimes is not None else None
        if current_regime is not None:
            signal.meta["regime"] = current_regime

        # 5. Entry — at next bar's open + half-spread for direction.
        # Session, news, and regime filters gate new entries only; exits (SL/TP)
        # processed in step 2 are never blocked.
        regime_ok = (
            allowed_regimes is None
            or current_regime is None
            or current_regime in allowed_regimes
        )
        if (
            ok
            and not state.positions
            and signal.action in ("BUY", "SELL")
            and signal.meta.get("sl") is not None
            and signal.meta.get("tp") is not None
            and session_filter.is_active(bar_time)
            and news_filter.is_active(bar_time, symbol)
            and regime_ok
        ):
            half_spread = (costs.spread_pips * pip_size) / 2.0
            entry_price = (
                next_open + half_spread if signal.action == "BUY"
                else next_open - half_spread
            )
            volume = risk.size_position(
                symbol, signal, account, window,
                trade_history=state.closed,
            )
            adj = risk.get_position_adjustment(state.equity, state.peak_equity)
            volume = max(0.0, volume * adj)
            # Round to LOT_STEP
            if volume > 0:
                volume = round(round(volume / LOT_STEP) * LOT_STEP, 2)
            if volume >= LOT_STEP:
                # Capture MetaLabeller features + probability for this entry
                ml_features = (
                    strat._last_features.copy()
                    if isinstance(strat, MetaLabeller) and strat._last_features is not None
                    else None
                )
                ml_prob = float(signal.strength) if isinstance(strat, MetaLabeller) else 0.0
                state.positions.append(_Position(
                    side=signal.action,
                    entry=entry_price,
                    sl=float(signal.meta["sl"]),
                    tp=float(signal.meta["tp"]),
                    volume=volume,
                    entry_idx=i + 1,
                    entry_time=next_bar["time"] if "time" in df.columns else i + 1,
                    meta_features=ml_features,
                    meta_prob=ml_prob,
                ))

        # 6. Mark-to-market and update peak
        mtm_equity = _mark_to_market(state, float(bar["close"]), pip_size)
        state.peak_equity = max(state.peak_equity, mtm_equity)
        state.equity_curve.append({"time": bar_time, "equity": mtm_equity})

    # 7. Close any open positions at the final close
    if state.positions and n > 0:
        last = df.iloc[-1]
        last_close = float(last["close"])
        last_time = last["time"] if "time" in df.columns else n - 1
        for pos in list(state.positions):
            _close_position(
                state, pos, last_close, last_time, pip_size, costs,
                reason="end_of_series", is_stop=False,
            )
        state.positions.clear()
        # Record final equity point
        state.equity_curve.append({"time": last_time, "equity": state.equity})

    return _compute_stats(
        trades=state.closed,
        equity_curve=state.equity_curve,
        starting_equity=starting_equity,
        n_bars=n,
    )


def _run_simulation(df: pd.DataFrame, params: dict,
                    config: dict | None = None,
                    symbol: str = "EURUSD") -> dict:
    """Top-level dispatch. Always uses the unified event loop now (F17/F18)."""
    return _run_event_loop(df, params, config or {}, symbol)


# Legacy adapter kept so existing tests that call ``_simulate_ema(df, params)``
# without the config/symbol context still pass. They were testing an older
# inlined simulator that no longer exists; we route them through the new loop
# with default config so the high-level invariants (returns dict, finite
# Sharpe, win-rate in [0,1], bars==len(df)) still hold.
def _simulate_ema(df: pd.DataFrame, params: dict) -> dict:
    p = dict(params)
    p.setdefault("strategy", "ema_crossover")
    return _run_event_loop(df, p, {}, "EURUSD")


def _simulate_mean_reversion(df: pd.DataFrame, params: dict) -> dict:
    p = dict(params)
    p["strategy"] = "mean_reversion"
    return _run_event_loop(df, p, {}, "EURUSD")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_cv_arg(cv_arg: str | None) -> tuple[str, int] | None:
    """Parse ``--cv kfold:5`` style argument."""
    if not cv_arg:
        return None
    if ":" in cv_arg:
        kind, n = cv_arg.split(":", 1)
        try:
            return kind.lower(), int(n)
        except ValueError:
            return None
    return cv_arg.lower(), 5


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backtest engine")
    parser.add_argument("--metric", choices=["sharpe", "sortino", "calmar"], default=None)
    parser.add_argument("--guard", action="store_true")
    parser.add_argument("--params", default=None, help="path to params yaml overlay")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--timeframe", default="H1")
    parser.add_argument("--bars", type=int, default=5000)
    parser.add_argument(
        "--wf-train-pct",
        type=float,
        default=0.0,
        help="DEPRECATED. Use --cv kfold:N --embargo M instead. "
             "Tail-holdout fraction in [0.0, 1.0); 0 disables.",
    )
    parser.add_argument(
        "--cv",
        default=None,
        help="Cross-validation: 'kfold:N' for purged k-fold (recommended).",
    )
    parser.add_argument(
        "--embargo",
        type=int,
        default=24,
        help="Embargo in bars on each side of test fold (default 24 = 1 day on H1).",
    )
    parser.add_argument(
        "--allow-synthetic",
        action="store_true",
        help="Allow running on synthetic random-walk data (for development only).",
    )
    parser.add_argument(
        "--min-trades",
        type=int,
        default=0,
        help="Minimum average trades per CV fold; param sets below this return "
             "Sharpe=0 (ignored when 0 or when --cv is not used).",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to config.yaml override (default: bot root config.yaml).",
    )
    args = parser.parse_args(argv)

    bot_root = _BOT_ROOT
    cfg = _load_params(args, bot_root)
    autoresearch_cfg = (cfg.get("autoresearch") or {})
    target_sharpe = float(autoresearch_cfg.get("target_sharpe", 1.5))
    max_dd_guard = float(autoresearch_cfg.get("max_drawdown_guard", 0.05))

    params = cfg.get("params") or {}
    params.setdefault("strategy", "ema_crossover")

    _strategy_name = params.get("strategy", "ema_crossover")
    if _strategy_name == "mean_reversion":
        _wr_key, _wr_default = "min_win_rate_guard_mr", 0.50
    else:
        _wr_key, _wr_default = "min_win_rate_guard_ema", 0.30
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
        loaded = _load_ohlcv_with_source(args.symbol, args.timeframe, args.bars, bot_root)
    except Exception as exc:
        print(f"ERROR loading data: {exc}", file=sys.stderr)
        return 2

    # F13: refuse synthetic data unless explicitly allowed.
    if loaded.source == "synthetic" and not args.allow_synthetic:
        print(
            f"ERROR refusing synthetic data for {args.symbol}/{args.timeframe}; "
            f"pass --allow-synthetic to override (development only).",
            file=sys.stderr,
        )
        return 2

    df = loaded.df

    # CV mode (F4) — purged k-fold takes precedence over --wf-train-pct
    cv = _parse_cv_arg(args.cv)
    if cv and cv[0] == "kfold":
        n_splits = cv[1]
        try:
            result = _run_cv(df, params, cfg, args.symbol, n_splits, args.embargo,
                             min_trades_per_fold=args.min_trades)
        except Exception as exc:
            print(f"ERROR simulating CV: {exc}", file=sys.stderr)
            return 2
    else:
        df = _apply_walk_forward(df, args.wf_train_pct)
        if len(df) < 50:
            print(f"insufficient bars: {len(df)}", file=sys.stderr)
            return 2
        if len(df) < WARN_BARS:
            print(
                f"WARN bars={len(df)} below 4176 statistical minimum",
                file=sys.stderr,
            )
        try:
            result = _run_simulation(df, params, cfg, args.symbol)
        except Exception as exc:
            print(f"ERROR simulating: {exc}", file=sys.stderr)
            return 2

    metric_value = result.get(args.metric or "sharpe", result["sharpe"])
    if args.metric or (not args.metric and not args.guard):
        # Backwards-compat: still print SHARPE line by default; print
        # named-metric line when a non-default metric was requested.
        if args.metric and args.metric != "sharpe":
            print(f"{args.metric.upper()} {metric_value:.4f}")
        print(f"SHARPE {result['sharpe']:.4f}")

    guard_ok = (
        result["sharpe"] > target_sharpe
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
        if not result["sharpe"] > target_sharpe:
            reasons.append(f"sharpe={result['sharpe']:.3f}<={target_sharpe}")
        if not result["max_drawdown"] < max_dd_guard:
            reasons.append(
                f"drawdown={result['max_drawdown']*100:.2f}% exceeds "
                f"{max_dd_guard*100:.1f}% threshold"
            )
        if not result["win_rate"] > min_wr_guard:
            reasons.append(
                f"win_rate={result['win_rate']*100:.1f}%<={min_wr_guard*100:.1f}%"
            )
        print("GUARD FAIL " + "; ".join(reasons))
        return 1

    return 0


def _run_cv(df: pd.DataFrame, params: dict, config: dict,
            symbol: str, n_splits: int, embargo: int,
            min_trades_per_fold: int = 0) -> dict:
    """Run purged k-fold CV; aggregate per-fold Sharpe by simple mean.

    When ``min_trades_per_fold > 0`` and the average trade count per test fold
    falls below this threshold, Sharpe is forced to 0 and a WARN line is
    written to stderr.  This prevents noise-inflated Sharpes from parameter
    sets that barely trade.
    """
    splits = _purged_kfold_indexes(len(df), n_splits, embargo)
    fold_results: list[dict] = []
    for _train_idx, test_idx in splits:
        if test_idx.size < 50:
            continue
        test_df = df.iloc[test_idx].reset_index(drop=True)
        fold_results.append(_run_simulation(test_df, params, config, symbol))
    if not fold_results:
        return _compute_stats(trades=[], equity_curve=[], n_bars=len(df))

    total_trades = int(sum(r["trades"] for r in fold_results))
    avg_trades = total_trades / len(fold_results)
    if min_trades_per_fold > 0 and avg_trades < min_trades_per_fold:
        print(
            f"WARN min_trades_not_met avg_per_fold={avg_trades:.1f} "
            f"< {min_trades_per_fold}",
            file=sys.stderr,
        )
        return {
            "sharpe": 0.0,
            "sortino": 0.0,
            "calmar": 0.0,
            "win_rate": 0.0,
            "max_drawdown": 0.0,
            "trades": total_trades,
            "bars": int(len(df)),
            "n_folds": len(fold_results),
        }

    sharpes = [r["sharpe"] for r in fold_results]
    win_rates = [r["win_rate"] for r in fold_results]
    max_dds = [r["max_drawdown"] for r in fold_results]
    return {
        "sharpe": float(np.mean(sharpes)) if sharpes else 0.0,
        "sortino": float(np.mean([r.get("sortino", 0.0) for r in fold_results])),
        "calmar": float(np.mean([r.get("calmar", 0.0) for r in fold_results])),
        "win_rate": float(np.mean(win_rates)) if win_rates else 0.0,
        "max_drawdown": float(np.max(max_dds)) if max_dds else 0.0,
        "trades": total_trades,
        "bars": int(len(df)),
        "n_folds": len(fold_results),
    }


if __name__ == "__main__":
    sys.exit(main())
