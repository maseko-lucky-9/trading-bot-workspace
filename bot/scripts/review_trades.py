#!/usr/bin/env python3
"""
Weekly trade review — F16 (Mark Douglas / Grimes discipline).

Reads ``logs/trades.csv`` (or a path supplied via --log) and produces a
human-readable summary covering:

* Overall statistics: win rate, profit factor, expectancy, avg R:R
* Breakdown by session (London / New York / Tokyo / Sydney / Off-session)
* Breakdown by day of week
* Consecutive loss streaks
* Worst 5 trades (for post-trade review)
* Best 5 trades
* Recent 10 trades (last week feel)

Usage::

    python scripts/review_trades.py
    python scripts/review_trades.py --log logs/trades.csv --days 30
    python scripts/review_trades.py --weeks 2
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import pandas as pd


# ---------------------------------------------------------------------------
# Session classification (UTC hours, mirrors SessionFilter)
# ---------------------------------------------------------------------------

_SESSIONS: dict[str, tuple[int, int]] = {
    "London":   (7,  16),
    "New York": (12, 21),
    "Tokyo":    (0,  9),
    "Sydney":   (22, 7),   # wraps midnight
}


def _session_for_hour(hour: int) -> str:
    """Return the first matching session name, or 'Off-session'."""
    for name, (start, end) in _SESSIONS.items():
        if start < end:
            if start <= hour < end:
                return name
        else:  # crosses midnight
            if hour >= start or hour < end:
                return name
    return "Off-session"


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------

def _profit_factor(profits: pd.Series) -> float:
    wins  = profits[profits > 0].sum()
    losses = profits[profits < 0].abs().sum()
    return float(wins / losses) if losses > 0 else math.inf


def _expectancy(profits: pd.Series) -> float:
    """(win% × avg_win) − (loss% × avg_loss) in $ per trade."""
    if len(profits) == 0:
        return 0.0
    wins  = profits[profits > 0]
    losses = profits[profits < 0]
    wr  = len(wins) / len(profits)
    lr  = 1.0 - wr
    avg_win  = float(wins.mean())  if len(wins)   > 0 else 0.0
    avg_loss = float(losses.mean()) if len(losses) > 0 else 0.0
    return wr * avg_win + lr * avg_loss


def _max_consecutive_losses(profits: pd.Series) -> int:
    best = cur = 0
    for p in profits:
        if p < 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _fmt(val: float, decimals: int = 2) -> str:
    return f"{val:+.{decimals}f}"


def _pct(val: float) -> str:
    return f"{val * 100:.1f}%"


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def build_report(df: pd.DataFrame, title: str = "Weekly Trade Review") -> str:
    lines: list[str] = []

    def h(text: str, level: int = 1) -> None:
        prefix = "#" * level
        lines.append(f"\n{prefix} {text}")

    def row(label: str, value: str) -> None:
        lines.append(f"  {label:<30} {value}")

    # Only closed trades (close_price populated)
    closed = df.dropna(subset=["close_price", "profit"]).copy()
    closed["profit"] = pd.to_numeric(closed["profit"], errors="coerce")
    closed = closed.dropna(subset=["profit"])

    if len(closed) == 0:
        return f"# {title}\n\nNo closed trades in the selected window.\n"

    # Parse timestamps
    for col in ("open_time", "close_time"):
        if col in closed.columns:
            closed[col] = pd.to_datetime(closed[col], utc=True, errors="coerce")

    profits = closed["profit"]

    h(title)
    lines.append(f"\n*{len(closed)} closed trades*\n")

    # ------------------------------------------------------------------
    h("Overall Statistics", 2)
    wins   = profits[profits > 0]
    losses = profits[profits < 0]
    wr     = len(wins) / len(profits) if len(profits) > 0 else 0.0

    row("Total trades",    str(len(profits)))
    row("Win rate",         _pct(wr))
    row("Total P&L",       f"${profits.sum():.2f}")
    row("Profit factor",   f"{_profit_factor(profits):.2f}" if not math.isinf(_profit_factor(profits)) else "∞")
    row("Expectancy",      f"${_expectancy(profits):.2f}/trade")
    row("Avg win",         f"${float(wins.mean()):.2f}" if len(wins) > 0 else "$0.00")
    row("Avg loss",        f"${float(losses.mean()):.2f}" if len(losses) > 0 else "$0.00")
    avg_rr = (
        abs(float(wins.mean()) / float(losses.mean()))
        if len(wins) > 0 and len(losses) > 0 and float(losses.mean()) != 0
        else 0.0
    )
    row("Avg realised R:R", f"{avg_rr:.2f}:1")
    row("Largest win",     f"${float(profits.max()):.2f}")
    row("Largest loss",    f"${float(profits.min()):.2f}")
    row("Max consec. losses", str(_max_consecutive_losses(profits)))

    # Expected R:R if present
    if "expected_rr" in closed.columns:
        closed["expected_rr"] = pd.to_numeric(closed["expected_rr"], errors="coerce")
        valid_rr = closed["expected_rr"].dropna()
        if len(valid_rr) > 0:
            row("Avg expected R:R", f"{float(valid_rr.mean()):.2f}:1")

    # ------------------------------------------------------------------
    h("By Day of Week", 2)
    if "open_time" in closed.columns and closed["open_time"].notna().any():
        closed["dow"] = closed["open_time"].dt.day_name()
        dow_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                     "Saturday", "Sunday"]
        grp = closed.groupby("dow")["profit"]
        lines.append(f"  {'Day':<12} {'Trades':>7} {'Win%':>7} {'P&L':>10}")
        lines.append(f"  {'-'*12} {'-'*7} {'-'*7} {'-'*10}")
        for day in dow_order:
            if day not in grp.groups:
                continue
            d_p = grp.get_group(day)
            d_wr = _pct(len(d_p[d_p > 0]) / len(d_p))
            lines.append(
                f"  {day:<12} {len(d_p):>7} {d_wr:>7} {d_p.sum():>+10.2f}"
            )

    # ------------------------------------------------------------------
    h("By Session", 2)
    if "open_time" in closed.columns and closed["open_time"].notna().any():
        closed["session"] = closed["open_time"].dt.hour.map(_session_for_hour)
        grp = closed.groupby("session")["profit"]
        lines.append(f"  {'Session':<12} {'Trades':>7} {'Win%':>7} {'P&L':>10}")
        lines.append(f"  {'-'*12} {'-'*7} {'-'*7} {'-'*10}")
        for sess, s_p in sorted(grp, key=lambda x: -len(x[1])):
            s_wr = _pct(len(s_p[s_p > 0]) / len(s_p))
            lines.append(
                f"  {sess:<12} {len(s_p):>7} {s_wr:>7} {s_p.sum():>+10.2f}"
            )

    # ------------------------------------------------------------------
    h("By Symbol", 2)
    if "symbol" in closed.columns:
        grp = closed.groupby("symbol")["profit"]
        lines.append(f"  {'Symbol':<10} {'Trades':>7} {'Win%':>7} {'P&L':>10}")
        lines.append(f"  {'-'*10} {'-'*7} {'-'*7} {'-'*10}")
        for sym, s_p in sorted(grp, key=lambda x: -len(x[1])):
            s_wr = _pct(len(s_p[s_p > 0]) / len(s_p))
            lines.append(
                f"  {sym:<10} {len(s_p):>7} {s_wr:>7} {s_p.sum():>+10.2f}"
            )

    # ------------------------------------------------------------------
    h("Worst 5 Trades (review intent vs. outcome)", 2)
    worst = closed.nsmallest(5, "profit")
    _trade_table(worst, lines)

    # ------------------------------------------------------------------
    h("Best 5 Trades", 2)
    best = closed.nlargest(5, "profit")
    _trade_table(best, lines)

    # ------------------------------------------------------------------
    h("Most Recent 10 Trades", 2)
    if "close_time" in closed.columns:
        recent = closed.sort_values("close_time", ascending=False).head(10)
    else:
        recent = closed.tail(10)
    _trade_table(recent, lines)

    lines.append("")
    return "\n".join(lines)


def _trade_table(subset: pd.DataFrame, lines: list[str]) -> None:
    cols = ["symbol", "type", "profit", "open_time", "close_time", "intent", "expected_rr"]
    available = [c for c in cols if c in subset.columns]
    lines.append(f"\n  {' | '.join(f'{c:>12}' for c in available)}")
    lines.append("  " + "-" * (14 * len(available)))
    for _, row in subset.iterrows():
        cells = []
        for c in available:
            v = row.get(c, "")
            if c == "profit":
                cells.append(f"{float(v):>+12.2f}")
            elif c in ("open_time", "close_time") and pd.notna(v):
                cells.append(f"{str(v)[:16]:>12}")
            elif c == "expected_rr" and pd.notna(v):
                cells.append(f"{float(v):>12.2f}")
            else:
                cells.append(f"{str(v)[:12]:>12}")
        lines.append("  " + " | ".join(cells))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Weekly trade review report")
    bot_root = Path(__file__).resolve().parents[1]
    parser.add_argument(
        "--log",
        default=str(bot_root / "logs" / "trades.csv"),
        help="Path to trades CSV (default: logs/trades.csv)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Filter to the last N calendar days (default: all)",
    )
    parser.add_argument(
        "--weeks",
        type=int,
        default=1,
        help="Filter to the last N weeks (default: 1; overridden by --days)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Write report to this file instead of stdout",
    )
    args = parser.parse_args(argv)

    log_path = Path(args.log)
    if not log_path.exists():
        print(f"ERROR: trades log not found at {log_path}", file=sys.stderr)
        return 1

    try:
        df = pd.read_csv(log_path)
    except Exception as exc:
        print(f"ERROR reading {log_path}: {exc}", file=sys.stderr)
        return 1

    # Time filter
    days = args.days if args.days is not None else args.weeks * 7
    if "open_time" in df.columns:
        df["open_time"] = pd.to_datetime(df["open_time"], utc=True, errors="coerce")
        cutoff = pd.Timestamp.utcnow() - pd.Timedelta(days=days)
        df = df[df["open_time"] >= cutoff]

    title = f"Trade Review — last {days} day(s)"
    report = build_report(df, title=title)

    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"Report written to {args.out}")
    else:
        print(report)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
