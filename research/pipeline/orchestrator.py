"""Orchestrator entry point.

Spawns one async ``run_book_agent`` task per book under a semaphore, drives
each through the 5-phase pipeline, and finally calls the coordinator to
produce the unified MOC ranked by global DSR.

Usage::

    python -m research.pipeline.orchestrator                 # full run
    python -m research.pipeline.orchestrator --resume        # honour checkpoints
    python -m research.pipeline.orchestrator --books 1       # only first N books
    python -m research.pipeline.orchestrator --dry-run       # phases 1-3 only
    python -m research.pipeline.orchestrator --skip-prefetch # skip MT5 history pull
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from .book_agent import run_book_agent
from .checkpoint_io import load_checkpoint
from .coordinator import run_coordinator
from .data_prefetch import (
    BridgeUnavailableError,
    DataInsufficiencyError,
    prefetch_history,
)
from .models import (
    BOOK_LIST,
    CHECKPOINT_DIR,
    SPEC_DIR,
)


CONCURRENCY = int(os.environ.get("RESEARCH_PIPELINE_CONCURRENCY", "10"))


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(name)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def _ensure_dirs() -> None:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    SPEC_DIR.mkdir(parents=True, exist_ok=True)


def _build_client(dry_run: bool):
    """Construct an Anthropic client. Returns ``None`` for dry-run."""
    if dry_run:
        return None
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(
            "ERROR: ANTHROPIC_API_KEY not set. Either export it or pass "
            "--dry-run to skip LLM calls.",
            file=sys.stderr,
        )
        sys.exit(2)
    import anthropic
    return anthropic.Anthropic(api_key=api_key)


async def _run_all_agents(
    books, client, *, semaphore: asyncio.Semaphore, dry_run: bool,
):
    tasks = []
    for agent_id, book in enumerate(books):
        cp = load_checkpoint(book.slug, agent_id)
        if cp.status in {"complete", "complete_no_strategies"}:
            print(f"[skip] {book.slug}: status={cp.status}")
            continue
        tasks.append(asyncio.create_task(
            run_book_agent(
                book, agent_id, cp, client,
                semaphore=semaphore, dry_run=dry_run,
            ),
            name=f"agent-{book.slug}",
        ))
    if not tasks:
        print("All agents already complete; nothing to run.")
        return []
    return await asyncio.gather(*tasks, return_exceptions=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="research.pipeline.orchestrator")
    parser.add_argument("--books", type=int, default=len(BOOK_LIST),
                        help="Run only the first N books (default: all 10).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip Claude API + backtest; phase 1-2 only with stubs.")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from existing checkpoints (default behaviour; "
                             "kept for explicitness).")
    parser.add_argument("--skip-prefetch", action="store_true",
                        help="Skip Phase 0 MT5 history pull. Use only when the "
                             "EURUSD M15 parquet is already populated.")
    parser.add_argument("--coordinator-only", action="store_true",
                        help="Skip agents; only run the coordinator pass over "
                             "existing trial_log + ADRs.")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)
    _ensure_dirs()

    client = _build_client(dry_run=args.dry_run)

    if args.coordinator_only:
        path = run_coordinator(client=client)
        print(f"MOC written: {path}")
        return 0

    # Phase 0 — data prefetch
    if not args.skip_prefetch and not args.dry_run:
        try:
            cache_path = prefetch_history()
            print(f"prefetch ok: {cache_path}")
        except BridgeUnavailableError as e:
            print(f"PREFETCH FAILED (bridge unavailable): {e}", file=sys.stderr)
            print("Pass --skip-prefetch if the parquet cache is already populated.",
                  file=sys.stderr)
            return 2
        except DataInsufficiencyError as e:
            print(f"PREFETCH FAILED (data insufficient): {e}", file=sys.stderr)
            return 3

    books = list(BOOK_LIST)[: args.books]
    semaphore = asyncio.Semaphore(min(CONCURRENCY, len(books)))

    results = asyncio.run(_run_all_agents(
        books, client, semaphore=semaphore, dry_run=args.dry_run,
    ))

    failed = [r for r in results if isinstance(r, Exception)]
    if failed:
        for r in failed:
            print(f"agent error: {type(r).__name__}: {r}", file=sys.stderr)

    if args.dry_run:
        print("dry-run complete; coordinator skipped.")
        return 0

    try:
        moc_path = run_coordinator(client=client)
        print(f"MOC written: {moc_path}")
    except RuntimeError as e:
        print(f"coordinator deferred: {e}", file=sys.stderr)
        print("Re-run with --coordinator-only after agents finish.", file=sys.stderr)
        return 4

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
