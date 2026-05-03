"""Phase 0 — pull 2020-2024 EURUSD M15 history from the MT5 bridge.

The thin 200-bar parquet cache at ``bot/bridge_data/history/EURUSD_M15.parquet``
is replaced with a full ~5 year window so the backtest engine has real data to
score strategies against. Synthetic fallback is explicitly refused — DSR on
random-walk returns is meaningless.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bot"))

from core.bridge.http_client import MT5BridgeClient  # noqa: E402
from core.data.history import HistoryFetcher  # noqa: E402

REQUIRED_BARS = 100_000   # ~3.5 years of M15 (forex 24/5)
TARGET_BARS = 130_000     # request enough to cover 2020-01-01 onwards
TARGET_START = datetime(2020, 1, 1, tzinfo=timezone.utc)
SYMBOL = "EURUSD"
TIMEFRAME = "M15"


class BridgeUnavailableError(RuntimeError):
    """Raised when the MT5 bridge cannot be reached."""


class DataInsufficiencyError(RuntimeError):
    """Raised when the bridge returns too few bars or doesn't reach back to 2020."""


def prefetch_history(
    symbol: str = SYMBOL,
    timeframe: str = TIMEFRAME,
    target_bars: int = TARGET_BARS,
    bridge_url: str = "http://localhost:8080",
) -> Path:
    """Fetch and cache historical OHLCV bars for backtesting.

    Returns the path to the written parquet file. Raises if the bridge is
    unreachable or if the returned data does not cover 2020-01-01 onwards.
    """
    client = MT5BridgeClient(base_url=bridge_url)
    if not client.ping():
        raise BridgeUnavailableError(
            f"MT5 bridge at {bridge_url} did not respond. Start the bridge "
            f"(and the EA on the Windows VM) before running this pipeline."
        )

    fetcher = HistoryFetcher(client)
    df, source = fetcher.fetch_with_source(
        symbol=symbol, timeframe=timeframe, bars=target_bars,
    )
    if source == "synthetic":
        raise DataInsufficiencyError(
            "Bridge returned synthetic data. The MT5 EA either lacks historical "
            "bars or the /history endpoint is unimplemented. Refusing to run "
            "backtests on random-walk data."
        )

    if "time" not in df.columns:
        raise DataInsufficiencyError("History dataframe missing 'time' column.")

    if len(df) < REQUIRED_BARS:
        raise DataInsufficiencyError(
            f"Got {len(df)} bars; need at least {REQUIRED_BARS} for 2020-2024 "
            f"coverage. Check MT5 history depth on the EA side."
        )

    earliest = pd.to_datetime(df["time"].min(), utc=True)
    if earliest > TARGET_START:
        raise DataInsufficiencyError(
            f"Earliest bar is {earliest.isoformat()} but pipeline requires "
            f"coverage from {TARGET_START.isoformat()}. Increase MT5 history "
            f"depth or shorten the backtest window in plan."
        )

    cache_path = fetcher.save_cache(df, symbol=symbol, timeframe=timeframe)
    return cache_path


def main(argv: list[str] | None = None) -> int:
    try:
        path = prefetch_history()
    except BridgeUnavailableError as e:
        print(f"PREFETCH FAIL bridge_unavailable: {e}", file=sys.stderr)
        return 2
    except DataInsufficiencyError as e:
        print(f"PREFETCH FAIL data_insufficient: {e}", file=sys.stderr)
        return 3
    df = pd.read_parquet(path)
    earliest = pd.to_datetime(df["time"].min(), utc=True).date()
    latest = pd.to_datetime(df["time"].max(), utc=True).date()
    print(f"PREFETCH OK rows={len(df)} range={earliest}..{latest} path={path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
