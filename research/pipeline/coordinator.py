"""Coordinator (post-run): rank, cluster, propose novel combinations, write MOC.

Reads the global trial log to compute the deflated Sharpe ratio (DSR) using
the **full** trial pool — this is the only correct denominator for multiple-
testing correction. Per-book DSR (which the agents do not compute) would
under-state the penalty across the 10-book sweep.

Steps
-----
1. Load ``research/trial_log.tsv``.
2. Compute global DSR via ``bot/autoresearch/loop.deflated_sharpe``.
3. Back-fill DSR + confidence into each existing ADR file.
4. Rank by DSR; cluster by mapped type.
5. Single Claude call to propose 3 novel combinations.
6. Render MOC to ``$V/MOCs/MOC - Strategy Research Pipeline.md``.
"""
from __future__ import annotations

import csv
import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .adr_writer import update_dsr_in_adr
from .checkpoint_io import all_checkpoints
from .models import (
    ADR_OUTPUT_DIR,
    BOOK_LIST,
    EXISTING_STRATEGY_TYPES,
    MOC_PATH,
    REPO_ROOT,
    TRIAL_LOG_PATH,
)

# Pull deflated_sharpe from the existing autoresearch module
sys.path.insert(0, str(REPO_ROOT / "bot"))
from autoresearch.loop import deflated_sharpe  # noqa: E402

log = logging.getLogger("coordinator")

CLUSTER_PARAM_BUCKETS = 3   # discretization granularity for clustering
COMBO_MODEL = "claude-sonnet-4-6"


@dataclass
class TrialRow:
    sr_id: int
    book_slug: str
    strategy_name: str
    mapped_type: str
    sharpe: float
    max_dd: float
    win_rate: float
    guard: str
    trades: int


def load_trials() -> list[TrialRow]:
    if not TRIAL_LOG_PATH.exists():
        return []
    rows: list[TrialRow] = []
    with TRIAL_LOG_PATH.open("r") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for r in reader:
            try:
                rows.append(TrialRow(
                    sr_id=int(r["sr_id"]),
                    book_slug=r["book_slug"],
                    strategy_name=r["strategy_name"],
                    mapped_type=r["mapped_type"],
                    sharpe=float(r["sharpe"]),
                    max_dd=float(r["max_dd"]),
                    win_rate=float(r["win_rate"]),
                    guard=r["guard"],
                    trades=int(r["trades"]),
                ))
            except (KeyError, ValueError):
                continue
    return rows


def compute_global_dsr(rows: list[TrialRow]) -> dict[int, float]:
    """Return {sr_id: dsr} using the full trial pool as the null distribution."""
    sharpes = [r.sharpe for r in rows]
    return {r.sr_id: deflated_sharpe(r.sharpe, sharpes) for r in rows}


def find_adr(sr_id: int) -> Path | None:
    if not ADR_OUTPUT_DIR.exists():
        return None
    matches = list(ADR_OUTPUT_DIR.glob(f"SR-{sr_id:03d}-*.md"))
    return matches[0] if matches else None


def _backfill_dsr(rows: list[TrialRow], dsr: dict[int, float]) -> int:
    n = 0
    for r in rows:
        path = find_adr(r.sr_id)
        if path is None:
            log.warning("ADR for SR-%03d not found", r.sr_id)
            continue
        update_dsr_in_adr(path, dsr[r.sr_id])
        n += 1
    return n


# --------------------------------------------------------------------------- #
# Ranking + clustering                                                        #
# --------------------------------------------------------------------------- #

def _book_title(slug: str) -> str:
    for b in BOOK_LIST:
        if b.slug == slug:
            return b.title
    return slug


