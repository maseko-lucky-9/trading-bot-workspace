"""
Strategy base classes (US-004).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd


@dataclass
class Signal:
    """Trade signal emitted by a strategy.

    Fields kept lean per US-004 spec; richer fields (sl/tp/confidence)
    are still available via ``meta`` for downstream sizing layers.
    """
    action: str = "HOLD"           # "BUY" | "SELL" | "HOLD"
    strength: float = 0.0          # 0..1 confidence
    reason: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    meta: dict = field(default_factory=dict)  # sl, tp, entry_price, etc.


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame) -> Signal:
        """Return a Signal computed from the supplied OHLCV frame."""
        raise NotImplementedError
