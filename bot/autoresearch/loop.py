"""
AutoresearchLoop (US-010).

Eight-phase coordinate-descent search over the params.yaml overlay.
Each iteration tweaks one parameter, re-runs the backtest CLI, parses
``SHARPE`` and the guard exit code, and either keeps or rolls back.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml


_BOT_ROOT = Path(__file__).resolve().parents[1]
_ENGINE = _BOT_ROOT / "backtest" / "engine.py"

# Strategy-specific parameter search spaces: (name, step, min, max)
_PARAMS_EMA = [
    ("ema_fast", 1.0, 3.0, 30.0),
    ("ema_slow", 1.0, 5.0, 200.0),
    ("atr_multiplier", 0.25, 0.5, 5.0),
]

_PARAMS_MR = [
    ("bb_period", 1.0, 10.0, 50.0),
    ("bb_std", 0.25, 1.0, 4.0),
    ("rsi_period", 1.0, 5.0, 50.0),
    ("atr_multiplier", 0.25, 0.5, 5.0),
]


def _strategy_params(current: dict) -> list:
    return _PARAMS_MR if current.get("strategy") == "mean_reversion" else _PARAMS_EMA

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
        self._visited_path = self.params_path.parent / "visited.json"
        self._ensure_results_header()
        self._param_cursor = 0
        self._direction = 1
        self._visited: set[tuple] = self._load_visited()
        self._symbols = self._configured_symbols()

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #

    _RESULTS_HEADER = (
        "iteration\tparam\told_val\tnew_val\tsharpe\tmax_dd\t"
        "win_rate\tdecision\tstrategy\ttimestamp\n"
    )

    def _ensure_results_header(self) -> None:
        if self.results_path.exists() and self.results_path.stat().st_size > 0:
            first_line = self.results_path.open().readline()
            if first_line.strip() == self._RESULTS_HEADER.strip():
                return
            # Schema changed — rotate old file and start fresh
            old = self.results_path.with_suffix(".tsv.bak")
            self.results_path.rename(old)
        self.results_path.write_text(self._RESULTS_HEADER)

    def _load_visited(self) -> set[tuple]:
        if not self._visited_path.exists():
            return set()
        try:
            data = json.loads(self._visited_path.read_text())
            return {tuple(item) for item in data}
        except Exception:
            return set()

    def _save_visited(self) -> None:
        try:
            self._visited_path.write_text(
                json.dumps([list(item) for item in self._visited])
            )
        except Exception:
            pass

    def _configured_symbols(self) -> list[str]:
        try:
            cfg = yaml.safe_load(self.config_path.read_text()) or {}
            instruments = (cfg.get("bot") or {}).get("instruments") or ["EURUSD"]
            return list(instruments) if instruments else ["EURUSD"]
        except Exception:
            return ["EURUSD"]

    def _load_params(self) -> dict:
        if not self.params_path.exists():
            return {"strategy": "ema_crossover", "ema_fast": 9, "ema_slow": 21}
        with self.params_path.open() as f:
            return yaml.safe_load(f) or {}

    def _save_params(self, params: dict) -> None:
        with self.params_path.open("w") as f:
            yaml.safe_dump(params, f, sort_keys=False)

    def _run_engine(self, *flags: str, symbol: str | None = None) -> tuple[int, str, str]:
        cmd = [
            sys.executable, str(_ENGINE),
            "--params", str(self.params_path),
            "--symbol", symbol or self._symbols[0],
            "--bars", "2000",
            *flags,
        ]
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
        param_space = _strategy_params(current)
        n = len(param_space)
        # Try each param in both directions; skip visited (param, value) pairs
        for offset in range(n):
            name, step, lo, hi = param_space[(self._param_cursor + offset) % n]
            old = float(current.get(name, 0))
            for direction in (1, -1):
                candidate = round(max(lo, min(hi, old + direction * step)), 4)
                if candidate != old and (name, candidate) not in self._visited:
                    self._param_cursor = (self._param_cursor + offset + 1) % n
                    return {"param": name, "old": old, "new": candidate}
        # All nearby values exhausted — take larger steps
        for name, step, lo, hi in param_space:
            old = float(current.get(name, 0))
            for mult in (2, 3, 5):
                for direction in (1, -1):
                    candidate = round(max(lo, min(hi, old + direction * step * mult)), 4)
                    if candidate != old and (name, candidate) not in self._visited:
                        return {"param": name, "old": old, "new": candidate}
        # Hard fallback — reset visited and try again
        self._visited.clear()
        name, step, lo, hi = param_space[0]
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
        """Average Sharpe across all configured symbols."""
        sharpes = []
        for sym in self._symbols:
            _, out, _ = self._run_engine("--metric", "sharpe", symbol=sym)
            sharpes.append(self._parse_sharpe(out))
        return sum(sharpes) / len(sharpes) if sharpes else float("-inf")

    def phase_guard(self) -> tuple[bool, str, float, float]:
        """Guard passes only when ALL configured symbols pass."""
        all_pass = True
        win_rates: list[float] = []
        max_dds: list[float] = []
        guard_lines: list[str] = []
        for sym in self._symbols:
            rc, out, _ = self._run_engine("--guard", symbol=sym)
            if rc != 0:
                all_pass = False
            guard_lines.append(f"{sym}: {out.strip()}")
            m_wr = _WR_RE.search(out)
            m_dd = _DD_RE.search(out)
            win_rates.append(float(m_wr.group(1)) if m_wr else 0.0)
            max_dds.append(float(m_dd.group(1)) if m_dd else 100.0)
        avg_wr = sum(win_rates) / len(win_rates) if win_rates else 0.0
        worst_dd = max(max_dds) if max_dds else 100.0
        return all_pass, "\n".join(guard_lines), avg_wr, worst_dd

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
        strategy: str = "",
    ) -> None:
        max_dd = ""
        win_rate = ""
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
                f"{sharpe:.4f}\t{max_dd}\t{win_rate}\t{decision}\t{strategy}\t{ts}\n"
            )

    def phase_compare_strategies(self, params: dict) -> dict:
        """Evaluate both strategies; switch to whichever has higher Sharpe.

        Only switches if the challenger beats the current strategy by more than
        10% — avoids thrashing on noise with synthetic data.
        """
        current = params.get("strategy", "ema_crossover")
        challenger = "mean_reversion" if current == "ema_crossover" else "ema_crossover"

        # Score current strategy across all symbols (params already saved by caller)
        current_sharpe = self.phase_verify()

        # Score challenger across all symbols
        test_params = dict(params)
        test_params["strategy"] = challenger
        self._save_params(test_params)
        challenger_sharpe = self.phase_verify()

        if challenger_sharpe > current_sharpe * 1.10:
            params = test_params
            print(
                f"[autoresearch] strategy switch {current}->{challenger} "
                f"sharpe {current_sharpe:.3f}->{challenger_sharpe:.3f}"
            )
        else:
            # Restore original strategy
            self._save_params(params)

        return params

    # ------------------------------------------------------------------ #
    # Driver                                                             #
    # ------------------------------------------------------------------ #

    def run(self, max_iterations: int = 5) -> dict:
        params = self.phase_review()
        self._save_params(params)
        params = self.phase_compare_strategies(params)
        # Baseline
        baseline = self.phase_verify()
        consecutive_keeps = 0
        last_decision = ""

        # Baseline guard metrics
        _, _gt, baseline_wr, _ = self.phase_guard()

        current_strategy = params.get("strategy", "ema_crossover")
        for i in range(1, max_iterations + 1):
            proposal = self.phase_ideate(params)
            self._visited.add((proposal["param"], proposal["new"]))
            new_params = self.phase_modify(params, proposal)
            self.phase_commit(proposal)
            new_sharpe = self.phase_verify()
            guard_pass, guard_text, new_wr, new_dd = self.phase_guard()
            decision = self.phase_decide(baseline, new_sharpe, guard_pass, baseline_wr, new_wr)
            self.phase_log(i, proposal, new_sharpe, guard_text, decision, strategy=current_strategy)

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

        self._save_visited()
        return {
            "final_sharpe": baseline,
            "final_params": params,
            "iterations": i,
            "decision": last_decision,
            "results_path": str(self.results_path),
        }