def render_ranking_table(rows: list[TrialRow], dsr: dict[int, float]) -> str:
    sorted_rows = sorted(rows, key=lambda r: dsr[r.sr_id], reverse=True)
    lines = [
        "| Rank | SR | Strategy | Book | Type | DSR | Sharpe | Max DD | WR | Trades | Guard |",
        "|------|----|---------|------|------|-----|--------|--------|----|--------|------|",
    ]
    for rank, r in enumerate(sorted_rows, 1):
        lines.append(
            f"| {rank} | SR-{r.sr_id:03d} | {r.strategy_name} | "
            f"{_book_title(r.book_slug)} | `{r.mapped_type}` | "
            f"{dsr[r.sr_id]:.4f} | {r.sharpe:.3f} | "
            f"{r.max_dd:.2f}% | {r.win_rate:.1f}% | {r.trades} | {r.guard} |"
        )
    return "\n".join(lines)


def cluster_by_type(rows: list[TrialRow]) -> dict[str, list[TrialRow]]:
    out: dict[str, list[TrialRow]] = {t: [] for t in EXISTING_STRATEGY_TYPES}
    for r in rows:
        out.setdefault(r.mapped_type, []).append(r)
    return out


def render_clusters(
    clusters: dict[str, list[TrialRow]], dsr: dict[int, float],
) -> str:
    blocks: list[str] = []
    for typ in EXISTING_STRATEGY_TYPES:
        members = clusters.get(typ, [])
        if not members:
            continue
        members_sorted = sorted(
            members, key=lambda r: dsr[r.sr_id], reverse=True,
        )
        blocks.append(f"### `{typ}` ({len(members_sorted)} strategies)")
        for r in members_sorted:
            blocks.append(
                f"- SR-{r.sr_id:03d} **{r.strategy_name}** — "
                f"{_book_title(r.book_slug)} — DSR={dsr[r.sr_id]:.3f} "
                f"(Sharpe={r.sharpe:.2f})"
            )
        blocks.append("")
    return "\n".join(blocks).strip() or "_no clusters yet_"


# --------------------------------------------------------------------------- #
# Novel combinations via Claude                                               #
# --------------------------------------------------------------------------- #

NOVEL_RUBRIC = (
    "Each combination MUST satisfy ALL of: "
    "(a) draw from at least 2 different mapped types "
    "(ema_crossover / mean_reversion / trend_following / pairs_trading); "
    "(b) cite at least one mechanistic reason for synergy "
    "(e.g. mean-reversion fades trend-following's entry-bar overshoot); "
    "(c) name the highest-correlation risk between constituents "
    "(what makes them fail together?); "
    "(d) sketch an implementation approach (Strategy subclass stub or "
    "composite signal aggregation)."
)


def _build_combo_prompt(rows: list[TrialRow], dsr: dict[int, float]) -> str:
    lines: list[str] = []
    sorted_rows = sorted(rows, key=lambda r: dsr[r.sr_id], reverse=True)[:15]
    for r in sorted_rows:
        lines.append(
            f"- SR-{r.sr_id:03d} {r.strategy_name} ({r.mapped_type}, "
            f"book={r.book_slug}): DSR={dsr[r.sr_id]:.3f}, "
            f"Sharpe={r.sharpe:.2f}, DD={r.max_dd:.2f}%, "
            f"WR={r.win_rate:.1f}%, trades={r.trades}"
        )
    listing = "\n".join(lines)
    return (
        "Below are the top backtested strategies extracted from 10 quant-finance "
        "books and tested on EURUSD M15 2020-2024. DSR (Deflated Sharpe Ratio) "
        "corrects raw Sharpe for the multiple-testing inflation across this sweep.\n\n"
        f"{listing}\n\n"
        f"Propose exactly 3 novel combinations worth testing. {NOVEL_RUBRIC}\n\n"
        "Format your answer as markdown with three '### Combination N:' headings, "
        "each followed by 'Constituents:', 'Synergy mechanism:', "
        "'Correlated failure mode:', and 'Implementation sketch:' bullets."
    )


