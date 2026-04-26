"""
HistoricalDataClient — bridge wrapper for fetching real OHLCV bars.

Owns application-level concerns over and above the transport-layer
``MT5BridgeClient``:

- Optional auto-fetch: triggers a FETCH_HISTORY command so the EA populates
  the bridge with real CopyRates data before paginating (all timeframes).
- Pagination: walks backward through the bridge's accumulated bars in
  PAGE_SIZE chunks until ``bars`` rows are collected or history is exhausted.
- Explicit ``BridgeUnavailableError`` on failure (NO synthetic fallback).
- Schema coercion (raw bridge dicts → canonical-schema DataFrame).
- Dedup-on-fetch (drop duplicate timestamps inside the bridge response).
- Input validation (bars > 0).

Transport retries / backoff are already provided by ``MT5BridgeClient`` via
``tenacity`` (see ``core/bridge/http_client.py``); this layer does not
re-implement them.
"""
from __future__ import annotations

import time

import pandas as pd

from core.bridge.http_client import BridgeDisconnected, MT5BridgeClient
from core.data.history_store import CANONICAL_COLUMNS, coerce_schema


class BridgeUnavailableError(BridgeDisconnected):
    """Raised when the bridge cannot supply the requested historical bars.

    Subclasses ``BridgeDisconnected`` so existing callers that catch the
    transport-layer exception also catch this one.
    """


class HistoricalDataClient:
    """Fetch canonical-schema OHLCV history from a running MT5 bridge.

    Parameters
    ----------
    bridge
        A connected ``MT5BridgeClient`` (or any object exposing
        ``get_history``, ``get_bar_count``, and ``request_fetch_history``).
    """

    _PAGE_SIZE = 500
    _POLL_INTERVAL = 0.5
    _POLL_TIMEOUT = 15.0

    def __init__(self, bridge: MT5BridgeClient) -> None:
        self.bridge = bridge

    def fetch(
        self,
        symbol: str,
        bars: int,
        timeframe: str = "H1",
        auto_fetch: bool = False,
    ) -> pd.DataFrame:
        """Fetch ``bars`` historical bars for ``symbol``/``timeframe``.

        Phase A (auto_fetch=True, opt-in): if the bridge holds fewer real
        bars than requested, queue a FETCH_HISTORY command and poll until the
        EA fills the buffer (up to _POLL_TIMEOUT seconds). Defaults to False
        so batch callers (e.g. backfill_history.py) are unaffected.

        Phase B: paginate backward through the bridge in PAGE_SIZE chunks via
        the offset param, collecting until ``bars`` rows are obtained or the
        bridge signals end-of-history with a short/empty page.

        Phase C: concatenate pages, coerce schema, dedup, sort ascending,
        return head(bars).

        Raises
        ------
        ValueError
            If ``bars <= 0``.
        BridgeUnavailableError
            If the bridge fails or returns no rows. Never falls back to
            synthetic data — failure is loud and explicit.
        """
        if bars <= 0:
            raise ValueError(f"bars must be > 0; got {bars}")

        # Phase A — ensure bridge has enough real bars
        if auto_fetch:
            self._ensure_bars(symbol, timeframe, bars)

        # Phase B — paginate backward through accumulated bars
        frames: list[pd.DataFrame] = []
        offset = 0
        collected = 0

        while collected < bars:
            need = min(self._PAGE_SIZE, bars - collected)
            try:
                rows = self.bridge.get_history(
                    symbol=symbol,
                    timeframe=timeframe,
                    bars=need,
                    offset=offset,
                )
            except BridgeDisconnected as e:
                raise BridgeUnavailableError(
                    f"bridge unreachable while fetching {symbol} {timeframe}: {e}"
                ) from e
            except Exception as e:  # pragma: no cover - defensive
                raise BridgeUnavailableError(
                    f"unexpected bridge failure for {symbol} {timeframe}: {e}"
                ) from e

            if not rows:
                break  # end of available history

            frames.append(pd.DataFrame(rows, columns=CANONICAL_COLUMNS))
            collected += len(rows)
            offset += len(rows)
            if len(rows) < need:
                break  # partial page — no more history beyond this point

        if not frames:
            raise BridgeUnavailableError(
                f"bridge returned empty response for {symbol} {timeframe} "
                f"(bars={bars}); refusing to synthesise"
            )

        # Phase C — combine, coerce, dedup, sort, cap
        df = pd.concat(frames, ignore_index=True)
        df = coerce_schema(df)
        df = df.drop_duplicates(subset="time").sort_values("time").reset_index(drop=True)
        return df.iloc[:bars] if len(df) > bars else df

    def _ensure_bars(self, symbol: str, timeframe: str, bars: int) -> None:
        """Queue FETCH_HISTORY if bridge has fewer real bars than needed; poll until filled."""
        try:
            count = int(self.bridge.get_bar_count(symbol, timeframe))
        except Exception:
            return  # bridge doesn't expose get_bar_count — skip auto-fetch

        if count >= bars:
            return

        try:
            self.bridge.request_fetch_history(symbol, timeframe, bars)
        except Exception:
            return  # best-effort: proceed with whatever bars are available

        deadline = time.time() + self._POLL_TIMEOUT
        while time.time() < deadline:
            time.sleep(self._POLL_INTERVAL)
            try:
                if int(self.bridge.get_bar_count(symbol, timeframe)) >= bars:
                    break
            except Exception:
                break  # bridge stopped responding — abort poll
