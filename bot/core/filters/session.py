"""
SessionFilter — gate new entries to configured forex trading sessions.

Sessions are defined as UTC hour ranges. Exits (SL/TP) are never blocked;
only new entry signals are gated.

Standard UTC windows (winter offsets):
  sydney    22:00–07:00 UTC  (crosses midnight)
  tokyo     00:00–09:00 UTC
  london    07:00–16:00 UTC
  new_york  12:00–21:00 UTC

Overlap london/new_york (12:00–16:00 UTC) is the most liquid window for
EURUSD and GBPUSD.

Config (new canonical path)::

    filters:
      sessions:
        enabled: true
        allowed: [london, new_york]

Legacy path also supported (strategy_config.session_filter) for backward
compatibility with existing configs.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd


_SESSION_UTC: dict[str, tuple[int, int]] = {
    "sydney":    (22, 7),
    "tokyo":     (0,  9),
    "london":    (7,  16),
    "new_york":  (12, 21),
}


def _hour_in_range(hour: int, start: int, end: int) -> bool:
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end   # crosses midnight


class SessionFilter:
    """Gate new entries to one or more named forex sessions.

    Supports two constructor signatures:

    1. Config-dict (canonical)::

           sf = SessionFilter({"enabled": True, "allowed": ["london", "new_york"]})

    2. Keyword (legacy, kept for backward compatibility)::

           sf = SessionFilter(sessions=["london"], enabled=True)

    Both ``is_active(dt)`` and ``is_trading_hour(dt)`` are exposed so
    callers using either naming convention work without change.
    """

    def __init__(
        self,
        config: dict | None = None,
        *,
        sessions: list[str] | None = None,
        enabled: bool | None = None,
    ) -> None:
        # Allow positional config dict OR keyword-style legacy construction
        cfg = config if isinstance(config, dict) else {}
        if sessions is not None:
            # Legacy kwargs override dict values
            cfg = dict(cfg)
            cfg["allowed"] = sessions
        if enabled is not None:
            cfg = dict(cfg)
            cfg["enabled"] = enabled

        self.enabled: bool = bool(cfg.get("enabled", True))
        allowed: list[str] = list(cfg.get("allowed", list(_SESSION_UTC)))
        self._ranges: list[tuple[int, int]] = []
        for name in allowed:
            key = name.lower().replace(" ", "_")
            if key in _SESSION_UTC:
                self._ranges.append(_SESSION_UTC[key])
        for pair in cfg.get("custom_ranges", []):
            self._ranges.append((int(pair[0]), int(pair[1])))

        # Backward-compat property expected by legacy tests
        self.active_sessions: list[str] = allowed

    # ------------------------------------------------------------------

    def _get_utc_hour(self, bar_time) -> int | None:
        """Extract UTC hour from various timestamp types. Returns None on failure."""
        if isinstance(bar_time, (int, float)):
            return None  # no calendar info
        if isinstance(bar_time, pd.Timestamp):
            ts = bar_time
            if ts.tzinfo is not None:
                ts = ts.tz_convert("UTC")
            return ts.hour
        if isinstance(bar_time, datetime):
            if bar_time.tzinfo is not None:
                return bar_time.astimezone(timezone.utc).hour
            return bar_time.hour
        try:
            ts = pd.Timestamp(bar_time)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            return ts.hour
        except Exception:
            return None

    def is_active(self, bar_time) -> bool:
        """Return True if a new entry is permitted at ``bar_time``."""
        if not self.enabled or not self._ranges:
            return True
        hour = self._get_utc_hour(bar_time)
        if hour is None:
            return True  # synthetic / index-only — pass through
        return any(_hour_in_range(hour, s, e) for s, e in self._ranges)

    def is_trading_hour(self, bar_time) -> bool:
        """Alias for :meth:`is_active` (backward compatibility)."""
        return self.is_active(bar_time)

    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, config: dict) -> "SessionFilter":
        """Build from the top-level bot config dict.

        Checks ``filters.sessions`` first; falls back to the legacy
        ``strategy_config.session_filter`` key so existing configs continue
        to work unchanged.
        """
        # New canonical path
        filters_cfg = (config.get("filters") or {}).get("sessions")
        if filters_cfg is not None:
            return cls(filters_cfg)
        # Legacy path
        legacy_cfg = (config.get("strategy_config") or {}).get("session_filter") or {}
        sessions = legacy_cfg.get("sessions") or list(_SESSION_UTC)
        enabled = bool(legacy_cfg.get("enabled", False))
        return cls({"enabled": enabled, "allowed": sessions})
