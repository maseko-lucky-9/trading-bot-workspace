"""
Backfill the H1 OHLCV parquet cache from the running MT5 bridge.

Usage
-----
    python -m scripts.backfill_history
    python -m scripts.backfill_history --target 5000 --symbols EURUSD,GBPUSD,USDJPY

Behaviour
---------
- For each requested symbol, reads the cached parquet at
  ``bridge_data/history/<SYMBOL>_H1.parquet``. If it already holds
  ``--target`` bars, skips the symbol (no bridge call).
- Otherwise asks the bridge for ``--target`` bars in a single request,
  merges into the cache with **prefer-existing** semantics (cached row
  wins on timestamp conflict), and writes atomically.
- Logs per-symbol: cached_before / fetched / cached_after / status / elapsed.
- Exits non-zero on any bridge failure (no synthetic fallback).

Symbol resolution: ``--symbols a,b,c`` overrides; otherwise reads
``bot.instruments`` from ``config.yaml``.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Any, Sequence

import yaml

from core.bridge.http_client import MT5BridgeClient
from core.data.historical_client import (
    BridgeUnavailableError,
    HistoricalDataClient,
)
from core.data.history_store import (
    merge_prefer_existing,
    read_existing,
    write_atomic,
)

_BOT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CACHE_DIR = _BOT_ROOT / "bridge_data" / "history"
_DEFAULT_CONFIG_PATH = _BOT_ROOT / "config.yaml"
_DEFAULT_TARGET = 5000

logger = logging.getLogger("backfill_history")


# ----------------------------------------------------------------- argument parsing

def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="backfill_history",
        description=(
            "Top up bridge_data/history/<SYMBOL>_H1.parquet with real MT5 bars. "
            "Idempotent: re-running with the target met is a no-op."
        ),
    )
    p.add_argument(
        "--target",
        type=int,
        default=_DEFAULT_TARGET,
        help=f"Minimum cached bar count per symbol (default {_DEFAULT_TARGET}).",
    )
    p.add_argument(
        "--symbols",
        type=str,
        default=None,
        help="Comma-separated symbol list (e.g. 'EURUSD,GBPUSD,USDJPY'). "
        "If omitted, reads bot.instruments from config.yaml.",
    )
    p.add_argument(
        "--timeframe",
        type=str,
        default="H1",
        help="Timeframe code (default H1). Only H1 is supported by the cache today.",
    )
    p.add_argument(
        "--cache-dir",
        type=Path,
        default=_DEFAULT_CACHE_DIR,
        help=f"Override cache directory (default {_DEFAULT_CACHE_DIR}).",
    )
    p.add_argument(
        "--config",
        type=Path,
        default=_DEFAULT_CONFIG_PATH,
        help=f"Path to config.yaml (default {_DEFAULT_CONFIG_PATH}).",
    )
    p.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        help="Logging level (DEBUG / INFO / WARNING).",
    )
    return p.parse_args(argv)


# ----------------------------------------------------------------- symbol resolution

def resolve_symbols(
    cli_symbols: str | None, config: dict[str, Any]
) -> list[str]:
    """CLI takes precedence over ``bot.instruments`` from config."""
    if cli_symbols:
        symbols = [s.strip() for s in cli_symbols.split(",") if s.strip()]
        if not symbols:
            raise ValueError("--symbols was provided but parsed to an empty list")
        return symbols

    instruments = (config.get("bot") or {}).get("instruments") or []
    if not instruments:
        raise ValueError(
            "No symbols specified: pass --symbols or set bot.instruments in config.yaml"
        )
    return list(instruments)


# ----------------------------------------------------------------- per-symbol backfill

def backfill_one(
    *,
    symbol: str,
    target: int,
    timeframe: str,
    cache_dir: Path,
    bridge: MT5BridgeClient,
) -> dict[str, Any]:
    """Top up the cache for one symbol. Returns a stats dict for logging."""
    started = time.time()
    cache_path = cache_dir / f"{symbol}_{timeframe}.parquet"
    cached = read_existing(cache_path)
    cached_before = 0 if cached is None else len(cached)

    if cached_before >= target:
        return {
            "symbol": symbol,
            "status": "noop",
            "cached_before": cached_before,
            "fetched": 0,
            "cached_after": cached_before,
            "elapsed_s": round(time.time() - started, 2),
        }

    client = HistoricalDataClient(bridge=bridge)
    fetched_df = client.fetch(symbol=symbol, bars=target, timeframe=timeframe)
    fetched_count = len(fetched_df)

    merged = merge_prefer_existing(
        cached if cached is not None else fetched_df.iloc[0:0], fetched_df
    )
    write_atomic(merged, cache_path)

    return {
        "symbol": symbol,
        "status": "fetched",
        "cached_before": cached_before,
        "fetched": fetched_count,
        "cached_after": len(merged),
        "elapsed_s": round(time.time() - started, 2),
    }


# ----------------------------------------------------------------- bridge factory

def _build_bridge(config: dict[str, Any]) -> MT5BridgeClient:
    """Construct an MT5BridgeClient from config. Isolated for test patching."""
    bridge_cfg = config.get("bridge") or {}
    base_url = bridge_cfg.get("base_url", "http://localhost:8080")
    return MT5BridgeClient(
        base_url=base_url,
        heartbeat_timeout=int(bridge_cfg.get("heartbeat_timeout", 10)),
    )


def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


# ----------------------------------------------------------------- main

def _format_stats(stats: dict[str, Any]) -> str:
    return (
        f"{stats['symbol']}: status={stats['status']} "
        f"cached_before={stats['cached_before']} "
        f"fetched={stats['fetched']} "
        f"cached_after={stats['cached_after']} "
        f"elapsed={stats['elapsed_s']}s"
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    try:
        config = _load_config(args.config)
        symbols = resolve_symbols(cli_symbols=args.symbols, config=config)
    except (FileNotFoundError, ValueError) as e:
        logger.error("Configuration error: %s", e)
        return 2

    args.cache_dir.mkdir(parents=True, exist_ok=True)
    bridge = _build_bridge(config)

    overall_started = time.time()
    logger.info(
        "Backfill start: symbols=%s target=%d timeframe=%s cache_dir=%s",
        symbols,
        args.target,
        args.timeframe,
        args.cache_dir,
    )

    failures: list[str] = []
    for symbol in symbols:
        try:
            stats = backfill_one(
                symbol=symbol,
                target=args.target,
                timeframe=args.timeframe,
                cache_dir=args.cache_dir,
                bridge=bridge,
            )
            logger.info(_format_stats(stats))
        except BridgeUnavailableError as e:
            logger.error("%s: bridge unavailable — %s", symbol, e)
            failures.append(symbol)
        except Exception as e:  # pragma: no cover - defensive
            logger.exception("%s: unexpected error: %s", symbol, e)
            failures.append(symbol)

    total_elapsed = round(time.time() - overall_started, 2)
    logger.info(
        "Backfill done: %d symbol(s), %d failure(s), elapsed=%ss",
        len(symbols),
        len(failures),
        total_elapsed,
    )

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
