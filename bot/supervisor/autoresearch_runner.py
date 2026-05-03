"""Drives AutoresearchLoop for each of the 8 symbol/timeframe/strategy combos.

Each combo gets its own isolated directory under bot/supervisor/combos/<slug>/
so visited sets, results.tsv, and params.yaml never interfere across combos.

On first run, params.yaml is copied from the base template and the `strategy:`
field is overwritten to match the combo.
"""
from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

log = logging.getLogger(__name__)

_BOT_ROOT = Path(__file__).resolve().parents[1]
_BASE_PARAMS = _BOT_ROOT / "autoresearch" / "params.yaml"
_CONFIG_PATH = _BOT_ROOT / "config.yaml"
_COMBOS_DIR = Path(__file__).resolve().parent / "combos"

_SHARPE_RE = re.compile(r"^SHARPE\s+(-?[0-9.]+)", re.MULTILINE)
_GUARD_RE = re.compile(r"^GUARD\s+(PASS|FAIL)", re.MULTILINE)
_WR_RE = re.compile(r"win_rate=([0-9.]+)%")
_DD_RE = re.compile(r"drawdown=([0-9.]+)%")


@dataclass
class Combo:
    symbol: str
    timeframe: str
    strategy: str

    @property
    def slug(self) -> str:
        return f"{self.symbol}_{self.timeframe}_{self.strategy}"

    @property
    def combo_dir(self) -> Path:
        return _COMBOS_DIR / self.slug


ALL_COMBOS: list[Combo] = [
    Combo("EURUSD", "M15", "mean_reversion"),
    Combo("EURUSD", "M15", "ema_crossover"),
    Combo("EURUSD", "H1",  "mean_reversion"),
    Combo("EURUSD", "H1",  "ema_crossover"),
    Combo("GBPUSD", "M15", "mean_reversion"),
    Combo("GBPUSD", "M15", "ema_crossover"),
    Combo("GBPUSD", "H1",  "mean_reversion"),
    Combo("GBPUSD", "H1",  "ema_crossover"),
]


@dataclass
class ComboResult:
    combo: Combo
    sharpe: Optional[float]
    dsr: Optional[float]
    guard: Optional[str]
    max_dd: Optional[float]
    win_rate: Optional[float]
    error: Optional[str] = None

    @property
    def promotable(self) -> bool:
        return (
            self.dsr is not None
            and self.dsr > 0.0
            and self.guard == "PASS"
            and self.error is None
        )


def _init_combo_dir(combo: Combo) -> Path:
    """Create per-combo dir and initialise params.yaml if absent."""
    combo_dir = combo.combo_dir
    combo_dir.mkdir(parents=True, exist_ok=True)

    params_path = combo_dir / "params.yaml"
    if not params_path.exists():
        shutil.copy2(_BASE_PARAMS, params_path)
        with params_path.open() as fh:
            params = yaml.safe_load(fh) or {}
        params["strategy"] = combo.strategy
        with params_path.open("w") as fh:
            yaml.safe_dump(params, fh, default_flow_style=False, sort_keys=True)
        log.info("initialised combo dir: %s", combo_dir)

    return combo_dir


def _parse_results_tail(results_path: Path) -> tuple[Optional[float], Optional[str], Optional[float], Optional[float]]:
    """Extract sharpe, guard, max_dd, win_rate from the last row of results.tsv."""
    if not results_path.exists():
        return None, None, None, None
    lines = results_path.read_text().splitlines()
    data_lines = [l for l in lines if l.strip() and not l.startswith("iteration")]
    if not data_lines:
        return None, None, None, None
    last = data_lines[-1]
    cols = last.split("\t")
    # Header: iteration param old_val new_val sharpe dsr max_dd win_rate decision strategy timestamp
    try:
        sharpe = float(cols[4]) if len(cols) > 4 else None
    except (ValueError, IndexError):
        sharpe = None
    try:
        max_dd = float(cols[6]) if len(cols) > 6 else None
    except (ValueError, IndexError):
        max_dd = None
    try:
        win_rate = float(cols[7]) if len(cols) > 7 else None
    except (ValueError, IndexError):
        win_rate = None
    # Guard isn't in results.tsv directly; we need to check phase_guard separately.
    # We store guard in the results TSV as the decision column for now.
    return sharpe, None, max_dd, win_rate


def run_combo(
    combo: Combo,
    *,
    max_iterations: int = 5,
    timeout_seconds: int = 1800,
    dry_run: bool = False,
) -> ComboResult:
    """Run AutoresearchLoop for one combo and return a ComboResult."""
    combo_dir = _init_combo_dir(combo)
    params_path = combo_dir / "params.yaml"
    results_path = combo_dir / "results.tsv"

    log.info("starting autoresearch: %s (max_iter=%d)", combo.slug, max_iterations)

    if dry_run:
        log.info("[dry-run] skipping autoresearch loop for %s", combo.slug)
        return ComboResult(
            combo=combo,
            sharpe=None, dsr=None, guard=None, max_dd=None, win_rate=None,
            error="dry-run",
        )

    try:
        import signal as _signal
        from bot.autoresearch.loop import AutoresearchLoop

        loop = AutoresearchLoop(
            config_path=_CONFIG_PATH,
            params_path=params_path,
            results_path=results_path,
        )

        result: dict = {}

        def _timeout_handler(signum, frame):
            raise TimeoutError(f"autoresearch timeout after {timeout_seconds}s")

        old_handler = _signal.signal(_signal.SIGALRM, _timeout_handler)
        _signal.alarm(timeout_seconds)
        try:
            result = loop.run(max_iterations=max_iterations)
        finally:
            _signal.alarm(0)
            _signal.signal(_signal.SIGALRM, old_handler)

        sharpe = result.get("final_sharpe")
        dsr = result.get("final_dsr")
        # guard is not returned directly by run(); derive from results.tsv last row
        _, _, max_dd, win_rate = _parse_results_tail(results_path)

        # Determine guard from results.tsv decision column — "keep" rows imply PASS
        guard = _infer_guard_from_results(results_path)

        log.info(
            "autoresearch done: %s sharpe=%.3f dsr=%.3f guard=%s",
            combo.slug, sharpe or 0, dsr or 0, guard,
        )
        return ComboResult(
            combo=combo, sharpe=sharpe, dsr=dsr, guard=guard,
            max_dd=max_dd, win_rate=win_rate,
        )

    except TimeoutError as exc:
        log.error("autoresearch timeout: %s", combo.slug)
        return ComboResult(
            combo=combo, sharpe=None, dsr=None, guard=None,
            max_dd=None, win_rate=None, error=str(exc),
        )
    except Exception as exc:
        log.exception("autoresearch error: %s", combo.slug)
        return ComboResult(
            combo=combo, sharpe=None, dsr=None, guard=None,
            max_dd=None, win_rate=None, error=str(exc),
        )


def _infer_guard_from_results(results_path: Path) -> Optional[str]:
    """Infer guard status from the most recent 'keep' or 'rollback' decision row."""
    if not results_path.exists():
        return None
    lines = results_path.read_text().splitlines()
    data_lines = [l for l in lines if l.strip() and not l.startswith("iteration")]
    for line in reversed(data_lines):
        cols = line.split("\t")
        if len(cols) > 8:
            decision = cols[8].strip()
            if decision == "keep":
                return "PASS"
            if decision in ("rollback", "no_improvement"):
                return "FAIL"
    return None


def bootstrap_combo_dirs() -> None:
    """Create all 8 combo directories and initialise params.yaml (idempotent)."""
    for combo in ALL_COMBOS:
        _init_combo_dir(combo)
