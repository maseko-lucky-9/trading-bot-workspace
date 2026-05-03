"""Regression detection for the supervisor loop.

Two regression signatures:
  1. circuit_breaker_false_positive — bridge transient returns the 10k fallback
     dict after real equity was established, producing a false ~90% drawdown
     halt that latches permanently.
  2. visited_set_cycling — autoresearch loop is stuck: same (param, value) pair
     appears ≥3 times in the last 20 results rows, OR Sharpe delta <0.01 for
     ≥8 consecutive iterations.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger(__name__)

_BOT_ROOT = Path(__file__).resolve().parents[1]
_CHECKPOINT_STATE_JSON = _BOT_ROOT / "checkpoints" / "state.json"
_BRIDGE_BASE = "http://192.168.64.1:8080"

# Thresholds from the plan
_CB_FALSE_POSITIVE_PEAK_EQUITY_FLOOR = 10_500.0
_CB_FALSE_POSITIVE_EQUITY_LOW = 9_800.0
_CB_FALSE_POSITIVE_EQUITY_HIGH = 10_100.0

_CYCLING_PARAM_REPEAT_N = 3       # same (param, value) ≥ N times in last 20 rows
_CYCLING_WINDOW = 20
_CYCLING_SHARPE_DELTA_THRESHOLD = 0.01
_CYCLING_SHARPE_CONSECUTIVE = 8


@dataclass
class Regression:
    regression_type: str           # 'circuit_breaker_false_positive' | 'visited_set_cycling'
    scope: str                     # 'global' | 'combo'
    combo_slug: Optional[str]
    description: str
    evidence: dict = field(default_factory=dict)


def _load_bot_state(state_json: Path = _CHECKPOINT_STATE_JSON) -> Optional[dict]:
    if not state_json.exists():
        return None
    try:
        return json.loads(state_json.read_text())
    except Exception as exc:
        log.warning("could not load bot state: %s", exc)
        return None


def _get_bridge_equity(bridge_base: str = _BRIDGE_BASE) -> Optional[float]:
    try:
        resp = httpx.get(f"{bridge_base}/account", timeout=5.0)
        resp.raise_for_status()
        return float(resp.json().get("equity", 0.0))
    except Exception as exc:
        log.debug("bridge account fetch failed: %s", exc)
        return None


def _detect_cb_false_positive(
    state_json: Path = _CHECKPOINT_STATE_JSON,
    bridge_base: str = _BRIDGE_BASE,
) -> Optional[Regression]:
    """Detect circuit-breaker false positive per plan §Regression Signatures."""
    state = _load_bot_state(state_json)
    if state is None:
        return None

    peak_equity = float(state.get("peak_equity", 0.0))
    cooling_off_until = float(state.get("cooling_off_until", 0.0))
    now_epoch = datetime.now(timezone.utc).timestamp()

    if peak_equity <= _CB_FALSE_POSITIVE_PEAK_EQUITY_FLOOR:
        return None
    if cooling_off_until <= now_epoch:
        return None

    bridge_equity = _get_bridge_equity(bridge_base)
    if bridge_equity is None:
        return None

    if not (_CB_FALSE_POSITIVE_EQUITY_LOW <= bridge_equity <= _CB_FALSE_POSITIVE_EQUITY_HIGH):
        return None

    evidence = {
        "peak_equity": peak_equity,
        "bridge_equity": bridge_equity,
        "cooling_off_until": cooling_off_until,
        "now_epoch": now_epoch,
    }
    return Regression(
        regression_type="circuit_breaker_false_positive",
        scope="global",
        combo_slug=None,
        description=(
            f"CB halted with peak_equity={peak_equity:.2f} but bridge equity "
            f"={bridge_equity:.2f} (near 10k fallback). Likely transient bridge "
            f"response latched a false drawdown halt."
        ),
        evidence=evidence,
    )


def _parse_results_rows(results_path: Path) -> list[dict]:
    """Parse results.tsv into a list of dicts (header-keyed)."""
    if not results_path.exists():
        return []
    lines = results_path.read_text().splitlines()
    if not lines:
        return []
    header = lines[0].strip().split("\t")
    rows = []
    for line in lines[1:]:
        if not line.strip():
            continue
        cols = line.split("\t")
        row = dict(zip(header, cols))
        rows.append(row)
    return rows


def _detect_visited_set_cycling(
    combo_slug: str,
    results_path: Path,
) -> Optional[Regression]:
    """Detect visited-set cycling for one combo."""
    rows = _parse_results_rows(results_path)
    if not rows:
        return None

    window = rows[-_CYCLING_WINDOW:]

    # Check 1: same (param, value) pair ≥ N times
    pair_counts: dict[tuple, int] = {}
    for row in window:
        param = row.get("param", "")
        new_val = row.get("new_val", "")
        if param and new_val:
            key = (param, new_val)
            pair_counts[key] = pair_counts.get(key, 0) + 1

    repeat_pairs = {k: v for k, v in pair_counts.items() if v >= _CYCLING_PARAM_REPEAT_N}
    if repeat_pairs:
        return Regression(
            regression_type="visited_set_cycling",
            scope="combo",
            combo_slug=combo_slug,
            description=(
                f"Visited-set cycling: repeated (param, value) pairs in last "
                f"{_CYCLING_WINDOW} rows: {list(repeat_pairs)}"
            ),
            evidence={"repeat_pairs": {str(k): v for k, v in repeat_pairs.items()},
                      "window_size": len(window)},
        )

    # Check 2: Sharpe delta < 0.01 for ≥ N consecutive iterations
    sharpes: list[float] = []
    for row in rows:
        try:
            sharpes.append(float(row.get("sharpe", "nan")))
        except ValueError:
            pass

    if len(sharpes) >= _CYCLING_SHARPE_CONSECUTIVE:
        consecutive_flat = 0
        for i in range(len(sharpes) - 1, 0, -1):
            delta = abs(sharpes[i] - sharpes[i - 1])
            if delta < _CYCLING_SHARPE_DELTA_THRESHOLD:
                consecutive_flat += 1
                if consecutive_flat >= _CYCLING_SHARPE_CONSECUTIVE:
                    return Regression(
                        regression_type="visited_set_cycling",
                        scope="combo",
                        combo_slug=combo_slug,
                        description=(
                            f"Sharpe flat for {consecutive_flat} consecutive iterations "
                            f"(delta < {_CYCLING_SHARPE_DELTA_THRESHOLD}) in {combo_slug}"
                        ),
                        evidence={
                            "consecutive_flat": consecutive_flat,
                            "recent_sharpes": sharpes[-_CYCLING_SHARPE_CONSECUTIVE:],
                        },
                    )
            else:
                break

    return None


class RegressionDetector:
    """Detects both regression types across all combos."""

    def __init__(
        self,
        combos_dir: Optional[Path] = None,
        state_json: Path = _CHECKPOINT_STATE_JSON,
        bridge_base: str = _BRIDGE_BASE,
    ) -> None:
        if combos_dir is None:
            combos_dir = Path(__file__).resolve().parent / "combos"
        self.combos_dir = combos_dir
        self.state_json = state_json
        self.bridge_base = bridge_base

    def scan(self) -> list[Regression]:
        regressions: list[Regression] = []

        cb = _detect_cb_false_positive(self.state_json, self.bridge_base)
        if cb is not None:
            log.warning("regression detected: %s", cb.regression_type)
            regressions.append(cb)

        for combo_dir in sorted(self.combos_dir.iterdir()):
            if not combo_dir.is_dir():
                continue
            results_path = combo_dir / "results.tsv"
            cycling = _detect_visited_set_cycling(combo_dir.name, results_path)
            if cycling is not None:
                log.warning("regression detected: %s in %s", cycling.regression_type, combo_dir.name)
                regressions.append(cycling)

        return regressions


# ---------------------------------------------------------------------------
# CLI replay helper (referenced in issue body reproduction section)
# ---------------------------------------------------------------------------

def _cli_main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="bot.supervisor.regression_detector")
    parser.add_argument("--replay", metavar="ITERATION_ID",
                        help="Replay regression detection for a recorded iteration.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)

    if args.replay:
        # For replay: re-run detector and report results to stdout
        detector = RegressionDetector()
        regressions = detector.scan()
        print(f"Detected {len(regressions)} regression(s) at replay of iteration {args.replay}:")
        for r in regressions:
            print(f"  - {r.regression_type} scope={r.scope} combo={r.combo_slug}")
            print(f"    {r.description}")
        return 0

    detector = RegressionDetector()
    regressions = detector.scan()
    if not regressions:
        print("No regressions detected.")
        return 0
    for r in regressions:
        print(f"REGRESSION: {r.regression_type} scope={r.scope} combo={r.combo_slug}")
        print(f"  {r.description}")
    return 1


if __name__ == "__main__":
    sys.exit(_cli_main())
