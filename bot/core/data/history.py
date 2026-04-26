"""
HistoryFetcher (US-003).

Fetches OHLCV bars from the bridge ``/history`` endpoint and caches them
to parquet. If the live bridge does not yet expose ``/history`` (the
running instance may pre-date this commit), a deterministic synthetic
walk is produced locally so the rest of the pipeline still runs.
"""
from __future__ import annotations

import random
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from core.bridge.http_client import MT5BridgeClient

_TF_SECONDS = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800,
    "H1": 3600, "H4": 14400, "D1": 86400,
}


class HistoryFetcher:
    """Fetch + cache OHLCV history.

    Parameters
    ----------
    bridge : MT5BridgeClient
    cache_dir : Path | None, optional
        Override the parquet cache directory; defaults to
        ``bridge_data/history`` under the bot/ root.
    """

    def __init__(self, bridge: MT5BridgeClient, cache_dir: Path | None = None) -> None:
        self.bridge = bridge
        bot_root = Path(__file__).resolve().parents[2]
        self.cache_dir = cache_dir or bot_root / "bridge_data" / "history"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    # Cache helpers                                                      #
    # ------------------------------------------------------------------ #

    def _cache_path(self, symbol: str, timeframe: str) -> Path:
        return self.cache_dir / f"{symbol}_{timeframe}.parquet"

    def load_cache(self, symbol: str, timeframe: str) -> pd.DataFrame | None:
        p = self._cache_path(symbol, timeframe)
        if not p.exists():
            return None
        try:
            return pd.read_parquet(p)
        except Exception:
            return None

    def save_cache(self, df: pd.DataFrame, symbol: str, timeframe: str) -> Path:
        p = self._cache_path(symbol, timeframe)
        df.to_parquet(p, index=False)
        return p

    # ------------------------------------------------------------------ #
    # Synthetic fallback                                                 #
    # ------------------------------------------------------------------ #

    def _synthesize(self, symbol: str, timeframe: str, bars: int) -> list[dict]:
        seconds = _TF_SECONDS.get(timeframe.upper(), 3600)
        # Try to seed off the live tick so the walk feels current.
        try:
            tick = self.bridge.get_tick(symbol)
            base = float(tick.get("bid") or 1.10000) or 1.10000
        except Exception:
            base = 1.10000
        seed_int = int((base * 1_000_000) % 2**31) ^ (hash(symbol) & 0x7fffffff)
        rng = random.Random(seed_int)

        now = int(time.time())
        end = now - (now % seconds)
        walk = []
        p = base
        for _ in range(bars):
            p = max(0.5, p + rng.gauss(0, 0.0008))
            walk.append(p)
        walk.reverse()

        out = []
        for i, close in enumerate(walk):
            prev = walk[i - 1] if i > 0 else close
            high = max(prev, close) + abs(rng.gauss(0, 0.0003))
            low = min(prev, close) - abs(rng.gauss(0, 0.0003))
            out.append({
                "time": end - (bars - 1 - i) * seconds,
                "open": round(prev, 5),
                "high": round(high, 5),
                "low": round(low, 5),
                "close": round(close, 5),
                "volume": rng.randint(50, 5000),
            })
        return out

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #

    def fetch(
        self, symbol: str = "EURUSD", timeframe: str = "H1", bars: int = 500
    ) -> pd.DataFrame:
        df, _source = self.fetch_with_source(symbol, timeframe, bars)
        return df

    def fetch_with_source(
        self, symbol: str = "EURUSD", timeframe: str = "H1", bars: int = 500
    ) -> tuple[pd.DataFrame, str]:
        """Fetch and report whether the data came from the bridge or the
        synthetic random walk fallback. Returns ("bridge", "synthetic")."""
        rows: list[dict] = []
        bridge_source = "unknown"
        try:
            # Prefer the source-aware API. Detect via duck-typing on the
            # *return value* — MagicMock spoofs hasattr(), so we must inspect
            # what actually comes back.
            got = None
            if hasattr(self.bridge, "get_history_with_source"):
                got = self.bridge.get_history_with_source(
                    symbol=symbol, timeframe=timeframe, bars=bars,
                )
            if (
                isinstance(got, tuple)
                and len(got) == 2
                and isinstance(got[0], list)
            ):
                rows, bridge_source = got
            else:
                rows = self.bridge.get_history(
                    symbol=symbol, timeframe=timeframe, bars=bars,
                )
        except Exception:
            rows = []
        # Bridge may itself report "synthetic" when the EA hasn't supplied
        # real bars for the requested symbol/timeframe — propagate that.
        synthetic_from_bridge = bridge_source.lower() == "synthetic"
        if not rows or synthetic_from_bridge:
            if not rows:
                rows = self._synthesize(symbol, timeframe, bars)
            source = "synthetic"
        else:
            source = "bridge"

        df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        for c in ("open", "high", "low", "close"):
            df[c] = df[c].astype(float)
        df["volume"] = df["volume"].astype(int)
        df = df.sort_values("time").reset_index(drop=True)
        # Cache only real bridge data; never persist synthetic fallback noise
        # to the parquet store (would silently corrupt later real backtests).
        if source == "bridge":
            try:
                self.save_cache(df, symbol, timeframe)
            except Exception:
                pass
        return df, source
