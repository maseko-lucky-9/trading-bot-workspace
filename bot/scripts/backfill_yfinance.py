#!/usr/bin/env python3
"""
Backfill H1 history from Yahoo Finance for symbols lacking sufficient bars.

Yahoo Finance provides OHLCV forex data via ``EURUSD=X`` tickers.  Volume is
synthetic (tick-count proxy), but price data matches broker data closely enough
for backtesting purposes.

Only gaps that exist BEFORE the broker data window are filled; existing broker
bars are never overwritten (broker data takes priority).

Usage::

    python scripts/backfill_yfinance.py
    python scripts/backfill_yfinance.py --symbols EURUSD GBPUSD --target 5000
    python scripts/backfill_yfinance.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from datetime import timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

# ---------------------------------------------------------------------------
BOT_ROOT   = Path(__file__).resolve().parents[1]
HISTORY_DIR = BOT_ROOT / "bridge_data" / "history"

# Yahoo Finance ticker suffixes for forex
_YAHOO_TICKER = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "USDCHF": "USDCHF=X",
    "AUDUSD": "AUDUSD=X",
    "USDCAD": "USDCAD=X",
    "NZDUSD": "NZDUSD=X",
}

_TF_INTERVAL = {
    "H1": "1h",
    "H4": "1h",  # no 4h on yf; caller can resample
    "M15": "15m",
    "M5":  "5m",
    "D1":  "1d",
}

# yfinance max look-back per interval
_TF_MAX_DAYS = {
    "1h":  730,   # ~2 years
    "15m": 59,
    "5m":  59,
    "1d":  10000,
}


def _fetch_yfinance(symbol: str, tf: str, start: str, end: str) -> pd.DataFrame:
    """Download OHLCV from Yahoo Finance and normalise to canonical schema."""
    ticker = _YAHOO_TICKER.get(symbol, f"{symbol}=X")
    interval = _TF_INTERVAL.get(tf, "1h")
    raw = yf.download(ticker, start=start, end=end, interval=interval,
                      auto_adjust=True, progress=False)
    if raw.empty:
        return pd.DataFrame()

    # Flatten MultiIndex columns produced by yfinance ≥ 0.2
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)

    raw = raw.rename(columns={
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    })
    raw = raw[["open", "high", "low", "close", "volume"]].copy()
    raw.index = pd.to_datetime(raw.index, utc=True)
    raw.index.name = "time"
    raw = raw.reset_index()
    raw["volume"] = raw["volume"].fillna(0).astype(int)
    raw["time"] = raw["time"].dt.tz_convert("UTC")
    return raw[["time", "open", "high", "low", "close", "volume"]]


def _load_existing(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    df = pd.read_parquet(path)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df


def _merge(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Merge new bars with existing, dedup on time (existing wins)."""
    if new.empty:
        return existing
    combined = pd.concat([existing, new], ignore_index=True)
    combined["time"] = pd.to_datetime(combined["time"], utc=True)
    combined = combined.drop_duplicates(subset="time", keep="first")
    combined = combined.sort_values("time").reset_index(drop=True)
    return combined


def _save(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df.to_parquet(path, index=False)


def backfill_symbol(
    symbol: str,
    tf: str = "H1",
    target: int = 5000,
    dry_run: bool = False,
) -> dict:
    path = HISTORY_DIR / f"{symbol}_{tf}.parquet"
    existing = _load_existing(path)
    current = len(existing)

    if current >= target:
        print(f"{symbol}/{tf}: already {current} bars — no backfill needed.")
        return {"symbol": symbol, "before": current, "after": current, "added": 0}

    needed = target - current
    interval = _TF_INTERVAL.get(tf, "1h")
    max_days = _TF_MAX_DAYS.get(interval, 730)

    # Determine fetch window.
    # end: day before the earliest existing bar (or today if no data yet)
    now = pd.Timestamp.now(tz="UTC")
    if not existing.empty:
        earliest = existing["time"].min()
        end_ts = earliest - pd.Timedelta(hours=1)
    else:
        end_ts = now

    # start: max_days rolling window measured from TODAY (Yahoo Finance limit)
    earliest_allowed = now - pd.Timedelta(days=max_days - 1)
    start_ts = earliest_allowed  # can't go further back than this

    if start_ts >= end_ts:
        print(f"  Nothing to fetch: start ({start_ts.date()}) >= end ({end_ts.date()}).")
        return {"symbol": symbol, "before": current, "after": current, "added": 0}

    start_str = start_ts.strftime("%Y-%m-%d")
    end_str   = end_ts.strftime("%Y-%m-%d")

    print(f"{symbol}/{tf}: have {current}/{target} bars. "
          f"Fetching {interval} from {start_str} to {end_str} ...")

    fetched = _fetch_yfinance(symbol, tf, start_str, end_str)
    if fetched.empty:
        print(f"  ⚠  No data returned from Yahoo Finance.")
        return {"symbol": symbol, "before": current, "after": current, "added": 0}

    print(f"  Downloaded {len(fetched)} bars "
          f"({fetched['time'].min()} → {fetched['time'].max()})")

    merged = _merge(existing, fetched)
    added  = len(merged) - current
    print(f"  After merge: {len(merged)} bars (+{added} new)")

    if not dry_run:
        _save(merged, path)
        print(f"  Saved → {path.relative_to(BOT_ROOT)}")
    else:
        print(f"  [dry-run] would save {len(merged)} bars to {path.name}")

    return {"symbol": symbol, "before": current, "after": len(merged), "added": added}


# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill H1 history from Yahoo Finance")
    parser.add_argument("--symbols", nargs="+", default=["EURUSD", "GBPUSD"],
                        help="Symbols to backfill (default: EURUSD GBPUSD)")
    parser.add_argument("--tf", default="H1",
                        choices=list(_TF_INTERVAL), help="Timeframe (default H1)")
    parser.add_argument("--target", type=int, default=5000,
                        help="Target bar count (default 5000)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would happen without writing")
    args = parser.parse_args(argv)

    results = []
    for sym in args.symbols:
        if sym not in _YAHOO_TICKER and f"{sym}=X" not in _YAHOO_TICKER.values():
            print(f"WARNING: {sym} not in ticker map — will try {sym}=X")
        r = backfill_symbol(sym, tf=args.tf, target=args.target, dry_run=args.dry_run)
        results.append(r)

    print("\n── Summary ──────────────────────────────────")
    for r in results:
        status = "✓" if r["after"] >= args.target else "⚠ still short"
        print(f"  {r['symbol']:8} {r['before']:>5} → {r['after']:>5} bars  "
              f"(+{r['added']:>4})  {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
