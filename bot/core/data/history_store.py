"""
History store — parquet I/O + prefer-existing merge for the H1 backfill module.

Owns:
- Canonical schema for ``bridge_data/history/<SYMBOL>_H1.parquet``.
- Reading existing parquet (returns ``None`` if absent).
- Schema coercion for incoming DataFrames (sort, dedup, dtypes).
- Prefer-existing merge semantics (cached row wins on timestamp conflict).
- Atomic write (``.tmp`` then ``os.replace``) to prevent torn files.

Design choices: see ``pipeline/design-brief.md`` (Decision 3 — prefer-existing).
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

CANONICAL_COLUMNS: list[str] = ["time", "open", "high", "low", "close", "volume"]
"""Column order locked to match existing ``bridge_data/history/*.parquet``."""

_TIME_DTYPE = "datetime64[ms, UTC]"
_FLOAT_COLS = ("open", "high", "low", "close")


class SchemaMismatchError(ValueError):
    """Raised when a DataFrame cannot be coerced to the canonical schema, or
    when a write would persist invalid data (unsorted / duplicate timestamps)."""


def coerce_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of ``df`` with the canonical schema enforced.

    Steps:
    1. Validate all canonical columns are present.
    2. Coerce ``time`` to ``datetime64[ms, UTC]`` (accepts epoch-seconds ints
       or already-typed timestamps).
    3. Coerce OHLC to ``float64`` and ``volume`` to ``int64``.
    4. Sort by ``time`` ascending, drop duplicate timestamps (keep first), and
       reset the index.

    Raises
    ------
    SchemaMismatchError
        If required columns are missing.
    """
    missing = [c for c in CANONICAL_COLUMNS if c not in df.columns]
    if missing:
        raise SchemaMismatchError(
            f"DataFrame missing canonical columns: {missing}; "
            f"got columns={list(df.columns)}"
        )

    out = df[CANONICAL_COLUMNS].copy()

    # Coerce time → tz-aware UTC ms.
    time_col = out["time"]
    if pd.api.types.is_datetime64_any_dtype(time_col):
        if time_col.dt.tz is None:
            time_col = time_col.dt.tz_localize("UTC")
        else:
            time_col = time_col.dt.tz_convert("UTC")
        out["time"] = time_col.astype(_TIME_DTYPE)
    else:
        # Assume epoch seconds (int / float).
        out["time"] = pd.to_datetime(time_col, unit="s", utc=True).astype(_TIME_DTYPE)

    for c in _FLOAT_COLS:
        out[c] = out[c].astype("float64")
    out["volume"] = out["volume"].astype("int64")

    out = (
        out.sort_values("time", kind="mergesort")
        .drop_duplicates(subset=["time"], keep="first")
        .reset_index(drop=True)
    )
    return out


def read_existing(path: Path) -> pd.DataFrame | None:
    """Read a cached parquet at ``path``; return ``None`` if it doesn't exist.

    Re-coerces to the canonical schema so callers can rely on dtype invariants
    even if an older parquet has slight schema drift.
    """
    if not Path(path).exists():
        return None
    df = pd.read_parquet(path)
    return coerce_schema(df)


def merge_prefer_existing(
    cached: pd.DataFrame, fetched: pd.DataFrame
) -> pd.DataFrame:
    """Merge ``cached`` and ``fetched`` keeping the cached row on overlap.

    Both inputs may be empty (the canonical column shape is preserved). The
    output is canonicalised (sorted, deduped, dtype-clean).
    """
    # Empty-safe: build canonical empties so concat doesn't lose dtypes.
    if cached is None or cached.empty:
        cached = pd.DataFrame(columns=CANONICAL_COLUMNS)
    if fetched is None or fetched.empty:
        fetched = pd.DataFrame(columns=CANONICAL_COLUMNS)

    # Stack cached FIRST so drop_duplicates(keep="first") prefers cached rows.
    combined = pd.concat([cached, fetched], ignore_index=True)
    if combined.empty:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)
    return coerce_schema(combined)


def write_atomic(df: pd.DataFrame, path: Path) -> Path:
    """Write ``df`` to ``path`` atomically (``.tmp`` then ``os.replace``).

    Validates that ``df`` is sorted ascending by ``time`` and has unique
    timestamps before writing — this catches programmer errors that would
    otherwise corrupt the cache.
    """
    path = Path(path)

    if not df["time"].is_monotonic_increasing:
        raise SchemaMismatchError("DataFrame is not sorted ascending by 'time'")
    if not df["time"].is_unique:
        raise SchemaMismatchError("DataFrame has duplicate 'time' values")

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)  # atomic on POSIX
    return path
