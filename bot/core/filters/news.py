"""
NewsBlackout — skip new entries within ``buffer_minutes`` of a scheduled
high-impact economic event.

Disabled by default (``enabled: false`` in config) because it requires a
populated calendar file.  When disabled every bar passes unconditionally so
the rest of the pipeline is unaffected.

Calendar CSV schema
-------------------
Required columns: ``time`` (ISO-8601 UTC), ``impact`` (string), ``currency`` (string).
Optional: ``event`` (description string, ignored).

Only rows with ``impact`` == "High" (case-insensitive) are loaded.
Currency filtering: if ``symbol`` is supplied to :meth:`is_active`, only
events whose ``currency`` appears in the symbol string are checked (e.g.
symbol ``"GBPUSD"`` blocks GBP and USD events).  Pass ``symbol=""`` to block
all high-impact events regardless of currency.

Example config:
    filters:
      news_blackout:
        enabled: true
        buffer_minutes: 30
        calendar_path: "data/news_calendar.csv"
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd


class NewsBlackout:
    """Gate new entries around high-impact news events.

    Parameters
    ----------
    config : dict
        The ``filters.news_blackout`` sub-dict from config.yaml.
    bot_root : Path | None
        Repository root used to resolve relative ``calendar_path``.
    """

    def __init__(self, config: dict | None = None, bot_root: Path | None = None) -> None:
        cfg = config or {}
        self.enabled: bool = bool(cfg.get("enabled", False))
        self.buffer: pd.Timedelta = pd.Timedelta(
            minutes=int(cfg.get("buffer_minutes", 30))
        )
        self._events: pd.DatetimeIndex = pd.DatetimeIndex([])

        if self.enabled:
            cal = cfg.get("calendar_path", "")
            if cal:
                path = Path(cal) if Path(cal).is_absolute() else Path(bot_root or ".") / cal
                self._load(path)

    # ------------------------------------------------------------------

    def _load(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            df = pd.read_csv(path)
            if "time" not in df.columns or "impact" not in df.columns:
                return
            df = df[df["impact"].str.lower() == "high"].copy()
            df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
            df = df.dropna(subset=["time"])
            self._events = pd.DatetimeIndex(df["time"])
            self._currencies: list[str] = (
                df["currency"].str.upper().tolist()
                if "currency" in df.columns else []
            )
        except Exception:
            pass  # malformed calendar → no blackout applied

    # ------------------------------------------------------------------

    def is_active(self, bar_time, symbol: str = "") -> bool:
        """Return True if a new entry is permitted at ``bar_time``.

        False means the bar falls within ``buffer_minutes`` of a
        high-impact event whose currency matches ``symbol``.
        """
        if not self.enabled or len(self._events) == 0:
            return True
        try:
            ts = pd.Timestamp(bar_time)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
        except Exception:
            return True

        sym = symbol.upper()
        for i, ev in enumerate(self._events):
            if abs(ts - ev) <= self.buffer:
                if not sym:
                    return False
                curr = self._currencies[i] if i < len(self._currencies) else ""
                if curr in sym:
                    return False
        return True

    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, config: dict, bot_root: Path | None = None) -> "NewsBlackout":
        """Build from the top-level bot config dict."""
        nb_cfg = (config.get("filters") or {}).get("news_blackout") or {}
        return cls(nb_cfg, bot_root=bot_root)