def propose_novel_combinations(
    rows: list[TrialRow], dsr: dict[int, float], client=None,
) -> str:
    """Single LLM call to produce three novel combinations."""
    if not rows:
        return "_no strategies to combine — pipeline produced empty trial log_"
    if client is None:
        return (
            "_LLM client unavailable; skipping novel-combination synthesis. "
            "Re-run coordinator with `ANTHROPIC_API_KEY` set._"
        )
    prompt = _build_combo_prompt(rows, dsr)
    resp = client.messages.create(
        model=COMBO_MODEL,
        max_tokens=4000,
        system=(
            "You are a quantitative strategist. Be concrete and rigorous. "
            "If two constituents are statistically equivalent, say so."
        ),
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(
        b.text for b in resp.content if getattr(b, "type", None) == "text"
    ).strip()


# --------------------------------------------------------------------------- #
# MOC rendering                                                               #
# --------------------------------------------------------------------------- #

def render_moc(
    rows: list[TrialRow],
    dsr: dict[int, float],
    novel_combos_md: str,
) -> str:
    today = date.today().isoformat()
    n_validated = sum(1 for r in rows if r.guard == "PASS")
    cps = all_checkpoints()
    no_strategy_books = [
        cp.book_slug for cp in cps if cp.status == "complete_no_strategies"
    ]
    failed_books = [cp.book_slug for cp in cps if cp.status == "failed"]

    ranking = render_ranking_table(rows, dsr)
    clusters = render_clusters(cluster_by_type(rows), dsr)

    stats = [
        f"- Books processed: {len(cps)}",
        f"- Books with no extractable strategies: "
        f"{len(no_strategy_books)} ({', '.join(no_strategy_books) or '—'})",
        f"- Books that failed: "
        f"{len(failed_books)} ({', '.join(failed_books) or '—'})",
        f"- Total strategies tested: {len(rows)}",
        f"- Validated (Guard PASS): {n_validated}",
        f"- DSR pool size: {len(rows)} (used for multiple-testing correction)",
    ]

    return (
        f"# MOC — Strategy Research Pipeline Results\n\n"
        f"Generated: {today}\n\n"
        "## Pipeline Stats\n\n"
        + "\n".join(stats) + "\n\n"
        "## Ranking by DSR (multiple-testing-corrected Sharpe)\n\n"
        + ranking + "\n\n"
        "## Strategy Clusters (overlapping ideas across books)\n\n"
        + clusters + "\n\n"
        "## Novel Combinations Worth Testing\n\n"
        + novel_combos_md + "\n"
    )


def write_moc(content: str) -> Path:
    MOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    MOC_PATH.write_text(content, encoding="utf-8")
    return MOC_PATH


# --------------------------------------------------------------------------- #
# Entry point                                                                  #
# --------------------------------------------------------------------------- #

def run_coordinator(client=None) -> Path:
    """End-to-end coordinator pass. Returns the MOC path."""
    rows = load_trials()
    if not rows:
        log.warning("no trial rows — coordinator producing empty MOC")
        moc = render_moc([], {}, "_no strategies tested_")
        return write_moc(moc)

    # Refuse to run unless every spawned agent is in a terminal state.
    cps = all_checkpoints()
    pending = [
        cp.book_slug for cp in cps
        if cp.status not in {"complete", "complete_no_strategies", "failed"}
    ]
    if pending:
        raise RuntimeError(
            f"coordinator refusing to run: {len(pending)} agents not in "
            f"terminal state: {pending}"
        )

    dsr = compute_global_dsr(rows)
    n_back = _backfill_dsr(rows, dsr)
    log.info("back-filled DSR into %d ADR files", n_back)

    novel = propose_novel_combinations(rows, dsr, client=client)
    moc = render_moc(rows, dsr, novel)
    path = write_moc(moc)
    log.info("MOC written: %s", path)
    return path


__all__ = [
    "load_trials", "compute_global_dsr", "render_ranking_table",
    "cluster_by_type", "render_clusters", "render_moc", "write_moc",
    "propose_novel_combinations", "run_coordinator", "TrialRow",
]
