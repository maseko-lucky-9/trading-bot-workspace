"""
Market-structure helpers for the FX GOAT trend-following strategy.

Pure-pandas / numpy implementation — no extra deps.

The compendium calls for top-down structural analysis: identify swings,
classify HH / HL / LH / LL into uptrend / downtrend / range, and detect
break-of-structure (BoS) events that confirm continuation.

These helpers are stateless: every call recomputes from the supplied
DataFrame. They never write to disk or touch the bridge.
"""
from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd


Trend = Literal["uptrend", "downtrend", "range"]


def detect_swings(
    df: pd.DataFrame,
    left: int = 2,
    right: int = 2,
) -> pd.DataFrame:
    """Label each bar as a fractal swing high / low.

    A bar at index *i* is a *swing high* if its ``high`` is strictly greater
    than the high of every bar in ``df[i-left:i]`` AND in ``df[i+1:i+1+right]``.
    Symmetrically for ``swing_low`` on ``low``.

    Returns a copy of ``df`` with two new boolean columns:
        - ``swing_high``
        - ``swing_low``

    Bars within the first ``left`` or last ``right`` indices can never be
    swings (insufficient context); these are returned as ``False``.

    Parameters
    ----------
    df : DataFrame with at least ``high`` and ``low`` columns.
    left, right : int
        Bars of confirmation either side. ``2/2`` is the canonical fractal.
    """
    if left < 1 or right < 1:
        raise ValueError("left and right must both be >= 1")

    out = df.copy()
    n = len(out)
    sh = np.zeros(n, dtype=bool)
    sl = np.zeros(n, dtype=bool)

    if n < left + right + 1:
        out["swing_high"] = sh
        out["swing_low"] = sl
        return out

    high = out["high"].to_numpy()
    low = out["low"].to_numpy()

    for i in range(left, n - right):
        center_h = high[i]
        center_l = low[i]
        left_window_h = high[i - left:i]
        right_window_h = high[i + 1:i + 1 + right]
        left_window_l = low[i - left:i]
        right_window_l = low[i + 1:i + 1 + right]
        if center_h > left_window_h.max() and center_h > right_window_h.max():
            sh[i] = True
        if center_l < left_window_l.min() and center_l < right_window_l.min():
            sl[i] = True

    out["swing_high"] = sh
    out["swing_low"] = sl
    return out


def classify_trend(swings_df: pd.DataFrame) -> Trend:
    """Classify the most recent four swings into uptrend / downtrend / range.

    Logic (FX GOAT compendium):
        - Two consecutive higher highs AND two consecutive higher lows -> uptrend.
        - Two consecutive lower highs AND two consecutive lower lows  -> downtrend.
        - Anything else -> range.

    The function inspects only the LAST two swing-high values and the LAST two
    swing-low values. If fewer than two of either exist, returns ``"range"``.

    Parameters
    ----------
    swings_df : DataFrame produced by :func:`detect_swings` — must contain
        ``high``, ``low``, ``swing_high``, ``swing_low`` columns.
    """
    if not {"high", "low", "swing_high", "swing_low"}.issubset(swings_df.columns):
        raise ValueError("swings_df must come from detect_swings()")

    sh = swings_df.loc[swings_df["swing_high"], "high"].to_numpy()
    sl = swings_df.loc[swings_df["swing_low"], "low"].to_numpy()

    if len(sh) < 2 or len(sl) < 2:
        return "range"

    last_two_h = sh[-2:]
    last_two_l = sl[-2:]

    higher_high = last_two_h[1] > last_two_h[0]
    higher_low = last_two_l[1] > last_two_l[0]
    lower_high = last_two_h[1] < last_two_h[0]
    lower_low = last_two_l[1] < last_two_l[0]

    if higher_high and higher_low:
        return "uptrend"
    if lower_high and lower_low:
        return "downtrend"
    return "range"


def last_break_of_structure(
    df: pd.DataFrame,
    swings_df: pd.DataFrame,
) -> dict | None:
    """Return the most recent break-of-structure (BoS) event or ``None``.

    A bullish BoS occurs when the close of a bar exceeds the most recent
    confirmed swing-high level that preceded it. A bearish BoS is the inverse.

    The returned dict contains:
        - ``direction``: ``"bullish"`` | ``"bearish"``
        - ``bar_index``: integer index in ``df`` where the close broke through
        - ``level``: the swing level that was broken

    Returns ``None`` when no BoS is detectable in the supplied frame.
    """
    if "close" not in df.columns:
        raise ValueError("df must contain a 'close' column")
    if not {"swing_high", "swing_low"}.issubset(swings_df.columns):
        raise ValueError("swings_df must come from detect_swings()")

    closes = df["close"].to_numpy()
    swing_high_idx = np.where(swings_df["swing_high"].to_numpy())[0]
    swing_low_idx = np.where(swings_df["swing_low"].to_numpy())[0]
    high_levels = swings_df["high"].to_numpy()
    low_levels = swings_df["low"].to_numpy()

    last_event: dict | None = None

    # Walk forward; record the latest BoS we encounter.
    for i in range(1, len(closes)):
        # Find the most recent confirmed swing-high strictly before bar i.
        ph_candidates = swing_high_idx[swing_high_idx < i]
        pl_candidates = swing_low_idx[swing_low_idx < i]
        if len(ph_candidates) > 0:
            ph_idx = int(ph_candidates[-1])
            ph_level = float(high_levels[ph_idx])
            if closes[i] > ph_level and (
                last_event is None or i > last_event["bar_index"]
            ):
                last_event = {
                    "direction": "bullish",
                    "bar_index": int(i),
                    "level": ph_level,
                }
        if len(pl_candidates) > 0:
            pl_idx = int(pl_candidates[-1])
            pl_level = float(low_levels[pl_idx])
            if closes[i] < pl_level and (
                last_event is None or i > last_event["bar_index"]
            ):
                last_event = {
                    "direction": "bearish",
                    "bar_index": int(i),
                    "level": pl_level,
                }
    return last_event
