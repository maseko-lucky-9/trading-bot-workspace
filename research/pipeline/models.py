"""Shared dataclasses and Pydantic schemas for the book-research pipeline."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


REPO_ROOT = Path(__file__).resolve().parents[2]
RESEARCH_ROOT = REPO_ROOT / "research"
CHECKPOINT_DIR = RESEARCH_ROOT / "checkpoints"
SPEC_DIR = RESEARCH_ROOT / "strategy_specs"
TRIAL_LOG_PATH = RESEARCH_ROOT / "trial_log.tsv"
SR_COUNTER_PATH = CHECKPOINT_DIR / "_sr_counter.json"

VAULT_ROOT = Path("/Users/ltmas/Documents/Obsidian Vault")
INGESTED_BOOKS_DIR = VAULT_ROOT / "wiki" / "ingested-books"
ADR_OUTPUT_DIR = VAULT_ROOT / "Memory" / "wiki" / "projects" / "strategy-research"
MEMORY_INDEX_PATH = VAULT_ROOT / "Memory" / "MEMORY.md"
MOC_PATH = VAULT_ROOT / "MOCs" / "MOC - Strategy Research Pipeline.md"

EXISTING_STRATEGY_TYPES = (
    "ema_crossover",
    "mean_reversion",
    "trend_following",
    "pairs_trading",
)


# --------------------------------------------------------------------------- #
# Book inputs                                                                 #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class BookSpec:
    slug: str
    title: str
    author: str
    year: int

    @property
    def vault_dir(self) -> Path:
        return INGESTED_BOOKS_DIR / f"unknown_{self.slug}"


# Ten books selected for analysis (psychology + intro books excluded).
BOOK_LIST: tuple[BookSpec, ...] = (
    BookSpec("advances-in-financial-machine-learning",
             "Advances in Financial Machine Learning", "López de Prado", 2018),
    BookSpec("machine-learning-for-algorithmic-trading",
             "Machine Learning for Algorithmic Trading", "Stefan Jansen", 2020),
    BookSpec("quantitative-trading-ernest-p-chan",
             "Quantitative Trading", "Ernest P. Chan", 2021),
    BookSpec("tsam", "The Systematic Trader (TSAM)", "Robert Carver", 2005),
    BookSpec("technical-analysis-of-the-financial",
             "Technical Analysis of the Financial Markets", "John Murphy", 1985),
    BookSpec("the-art-and-science-of",
             "The Art and Science of Technical Analysis", "Adam Grimes", 2012),
    BookSpec("day-trading-and-swing-trading",
             "Day Trading and Swing Trading the Currency Market", "Kathy Lien", 2009),
    BookSpec("naked-forex-walter-petrs",
             "Naked Forex", "Walter Peters", 2012),
    BookSpec("forex-trading-jim-brown",
             "Forex Trading", "Jim Brown", 2016),
    BookSpec("neural-networks-for-algo-trading",
             "Neural Networks for Algorithmic Trading", "Unknown", 2000),
)


@dataclass
class BookChunk:
    """A heading-aligned slice of a book, sized to fit Claude's context."""
    heading_path: list[str]
    body: str
    token_estimate: int

    @property
    def heading(self) -> str:
        return " > ".join(self.heading_path) if self.heading_path else "(root)"


# --------------------------------------------------------------------------- #
# LLM extraction schema (Pydantic — validated on every response)              #
# --------------------------------------------------------------------------- #

class StrategyCandidate(BaseModel):
    """Single trading strategy extracted from a book chunk by the LLM."""
    name: str
    hypothesis: str
    entry_rules: list[str]
    exit_rules: list[str]
    parameters: dict[str, Any] = Field(default_factory=dict)
    timeframe_hint: str | None = None
    instrument_hint: str | None = None


class ExtractionResponse(BaseModel):
    """Top-level LLM response shape."""
    strategies: list[StrategyCandidate]


# --------------------------------------------------------------------------- #
# Post-mapping spec                                                           #
# --------------------------------------------------------------------------- #

@dataclass
class MappedStrategy:
    """A StrategyCandidate after strategy_mapper has assigned a known type."""
    candidate: StrategyCandidate
    mapped_type: str            # one of EXISTING_STRATEGY_TYPES
    yaml_params: dict[str, Any] # written verbatim to the spec YAML
    sr_id: int = 0              # assigned by SR counter at write time
    spec_path: Path | None = None

    @property
    def slug(self) -> str:
        safe = "".join(
            c if c.isalnum() else "-" for c in self.candidate.name.lower()
        ).strip("-")
        # collapse repeated dashes
        while "--" in safe:
            safe = safe.replace("--", "-")
        return safe[:60] or "strategy"


# --------------------------------------------------------------------------- #
# Backtest result                                                             #
# --------------------------------------------------------------------------- #

@dataclass
class BacktestResult:
    sr_id: int
    strategy_name: str
    book_slug: str
    mapped_type: str
    sharpe: float
    max_drawdown_pct: float
    win_rate_pct: float
    guard_pass: bool
    trades: int
    bars: int
    error: str | None = None

    def is_validated(self) -> bool:
        return self.error is None and self.guard_pass


# --------------------------------------------------------------------------- #
# Per-agent checkpoint                                                        #
# --------------------------------------------------------------------------- #

VALID_STATUSES = {
    "pending",
    "in_progress",
    "complete",
    "complete_no_strategies",
    "failed",
}

VALID_PHASES = ("read", "extract", "spec", "backtest", "adr")


@dataclass
class AgentCheckpoint:
    book_slug: str
    agent_id: int
    status: str = "pending"
    phase: str = "read"
    phases_complete: list[str] = field(default_factory=list)
    n_chunks: int = 0
    chunks_done: int = 0
    strategies: list[dict[str, Any]] = field(default_factory=list)
    spec_files: list[str] = field(default_factory=list)
    backtest_results: list[dict[str, Any]] = field(default_factory=list)
    adrs_written: list[str] = field(default_factory=list)
    last_updated: str = ""
    error: str | None = None

    def touch(self) -> None:
        self.last_updated = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentCheckpoint":
        return cls(**d)

    def mark_phase_done(self, phase: str) -> None:
        if phase not in self.phases_complete:
            self.phases_complete.append(phase)


__all__ = [
    "REPO_ROOT", "RESEARCH_ROOT", "CHECKPOINT_DIR", "SPEC_DIR", "TRIAL_LOG_PATH",
    "SR_COUNTER_PATH", "VAULT_ROOT", "INGESTED_BOOKS_DIR", "ADR_OUTPUT_DIR",
    "MEMORY_INDEX_PATH", "MOC_PATH", "EXISTING_STRATEGY_TYPES",
    "BookSpec", "BOOK_LIST", "BookChunk",
    "StrategyCandidate", "ExtractionResponse", "MappedStrategy",
    "BacktestResult", "AgentCheckpoint",
    "VALID_STATUSES", "VALID_PHASES",
]
