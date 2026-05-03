"""Write ADR-style strategy-research notes into the Obsidian vault.

Each note lives at::

    $V/Memory/wiki/projects/strategy-research/SR-NNN-<slug>.md

DSR is left as a placeholder during the per-agent run; the coordinator does a
second pass to back-fill DSR + Confidence based on the **global** trial pool.
"""
from __future__ import annotations

import fcntl
from datetime import date, datetime, timezone
from pathlib import Path

from .models import (
    ADR_OUTPUT_DIR,
    BacktestResult,
    MappedStrategy,
    MEMORY_INDEX_PATH,
    BookSpec,
)


DSR_PENDING = "_pending coordinator pass_"
CONFIDENCE_PENDING = (
    "_Filled in by coordinator_ — DSR > 0.5 → high | 0.0–0.5 → moderate | "
    "< 0.0 → noise-level. DSR is computed against the **global** trial pool."
)


def _confidence_label(dsr: float | None) -> str:
    if dsr is None:
        return CONFIDENCE_PENDING
    if dsr > 0.5:
        bucket = "**high** confidence"
    elif dsr > 0.0:
        bucket = "**moderate** confidence"
    else:
        bucket = "**noise-level** confidence"
    return f"DSR = {dsr:.4f} → {bucket}."


def _signal_spec_table(yaml_params: dict) -> str:
    lines = ["| Parameter | Value |", "|-----------|-------|"]
    for k, v in yaml_params.items():
        lines.append(f"| `{k}` | {v} |")
    return "\n".join(lines)


def _adr_path(sr_id: int, mapped: MappedStrategy) -> Path:
    return ADR_OUTPUT_DIR / f"SR-{sr_id:03d}-{mapped.slug}.md"


def render_adr(
    mapped: MappedStrategy,
    result: BacktestResult,
    book: BookSpec,
    dsr: float | None = None,
) -> str:
    """Return the rendered ADR markdown."""
    today = date.today().isoformat()
    sr_label = f"SR-{result.sr_id:03d}"
    status = "Validated" if result.is_validated() else "Failed"
    dsr_cell = f"{dsr:.4f}" if dsr is not None else DSR_PENDING

    book_wikilink = f"[[unknown_{book.slug}]]"

    if result.error:
        result_block = (
            f"Backtest error: `{result.error}`. Sharpe is set to 0 by convention."
        )
    else:
        result_block = (
            f"| Metric | Value |\n"
            f"|--------|-------|\n"
            f"| Sharpe (CV mean) | {result.sharpe:.4f} |\n"
            f"| DSR | {dsr_cell} |\n"
            f"| Max Drawdown | {result.max_drawdown_pct:.2f}% |\n"
            f"| Win Rate | {result.win_rate_pct:.2f}% |\n"
            f"| Trades | {result.trades} |\n"
            f"| Bars | {result.bars} |\n"
            f"| Guard | {'PASS' if result.guard_pass else 'FAIL'} |"
        )

    confidence = _confidence_label(dsr)

    body = f"""---
name: {sr_label} — {mapped.candidate.name}
description: Backtest results for {mapped.candidate.name} from {book.title}
type: project
tags: [memory, project, strategy-research, backtest]
source: book-research-pipeline
updated: "{today}"
---

# {sr_label} — {mapped.candidate.name}

**Date:** {today}
**Status:** {status}
**Source Book:** {book.title} ({book.author}, {book.year})
**Mapped Type:** `{mapped.mapped_type}`
**Timeframe:** EURUSD M15 | 2020-2024

## Hypothesis
{mapped.candidate.hypothesis}

## Signal Specification
{_signal_spec_table(mapped.yaml_params)}

### Entry Rules (extracted)
{chr(10).join(f"- {r}" for r in mapped.candidate.entry_rules) or "- (none extracted)"}

### Exit Rules (extracted)
{chr(10).join(f"- {r}" for r in mapped.candidate.exit_rules) or "- (none extracted)"}

## Backtest Results
{result_block}

## Confidence Assessment
{confidence}

## Consequences
### Positive
- Direct mapping of a {book.author} strategy onto an existing backtestable type;
  any edge here is shippable as a parameter overlay (`{mapped.spec_path.name if mapped.spec_path else "spec.yaml"}`).
### Negative
- Mapping is lossy — the original strategy may rely on signals or context not
  captured by the four existing types. DSR is the gating metric, not raw Sharpe,
  to penalise multiple-testing inflation across the 10-book sweep.

## Related
- {book_wikilink}
- [[MOC - Strategy Research Pipeline]]
"""
    return body


def write_adr(
    mapped: MappedStrategy,
    result: BacktestResult,
    book: BookSpec,
    dsr: float | None = None,
) -> Path:
    """Render and write the ADR. Returns the path written."""
    ADR_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = _adr_path(result.sr_id, mapped)
    path.write_text(render_adr(mapped, result, book, dsr=dsr), encoding="utf-8")
    return path


def append_memory_pointer(
    sr_id: int, mapped: MappedStrategy, result: BacktestResult,
) -> None:
    """Append a single-line pointer to the vault's MEMORY.md auto-load index."""
    if not MEMORY_INDEX_PATH.exists():
        return
    summary = (
        f"Sharpe={result.sharpe:.2f}, DD={result.max_drawdown_pct:.1f}%, "
        f"trades={result.trades}, "
        f"{'PASS' if result.guard_pass else 'FAIL'}"
    )
    rel = (
        f"wiki/projects/strategy-research/SR-{sr_id:03d}-{mapped.slug}.md"
    )
    line = f"- [SR-{sr_id:03d} {mapped.candidate.name}]({rel}) — {summary}\n"

    with MEMORY_INDEX_PATH.open("a") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            fh.write(line)
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def update_dsr_in_adr(path: Path, dsr: float) -> None:
    """Overwrite the DSR placeholder + confidence line in an existing ADR.

    Used by the coordinator's back-fill pass.
    """
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    text = text.replace(DSR_PENDING, f"{dsr:.4f}")
    text = text.replace(CONFIDENCE_PENDING, _confidence_label(dsr))
    path.write_text(text, encoding="utf-8")


__all__ = [
    "render_adr", "write_adr", "append_memory_pointer", "update_dsr_in_adr",
    "_adr_path",
]
