"""
Candlestick-trigger helpers for the FX GOAT trend-following strategy.

Naked Forex (Walter Peters, Ch 8 "Kangaroo Tails") describes the *pin bar* as
the canonical price-action confirmation at structural levels: a long tail in
the direction of rejection, a small body in the opposite third of the bar's
range, and a close that breaks against the rejected move.

The FX GOAT compendium (§3 *Strategic Entry Methods*) names "specific
candlestick triggers" as one of the three ingredients of Premium execution.
This module supplies the mechanical trigger; the trend-following strategy
gates premium-mode entries through ``is_pin_bar``.

Pure-pandas / numpy. No extra deps.
"""
from __future__ import annotations

from typing import Literal, Mapping

import pandas as pd


Direction = Literal["bullish", "bearish"]


def is_pin_bar(
    bar: Mapping[str, float],
    direction: Direction,
    *,
    prior_close: float | None = None,
    body_ratio_max: float = 1.0 / 3.0,
    tail_ratio_min: float = 2.0,
) -> bool:
    """Return True when ``bar`` is a pin bar in the requested direction.

    A bullish pin bar has:
        - A long lower tail (rejection of lower prices)
        - A small body in the upper third of the bar's range
        - Close that closes above ``prior_close`` (price-action follow-through)

    A bearish pin bar is the inverse.

    Parameters
    ----------
    bar : mapping with at least ``open``, ``high``, ``low``, ``close`` keys.
    direction : ``"bullish"`` or ``"bearish"``.
    prior_close : optional close of the immediately preceding bar. When
        supplied, the function additionally requires a directional close
        against the prior bar (avoids accepting an inside pin that lacks
        any momentum confirmation).
    body_ratio_max : maximum body size as a fraction of total range.
        Default 1/3 — anything larger ceases to be a pin.
    tail_ratio_min : minimum tail-to-body ratio. Default 2.0 (Naked Forex
        canonical definition; raise to 3.0 for stricter pins).
    """
    o = float(bar["open"])
    h = float(bar["high"])
    l = float(bar["low"])
    c = float(bar["close"])
    if h <= l:
        return False
    rng = h - l
    body = abs(c - o)
    body_top = max(o, c)
    body_bot = min(o, c)
    upper_tail = h - body_top
    lower_tail = body_bot - l
    if rng <= 0 or body / rng > body_ratio_max:
        return False
    if direction == "bullish":
        if lower_tail < tail_ratio_min * max(body, rng * 1e-9):
            return False
        # Body must sit in the upper third of the range.
        if body_bot < l + (1.0 - body_ratio_max) * rng:
            return False
        if prior_close is not None and c <= float(prior_close):
            return False
        return True
    if direction == "bearish":
        if upper_tail < tail_ratio_min * max(body, rng * 1e-9):
            return False
        # Body must sit in the lower third of the range.
        if body_top > h - (1.0 - body_ratio_max) * rng:
            return False
        if prior_close is not None and c >= float(prior_close):
            return False
        return True
    raise ValueError(f"direction must be 'bullish' or 'bearish', got {direction!r}")


def is_pin_bar_at(
    df: pd.DataFrame,
    index: int,
    direction: Direction,
    *,
    body_ratio_max: float = 1.0 / 3.0,
    tail_ratio_min: float = 2.0,
) -> bool:
    """Convenience wrapper applying :func:`is_pin_bar` to a DataFrame row.

    Uses ``df.iloc[index]`` for the trigger bar and ``df.iloc[index - 1]``
    for ``prior_close`` when ``index > 0``.
    """
    if index < 0 or index >= len(df):
        raise IndexError(f"index {index} out of range for df of length {len(df)}")
    bar = df.iloc[index]
    prior = float(df.iloc[index - 1]["close"]) if index > 0 else None
    return is_pin_bar(
        bar,
        direction,
        prior_close=prior,
        body_ratio_max=body_ratio_max,
        tail_ratio_min=tail_ratio_min,
    )
