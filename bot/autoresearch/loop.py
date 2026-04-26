"""
AutoresearchLoop (US-010).

Eight-phase coordinate-descent search over the params.yaml overlay.
Each iteration tweaks one parameter, re-runs the backtest CLI, parses
``SHARPE`` and the guard exit code, and either keeps or rolls back.

Wave 0 changes (F4, F5):
- ``phase_verify`` now invokes ``backtest/engine.py`` with
  ``--cv kfold:N --embargo M`` so the optimisation metric is
  out-of-sample. The full-window codepath remains as a fallback when
  ``autoresearch.cv_n_splits`` is set to 0.
- The keep/rollback decision uses the **deflated Sharpe ratio** (DSR),
  which corrects raw Sharpe for the inflation introduced by repeatedly
  searching over the same dataset (López de Prado, AFML Ch. 11).
- ``results.tsv`` has a new ``dsr`` column. Old files are rotated to
  ``.bak`` automatically (existing schema-change rotation logic).
"""
from __future__ import annotations

import json
import math
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


def deflated_sharpe(observed_sr: float, trial_srs: list[float],
                    n_returns: int = 252) -> float:
    """Pragmatic deflated-Sharpe estimate (López de Prado, AFML Ch. 11).

    Returns ``observed_sr - expected_max_sr_under_null`` where the expected
    maximum is the Gumbel approximation over ``len(trial_srs)`` independent
    Gaussian draws. We deliberately drop the skew/kurtosis correction (PSR's
    third-/fourth-moment terms) because we do not have access to the per-bar
    return series from the engine subprocess — what we get back is one Sharpe
    number. The result is interpretable as "Sharpe in excess of what random
    search would hand you for free".

    Edge cases:
    - With <2 trials, returns ``observed_sr`` unchanged (no penalty
      computable).
    - With zero std across trials, returns ``observed_sr`` unchanged.
    - The ``n_returns`` parameter is reserved for a future PSR upgrade; not
      currently used in the no-skew variant.
    """
    finite = [s for s in trial_srs if math.isfinite(s)]
    n = len(finite)
    if n < 2:
        return observed_sr
    mean_sr = sum(finite) / n
    var = sum((s - mean_sr) ** 2 for s in finite) / (n - 1)
    sr_std = math.sqrt(var)
    if sr_std == 0.0:
        return observed_sr
    # Gumbel approximation of E[max of N iid standard normals]
    if n == 1:
        e_max_z = 0.0
    elif n == 2:
        e_max_z = 0.5641895835  # 1 / sqrt(pi)
    else:
        log_n = math.log(n)
        e_max_z = math.sqrt(2.0 * log_n) - (
            (math.log(math.log(n)) + math.log(4.0 * math.pi))
            / (2.0 * math.sqrt(2.0 * log_n))
        )
    sr_threshold = mean_sr + sr_std * e_max_z
    return float(observed_sr - sr_threshold)


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
        # Walk-forward + multi-symbol aggregation config (loaded from config.yaml)
        self._wf_train_pct, self._multi_symbol_mean = self._load_autoresearch_cfg()
        self._cv_n_splits, self._cv_embargo = self._load_cv_cfg()
        # Track historical Sharpes (in-process) for DSR; loaded from existing
        # results.tsv on init so the multiple-testing penalty includes prior
        # iterations across restarts.
        self._sharpe_history: list[float] = self._load_sharpe_history()

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #

    _RESULTS_HEADER = (
        "iteration\tparam\told_val\tnew_val\tsharpe\tdsr\tmax_dd\t"
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

    def _load_cv_cfg(self) -> tuple[int, int]:
        """Read autoresearch.cv_n_splits and autoresearch.cv_embargo.

        Defaults: 5-fold with 24-bar embargo (1 day on H1). Set
        ``cv_n_splits: 0`` in config to disable k-fold and fall back to the
        old full-window verify path.
        """
        try:
            cfg = yaml.safe_load(self.config_path.read_text()) or {}
            ar = (cfg.get("autoresearch") or {})
            n_splits = int(ar.get("cv_n_splits", 5))
            embargo = int(ar.get("cv_embargo", 24))
            return max(0, n_splits), max(0, embargo)
        except Exception:
            return 5, 24

    def _load_autoresearch_cfg(self) -> tuple[float, bool]:
        """Read autoresearch.wf_train_pct and autoresearch.multi_symbol_mean.

        Defaults: wf_train_pct=0.0 (disabled), multi_symbol_mean=True.
        Both options are opt-in — when disabled, behaviour matches the
        pre-change single-symbol / full-window codepath.
        """
        try:
            cfg = yaml.safe_load(self.config_path.read_text()) or {}
            ar = (cfg.get("autoresearch") or {})
            wf = float(ar.get("wf_train_pct", 0.0) or 0.0)
            multi = bool(ar.get("multi_symbol_mean", True))
            # Clamp to safe range
            if wf < 0.0:
                wf = 0.0
            if wf >= 1.0:
                wf = 0.999
            return wf, multi
        except Exception:
            return 0.0, True

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

    def _load_sharpe_history(self) -> list[float]:
        """Read prior Sharpe values from results.tsv so DSR's multiple-testing
        penalty spans loop restarts."""
        if not self.results_path.exists():
            return []
        out: list[float] = []
        try:
            with self.results_path.open() as f:
                next(f, None)  # skip header
                for line in f:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) < 5:
                        continue
                    try:
                        out.append(float(parts[4]))
                    except ValueError:
                        continue
        except Exception:
            return []
        return out

    def _verify_flags(self) -> tuple[str, ...]:
        """Build the verify-time CLI flags. Uses purged k-fold when configured
        (Wave 0 default) and falls back to the legacy full-window call if
        cv_n_splits == 0."""
        if self._cv_n_splits >= 2:
            return (
                "--metric", "sharpe",
                "--cv", f"kfold:{self._cv_n_splits}",
                "--embargo", str(self._cv_embargo),
            )
        return ("--metric", "sharpe")

    def phase_verify(self) -> float:
        """Sharpe across configured symbols, evaluated via purged k-fold CV.

        Aggregation rule:
        - len(symbols) == 1 → return that symbol's k-fold-mean sharpe.
        - len(symbols) > 1 AND multi_symbol_mean=True → mean across symbols.
        - len(symbols) > 1 AND multi_symbol_mean=False → first symbol only.

        F4: This now uses ``--cv kfold:N --embargo M`` so the optimisation
        metric is the mean per-fold OOS Sharpe, not the in-sample full-window
        Sharpe. Set ``autoresearch.cv_n_splits: 0`` to opt out.
        """
        flags = self._verify_flags()
        if len(self._symbols) == 1 or not self._multi_symbol_mean:
            sym = self._symbols[0]
            _, out, _ = self._run_engine(*flags, symbol=sym)
            return self._parse_sharpe(out)
        sharpes: list[float] = []
        for sym in self._symbols:
            _, out, _ = self._run_engine(*flags, symbol=sym)
            sharpes.append(self._parse_sharpe(out))
        return sum(sharpes) / len(sharpes) if sharpes else float("-inf")

    def phase_guard(self) -> tuple[bool, str, float, float]:
        """Guard passes only when ALL configured symbols pass.

        Walk-forward holdout (--wf-train-pct) is applied to guard calls when
        configured > 0 so that acceptance is judged on out-of-sample bars.
        """
        guard_flags: tuple[str, ...] = ("--guard",)
        if self._wf_train_pct > 0.0:
            guard_flags = ("--guard", "--wf-train-pct", str(self._wf_train_pct))

        all_pass = True
        win_rates: list[float] = []
        max_dds: list[float] = []
        guard_lines: list[str] = []
        for sym in self._symbols:
            rc, out, _ = self._run_engine(*guard_flags, symbol=sym)
            if rc != 0 or not self._parse_guard(out):
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
        baseline_dsr: float | None = None,
        new_dsr: float | None = None,
    ) -> str:
        """Keep/rollback decision based on raw OOS Sharpe (from k-fold) +
        guard pass.

        We deliberately do NOT use DSR for per-iteration decisions: the
        multiple-testing penalty grows monotonically with each iteration,
        making baseline_dsr and new_dsr non-comparable across the loop. DSR is
        instead reported at end-of-run as an honesty check on the final
        winner. The ``baseline_dsr`` / ``new_dsr`` parameters are accepted for
        API stability but currently unused.
        """
        if guard_pass:
            return "keep"
        # Without a guard pass we now require strict improvement on both
        # Sharpe AND win-rate — no leniency-keeps on guard failure (was a
        # source of noise in the legacy decision rule).
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
        dsr: float | None = None,
    ) -> None:
        max_dd = ""
        win_rate = ""
        m_dd = re.search(r"drawdown=([0-9.]+)%", guard_text)
        if m_dd:
            max_dd = m_dd.group(1)
        m_wr = re.search(r"win_rate=([0-9.]+)%", guard_text)
        if m_wr:
            win_rate = m_wr.group(1)
        dsr_str = "" if dsr is None else f"{dsr:.4f}"
        ts = datetime.now(tz=timezone.utc).isoformat()
        with self.results_path.open("a") as f:
            f.write(
                f"{iteration}\t{proposal['param']}\t{proposal['old']}\t{proposal['new']}\t"
                f"{sharpe:.4f}\t{dsr_str}\t{max_dd}\t{win_rate}\t{decision}\t{strategy}\t{ts}\n"
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
        self._sharpe_history.append(baseline)
        baseline_dsr = deflated_sharpe(baseline, self._sharpe_history)
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
            self._sharpe_history.append(new_sharpe)
            new_dsr = deflated_sharpe(new_sharpe, self._sharpe_history)
            guard_pass, guard_text, new_wr, new_dd = self.phase_guard()
            decision = self.phase_decide(
                baseline, new_sharpe, guard_pass,
                baseline_wr, new_wr,
                baseline_dsr=baseline_dsr, new_dsr=new_dsr,
            )
            self.phase_log(
                i, proposal, new_sharpe, guard_text, decision,
                strategy=current_strategy, dsr=new_dsr,
            )

            if decision == "keep":
                params = new_params
                baseline = new_sharpe
                baseline_wr = new_wr
                baseline_dsr = new_dsr
                consecutive_keeps += 1
                # Convergence: 3 consecutive keeps with raw Sharpe above the
                # target. DSR is reported in the result for honesty but does
                # not gate convergence (it would never trigger with growing
                # multiple-testing penalty).
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
            "final_dsr": baseline_dsr,
            "final_params": params,
            "iterations": i,
            "decision": last_decision,
            "results_path": str(self.results_path),
        }
