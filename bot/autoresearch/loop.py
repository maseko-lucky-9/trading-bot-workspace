"""
AutoresearchLoop (US-010).

Eight-phase coordinate-descent search over the params.yaml overlay.
Each iteration tweaks one parameter, re-runs the backtest CLI, parses
``SHARPE`` and the guard exit code, and either keeps or rolls back.
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml


_BOT_ROOT = Path(__file__).resolve().parents[1]
_ENGINE = _BOT_ROOT / "backtest" / "engine.py"

# Round-robin parameters and their step sizes
_PARAMS = [
    ("ema_fast", 1.0, 3.0, 30.0),       # name, step, min, max
    ("ema_slow", 1.0, 5.0, 200.0),
    ("atr_multiplier", 0.25, 0.5, 5.0),
    ("rsi_period", 1.0, 5.0, 50.0),
    ("bb_std", 0.25, 1.0, 4.0),
]

_SHARPE_RE = re.compile(r"^SHARPE\s+(-?[0-9.]+)", re.MULTILINE)
_GUARD_RE  = re.compile(r"^GUARD\s+(PASS|FAIL)", re.MULTILINE)
_WR_RE     = re.compile(r"win_rate=([0-9.]+)%")
_DD_RE     = re.compile(r"drawdown=([0-9.]+)%")


class AutoresearchLoop:
    def __init__(
        self,
        config_path: Path | None = None,
        params_path: Path | None = None,
        results_path: Path | None = None,
    ) -> None:
        self.config_path = Path(config_path) if config_path else _BOT_ROOT / "config.yaml"
        self.params_path = (
            Path(params_path) if params_path else _BOT_ROOT / "autoresearch" / "params.yaml"
        )
        self.results_path = (
            Path(results_path)
            if results_path
            else _BOT_ROOT / "autoresearch" / "results.tsv"
        )
        self.results_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_results_header()
        self._param_cursor = 0
        self._direction = 1
        self._visited: set[tuple] = set()  # (param, value) pairs tried

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #

    def _ensure_results_header(self) -> None:
        if not self.results_path.exists() or self.results_path.stat().st_size == 0:
            self.results_path.write_text(
                "iteration\tparam\told_val\tnew_val\tsharpe\tmax_dd\twin_rate\tdecision\ttimestamp\n"
            )

    def _load_params(self) -> dict:
        if not self.params_path.exists():
            return {"strategy": "ema_crossover", "ema_fast": 9, "ema_slow": 21}
        with self.params_path.open() as f:
            return yaml.safe_load(f) or {}

    def _save_params(self, params: dict) -> None:
        with self.params_path.open("w") as f:
            yaml.safe_dump(params, f, sort_keys=False)

    def _run_engine(self, *flags: str) -> tuple[int, str, str]:
        cmd = [sys.executable, str(_ENGINE), "--params", str(self.params_path), "--bars", "2000", *flags]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, cwd=str(_BOT_ROOT), timeout=120
        )
        return proc.returncode, proc.stdout, proc.stderr

    def _parse_sharpe(self, stdout: str) -> float:
        m = _SHARPE_RE.search(stdout)
        return float(m.group(1)) if m else float("-inf")

    def _parse_guard(self, stdout: str) -> bool:
        m = _GUARD_RE.search(stdout)
        return bool(m and m.group(1) == "PASS")

    # ------------------------------------------------------------------ #
    # Phases                                                             #
    # ------------------------------------------------------------------ #

    def phase_review(self) -> dict:
        return self._load_params()

    def phase_ideate(self, current: dict) -> dict:
        # Try each param in both directions; skip visited (param, value) pairs
        for offset in range(len(_PARAMS)):
            name, step, lo, hi = _PARAMS[(self._param_cursor + offset) % len(_PARAMS)]
            old = float(current.get(name, 0))
            for direction in (1, -1):
                candidate = round(max(lo, min(hi, old + direction * step)), 4)
                if candidate != old and (name, candidate) not in self._visited:
                    self._param_cursor = (self._param_cursor + offset + 1) % len(_PARAMS)
                    return {"param": name, "old": old, "new": candidate}
        # All nearby values exhausted — take larger steps on ema params
        for name, step, lo, hi in _PARAMS:
            old = float(current.get(name, 0))
            for mult in (2, 3, 5):
                for direction in (1, -1):
                    candidate = round(max(lo, min(hi, old + direction * step * mult)), 4)
                    if candidate != old and (name, candidate) not in self._visited:
                        return {"param": name, "old": old, "new": candidate}
        # Hard fallback — reset visited and try again
        self._visited.clear()
        name, step, lo, hi = _PARAMS[0]
        old = float(current.get(name, 0))
        return {"param": name, "old": old, "new": max(lo, min(hi, old + step))}

    def phase_modify(self, params: dict, proposal: dict) -> dict:
        new_params = dict(params)
        new_params[proposal["param"]] = proposal["new"]
        self._save_params(new_params)
        return new_params

    def phase_commit(self, proposal: dict) -> dict:
        return {"committed_at": datetime.now(tz=timezone.utc).isoformat(), **proposal}

    def phase_verify(self) -> float:
        rc, out, err = self._run_engine("--metric", "sharpe")
        return self._parse_sharpe(out)

    def phase_guard(self) -> tuple[bool, str, float, float]:
        rc, out, err = self._run_engine("--guard")
        guard_pass = rc == 0
        m_wr = _WR_RE.search(out)
        m_dd = _DD_RE.search(out)
        win_rate = float(m_wr.group(1)) if m_wr else 0.0
        max_dd   = float(m_dd.group(1)) if m_dd else 100.0
        return guard_pass, out.strip(), win_rate, max_dd

    def phase_decide(
        self,
        baseline_sharpe: float,
        new_sharpe: float,
        guard_pass: bool,
        baseline_wr: float,
        new_wr: float,
    ) -> str:
        if guard_pass:
            return "keep"  # full guard pass always keeps
        # Greedy: keep if win_rate improved AND sharpe didn't regress badly
        if new_wr > baseline_wr and new_sharpe >= baseline_sharpe * 0.9:
            return "keep"
        # Keep pure sharpe improvements when already close to guard
        if new_sharpe > baseline_sharpe and new_wr >= baseline_wr:
            return "keep"
        return "rollback"

    def phase_log(
        self,
        iteration: int,
        proposal: dict,
        sharpe: float,
        guard_text: str,
        decision: str,
    ) -> None:
        max_dd = ""
        win_rate = ""
        # Try to pull metrics out of guard line
        m_dd = re.search(r"drawdown=([0-9.]+)%", guard_text)
        if m_dd:
            max_dd = m_dd.group(1)
        m_wr = re.search(r"win_rate=([0-9.]+)%", guard_text)
        if m_wr:
            win_rate = m_wr.group(1)
        ts = datetime.now(tz=timezone.utc).isoformat()
        with self.results_path.open("a") as f:
            f.write(
                f"{iteration}\t{proposal['param']}\t{proposal['old']}\t{proposal['new']}\t"
                f"{sharpe:.4f}\t{max_dd}\t{win_rate}\t{decision}\t{ts}\n"
            )

    # ------------------------------------------------------------------ #
    # Driver                                                             #
    # ------------------------------------------------------------------ #

    def run(self, max_iterations: int = 5) -> dict:
        params = self.phase_review()
        # Baseline
        self._save_params(params)
        baseline = self.phase_verify()
        consecutive_keeps = 0
        last_decision = ""

        # Baseline guard metrics
        _, _gt, baseline_wr, _ = self.phase_guard()

        for i in range(1, max_iterations + 1):
            proposal = self.phase_ideate(params)
            self._visited.add((proposal["param"], proposal["new"]))
            new_params = self.phase_modify(params, proposal)
            self.phase_commit(proposal)
            new_sharpe = self.phase_verify()
            guard_pass, guard_text, new_wr, new_dd = self.phase_guard()
            decision = self.phase_decide(baseline, new_sharpe, guard_pass, baseline_wr, new_wr)
            self.phase_log(i, proposal, new_sharpe, guard_text, decision)

            if decision == "keep":
                params = new_params
                baseline = new_sharpe
                baseline_wr = new_wr
                consecutive_keeps += 1
                if guard_pass and consecutive_keeps >= 3 and new_sharpe > 1.5:
                    last_decision = "converged"
                    break
            else:
                self._save_params(params)
                consecutive_keeps = 0
            last_decision = decision

        return {
            "final_sharpe": baseline,
            "final_params": params,
            "iterations": i,
            "decision": last_decision,
            "results_path": str(self.results_path),
        }
