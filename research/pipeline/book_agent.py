"""Per-book asynchronous research agent (5 phases).

read -> extract -> spec -> backtest -> adr

Each phase is durable: state is checkpointed to disk before moving on so the
pipeline can be killed and resumed (e.g. after Claude rate-limit pauses)
without losing progress or duplicating LLM calls.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .adr_writer import append_memory_pointer, write_adr
from .backtest_runner import run_backtest_async
from .checkpoint_io import next_sr_id, save_checkpoint
from .models import (
    SPEC_DIR,
    AgentCheckpoint,
    BacktestResult,
    BookChunk,
    BookSpec,
    ExtractionResponse,
    MappedStrategy,
    StrategyCandidate,
)
from .strategy_mapper import map_strategy

log = logging.getLogger("book_agent")

CHUNK_TARGET_TOKENS = 80_000
CHARS_PER_TOKEN = 4
EXTRACT_MODEL = "claude-sonnet-4-6"
EXTRACT_MAX_TOKENS = 8000
RATE_LIMIT_BACKOFFS = (60, 120, 240)

SYSTEM_PROMPT = (
    "You are a quantitative finance research analyst. You read excerpts of "
    "trading books and extract every concrete trading strategy that has a "
    "stated hypothesis, specific entry conditions, specific exit conditions, "
    "and at least one numeric parameter. You return JSON only, no prose."
)


_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$", re.MULTILINE)


def _split_by_headings(text: str) -> list[tuple[list[str], str]]:
    """Split a markdown doc into (heading_path, body) pairs."""
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [(["(root)"], text)]
    sections: list[tuple[list[str], str]] = []
    if matches[0].start() > 0:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            sections.append((["(preamble)"], preamble))
    h2_path: str | None = None
    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end].strip()
        if not body:
            if level == 2:
                h2_path = title
            continue
        if level == 2:
            h2_path = title
            heading_path = [title]
        else:
            heading_path = [h2_path, title] if h2_path else [title]
        sections.append((heading_path, body))
    return sections


def _pack_into_chunks(sections: list[tuple[list[str], str]]) -> list[BookChunk]:
    """Greedily pack sections into <=CHUNK_TARGET_TOKENS chunks."""
    chunks: list[BookChunk] = []
    cur_path: list[str] = []
    cur_body_parts: list[str] = []
    cur_chars = 0
    target_chars = CHUNK_TARGET_TOKENS * CHARS_PER_TOKEN

    def _flush() -> None:
        nonlocal cur_body_parts, cur_chars, cur_path
        if not cur_body_parts:
            return
        body = "\n\n".join(cur_body_parts)
        chunks.append(BookChunk(
            heading_path=cur_path or ["(root)"],
            body=body,
            token_estimate=len(body) // CHARS_PER_TOKEN,
        ))
        cur_body_parts = []
        cur_chars = 0

    for path, body in sections:
        if len(body) > target_chars:
            _flush()
            paras = body.split("\n\n")
            buf: list[str] = []
            buf_chars = 0
            for para in paras:
                if buf_chars + len(para) > target_chars and buf:
                    chunks.append(BookChunk(
                        heading_path=path,
                        body="\n\n".join(buf),
                        token_estimate=buf_chars // CHARS_PER_TOKEN,
                    ))
                    buf = [para]
                    buf_chars = len(para)
                else:
                    buf.append(para)
                    buf_chars += len(para) + 2
            if buf:
                chunks.append(BookChunk(
                    heading_path=path,
                    body="\n\n".join(buf),
                    token_estimate=buf_chars // CHARS_PER_TOKEN,
                ))
            continue
        if cur_chars + len(body) > target_chars and cur_body_parts:
            _flush()
        if not cur_body_parts:
            cur_path = path
        prefix = " > ".join(path)
        cur_body_parts.append(f"### {prefix}\n\n{body}")
        cur_chars += len(body) + len(prefix) + 8
    _flush()
    return chunks


def read_and_chunk(book: BookSpec) -> list[BookChunk]:
    """Recursively read all .md files for a book and chunk them."""
    if not book.vault_dir.exists():
        return []
    pieces: list[str] = []
    for path in sorted(book.vault_dir.rglob("*.md")):
        if path.name == "_meta.md":
            continue
        try:
            pieces.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError as e:
            log.warning("could not read %s: %s", path, e)
    if not pieces:
        return []
    full = "\n\n".join(pieces)
    sections = _split_by_headings(full)
    return _pack_into_chunks(sections)


_FENCE = chr(96) * 3


def _build_extract_messages(
    book: BookSpec, chunk: BookChunk, chunk_idx: int, n_chunks: int,
) -> list[dict[str, Any]]:
    schema_hint = json.dumps({
        "strategies": [{
            "name": "str",
            "hypothesis": "str",
            "entry_rules": ["str"],
            "exit_rules": ["str"],
            "parameters": {"param_name": "default_value"},
            "timeframe_hint": "str_or_null",
            "instrument_hint": "str_or_null",
        }],
    })
    instruction = (
        f"You are extracting trading strategies from {book.title} "
        f"({book.author}, {book.year}). "
        f"This is chunk {chunk_idx + 1} of {n_chunks} (heading: {chunk.heading}).\n\n"
        "Extract EVERY concrete trading strategy in this chunk that has: "
        "a stated market hypothesis, specific entry conditions "
        "(indicator + threshold), specific exit conditions (SL/TP/time), "
        "and at least one numeric parameter.\n\n"
        f"Return JSON ONLY (no prose) matching this schema: {schema_hint}\n\n"
        "If the chunk contains no concrete strategy, return an empty "
        "strategies list."
    )
    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": chunk.body,
                    "cache_control": {"type": "ephemeral"},
                },
                {"type": "text", "text": instruction},
            ],
        }
    ]


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith(_FENCE):
        lines = text.splitlines()
        if lines[0].startswith(_FENCE):
            lines = lines[1:]
        if lines and lines[-1].startswith(_FENCE):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text


def _validate_response(raw: str) -> ExtractionResponse:
    cleaned = _strip_code_fence(raw)
    return ExtractionResponse.model_validate_json(cleaned)


async def _call_claude_with_retries(
    client, messages: list[dict[str, Any]], system: str,
):
    """Wrap a Claude API call with rate-limit-aware exponential backoff."""
    import anthropic
    last_err: Exception | None = None
    for attempt, sleep_for in enumerate([0, *RATE_LIMIT_BACKOFFS]):
        if sleep_for:
            log.warning("rate-limit backoff %ds (attempt %d)", sleep_for, attempt)
            await asyncio.sleep(sleep_for)
        try:
            return await asyncio.to_thread(
                client.messages.create,
                model=EXTRACT_MODEL,
                max_tokens=EXTRACT_MAX_TOKENS,
                system=system,
                messages=messages,
            )
        except anthropic.RateLimitError as e:
            last_err = e
            continue
        except anthropic.APIStatusError as e:
            if 500 <= e.status_code < 600:
                last_err = e
                continue
            raise
    assert last_err is not None
    raise last_err


async def extract_chunk(
    client, book: BookSpec, chunk: BookChunk, chunk_idx: int, n_chunks: int,
) -> list[StrategyCandidate]:
    """Call the LLM once for a chunk; retry on schema failures."""
    messages = _build_extract_messages(book, chunk, chunk_idx, n_chunks)
    resp = await _call_claude_with_retries(client, messages, SYSTEM_PROMPT)
    raw = "".join(
        b.text for b in resp.content if getattr(b, "type", None) == "text"
    )
    try:
        return _validate_response(raw).strategies
    except (ValidationError, ValueError, json.JSONDecodeError) as e:
        log.warning("first parse failed for %s chunk %d: %s",
                    book.slug, chunk_idx, e)
        fix_messages = list(messages) + [
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": (
                    "The previous response was not valid JSON. Re-emit ONLY "
                    "the JSON object with no prose, no markdown fences."
                ),
            },
        ]
        resp = await _call_claude_with_retries(client, fix_messages, SYSTEM_PROMPT)
        raw = "".join(
            b.text for b in resp.content
            if getattr(b, "type", None) == "text"
        )
        try:
            return _validate_response(raw).strategies
        except (ValidationError, ValueError, json.JSONDecodeError) as e2:
            log.error("fix retry failed for %s chunk %d: %s",
                      book.slug, chunk_idx, e2)
            return []


def _dedup_candidates(
    candidates: list[StrategyCandidate],
) -> list[StrategyCandidate]:
    """Dedup by lowercase name; keep candidate with most parameters."""
    by_name: dict[str, StrategyCandidate] = {}
    for c in candidates:
        key = c.name.strip().lower()
        if key not in by_name or len(c.parameters) > len(by_name[key].parameters):
            by_name[key] = c
    return list(by_name.values())


def write_spec(book: BookSpec, mapped: MappedStrategy) -> Path:
    """Write the spec YAML and stamp SR id + path on the MappedStrategy."""
    SPEC_DIR.mkdir(parents=True, exist_ok=True)
    sr_id = next_sr_id()
    mapped.sr_id = sr_id
    filename = f"SR-{sr_id:03d}-{book.slug}-{mapped.slug}.yaml"
    path = SPEC_DIR / filename
    payload = {
        "book_source": book.slug,
        "strategy_name": mapped.candidate.name,
        **mapped.yaml_params,
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    mapped.spec_path = path
    return path


async def run_book_agent(
    book: BookSpec,
    agent_id: int,
    cp: AgentCheckpoint,
    client,
    *,
    semaphore: asyncio.Semaphore,
    dry_run: bool = False,
) -> AgentCheckpoint:
    """Drive one book through the 5-phase pipeline.

    The checkpoint is mutated in place and saved after every phase.
    """
    cp.status = "in_progress"
    cp.error = None

    async with semaphore:
        # Phase 1: read & chunk
        if "read" not in cp.phases_complete:
            cp.phase = "read"
            log.info("[%s] phase=read", book.slug)
            chunks = read_and_chunk(book)
            cp.n_chunks = len(chunks)
            cp.mark_phase_done("read")
            save_checkpoint(cp)
        else:
            chunks = read_and_chunk(book)
            if len(chunks) != cp.n_chunks:
                cp.n_chunks = len(chunks)

        if not chunks:
            cp.status = "failed"
            cp.error = f"no_content: vault dir empty or unreadable ({book.vault_dir})"
            save_checkpoint(cp)
            return cp

        # Phase 2: extract
        if "extract" not in cp.phases_complete:
            cp.phase = "extract"
            log.info("[%s] phase=extract n_chunks=%d", book.slug, len(chunks))
            all_candidates: list[StrategyCandidate] = []
            for s in cp.strategies:
                try:
                    all_candidates.append(StrategyCandidate.model_validate(s))
                except ValidationError:
                    continue
            for i, chunk in enumerate(chunks):
                if i < cp.chunks_done:
                    continue
                if dry_run:
                    log.info("[%s] dry-run skip extract chunk %d", book.slug, i)
                    cp.chunks_done = i + 1
                    save_checkpoint(cp)
                    continue
                candidates = await extract_chunk(client, book, chunk, i, len(chunks))
                all_candidates.extend(candidates)
                cp.chunks_done = i + 1
                cp.strategies = [
                    c.model_dump() for c in _dedup_candidates(all_candidates)
                ]
                save_checkpoint(cp)
            cp.mark_phase_done("extract")
            save_checkpoint(cp)
        else:
            log.info("[%s] resume after extract", book.slug)

        candidates_now: list[StrategyCandidate] = []
        for s in cp.strategies:
            try:
                candidates_now.append(StrategyCandidate.model_validate(s))
            except ValidationError:
                continue
        candidates_now = _dedup_candidates(candidates_now)

        if not candidates_now:
            cp.status = "complete_no_strategies"
            save_checkpoint(cp)
            log.info("[%s] no strategies extracted; terminating", book.slug)
            return cp

        if dry_run:
            cp.phase = "spec"
            cp.mark_phase_done("spec")
            cp.status = "complete"
            save_checkpoint(cp)
            return cp

        # Phase 3: spec
        mapped_list: list[MappedStrategy] = []
        if "spec" not in cp.phases_complete:
            cp.phase = "spec"
            log.info("[%s] phase=spec n_candidates=%d", book.slug, len(candidates_now))
            for cand in candidates_now:
                mapped = map_strategy(cand)
                spec_path = write_spec(book, mapped)
                cp.spec_files.append(str(spec_path))
                mapped_list.append(mapped)
            cp.mark_phase_done("spec")
            save_checkpoint(cp)
        else:
            for cand, spec_path_str in zip(candidates_now, cp.spec_files):
                spec_path = Path(spec_path_str)
                mapped = map_strategy(cand)
                m = re.search(r"SR-(\d+)-", spec_path.name)
                if m:
                    mapped.sr_id = int(m.group(1))
                mapped.spec_path = spec_path
                mapped_list.append(mapped)

        # Phase 4: backtest
        results: list[BacktestResult] = []
        if "backtest" not in cp.phases_complete:
            cp.phase = "backtest"
            log.info("[%s] phase=backtest n_specs=%d", book.slug, len(mapped_list))
            done_sr_ids = {r.get("sr_id") for r in cp.backtest_results}
            for mapped in mapped_list:
                if mapped.sr_id in done_sr_ids:
                    continue
                t0 = time.time()
                result = await run_backtest_async(mapped, book.slug)
                log.info(
                    "[%s] SR-%03d sharpe=%.3f guard=%s (%.1fs)",
                    book.slug, mapped.sr_id, result.sharpe,
                    "PASS" if result.guard_pass else "FAIL", time.time() - t0,
                )
                cp.backtest_results.append({
                    "sr_id": result.sr_id,
                    "sharpe": result.sharpe,
                    "max_dd": result.max_drawdown_pct,
                    "win_rate": result.win_rate_pct,
                    "guard": result.guard_pass,
                    "trades": result.trades,
                    "error": result.error,
                })
                save_checkpoint(cp)
                results.append(result)
            cp.mark_phase_done("backtest")
            save_checkpoint(cp)
        else:
            for mapped in mapped_list:
                stored = next(
                    (r for r in cp.backtest_results
                     if r.get("sr_id") == mapped.sr_id), None,
                )
                if not stored:
                    continue
                results.append(BacktestResult(
                    sr_id=mapped.sr_id,
                    strategy_name=mapped.candidate.name,
                    book_slug=book.slug,
                    mapped_type=mapped.mapped_type,
                    sharpe=stored["sharpe"],
                    max_drawdown_pct=stored["max_dd"],
                    win_rate_pct=stored["win_rate"],
                    guard_pass=stored["guard"],
                    trades=stored["trades"],
                    bars=0,
                    error=stored.get("error"),
                ))

        # Phase 5: ADR
        if "adr" not in cp.phases_complete:
            cp.phase = "adr"
            log.info("[%s] phase=adr n_results=%d", book.slug, len(results))
            for mapped, result in zip(mapped_list, results):
                adr_path = write_adr(mapped, result, book, dsr=None)
                append_memory_pointer(mapped.sr_id, mapped, result)
                if str(adr_path) not in cp.adrs_written:
                    cp.adrs_written.append(str(adr_path))
            cp.mark_phase_done("adr")

        cp.status = "complete"
        save_checkpoint(cp)
        log.info(
            "[%s] complete (%d strategies, %d ADRs)",
            book.slug, len(mapped_list), len(cp.adrs_written),
        )
        return cp


__all__ = [
    "run_book_agent", "read_and_chunk", "_split_by_headings",
    "_pack_into_chunks", "_dedup_candidates", "write_spec", "extract_chunk",
]
