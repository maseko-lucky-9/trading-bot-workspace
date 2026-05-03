"""SupervisorLoop — autonomous meta-controller for the MT5 trading bot.

Eight phases per iteration:
  1. Market data check (bridge ping + bar-count validation)
  2. Autoresearch sweep (8 combos sequentially)
  3. DSR gate + param promotion / rollback
  4. Regression detection
  5. Issue filing + patch agent
  6. CI gate sweep (merge promotable PRs)
  7. Drawdown check + escalation
  8. Checkpoint + sleep

Entry:
    python -m bot.supervisor.loop                     # full run, loop forever
    python -m bot.supervisor.loop --dry-run           # no subprocesses or API calls
    python -m bot.supervisor.loop --once              # single iteration then exit
    python -m bot.supervisor.loop --skip-autoresearch # phases 3-8 on existing data
    python -m bot.supervisor.loop --escalate          # one-shot test escalation then exit
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

log = logging.getLogger(__name__)

_BOT_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_PATH = _BOT_ROOT / "config.yaml"
_BASE_PARAMS = _BOT_ROOT / "autoresearch" / "params.yaml"
_PID_FILE = Path(__file__).resolve().parent / ".supervisor.pid"
_SNAPSHOTS_DIR = Path(__file__).resolve().parent / "snapshots"
_DB_PATH = Path(__file__).resolve().parent / "supervisor.db"
_BRIDGE_BASE = "http://192.168.64.1:8080"


def _load_config() -> dict:
    try:
        return yaml.safe_load(_CONFIG_PATH.read_text()) or {}
    except Exception:
        return {}


def _supervisor_cfg(cfg: dict) -> dict:
    return cfg.get("supervisor") or {}


def _bot_mode(cfg: dict) -> str:
    return (cfg.get("bot") or {}).get("mode", "paper")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(name)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


# ---------------------------------------------------------------------------
# Startup checks
# ---------------------------------------------------------------------------

def _check_pid_lock() -> None:
    if not _PID_FILE.exists():
        return
    try:
        existing_pid = int(_PID_FILE.read_text().strip())
        os.kill(existing_pid, 0)  # raises if process doesn't exist
        print(
            f"ERROR: supervisor already running (PID {existing_pid}). "
            f"Remove {_PID_FILE} if the process is dead.",
            file=sys.stderr,
        )
        sys.exit(1)
    except (ProcessLookupError, PermissionError):
        pass  # stale PID file; overwrite below


def _write_pid() -> None:
    _PID_FILE.write_text(str(os.getpid()))


def _remove_pid() -> None:
    try:
        _PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _check_gh_auth() -> None:
    result = subprocess.run(
        ["gh", "auth", "status"], capture_output=True, text=True
    )
    if result.returncode != 0:
        print(
            "ERROR: gh CLI not authenticated. Run `gh auth login` first.",
            file=sys.stderr,
        )
        sys.exit(1)


def _check_git_remote() -> None:
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        capture_output=True, text=True, cwd=str(_BOT_ROOT),
    )
    if result.returncode != 0:
        print("ERROR: no git remote 'origin' configured.", file=sys.stderr)
        sys.exit(1)


def _check_deps() -> None:
    try:
        import anthropic  # noqa: F401
        import httpx      # noqa: F401
    except ImportError as exc:
        log.warning("optional dependency missing: %s", exc)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.warning(
            "ANTHROPIC_API_KEY not set — patch agent will be disabled. "
            "Regressions will be filed as issues but not auto-patched."
        )


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def _bootstrap() -> None:
    """Create DB, combo dirs, snapshots dir (idempotent)."""
    from .checkpoint import bootstrap as db_bootstrap
    from .autoresearch_runner import bootstrap_combo_dirs

    db_bootstrap(db_path=_DB_PATH)
    _SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    bootstrap_combo_dirs()
    log.info("bootstrap complete")


# ---------------------------------------------------------------------------
# Phase 1 — Market data check
# ---------------------------------------------------------------------------

def _phase1_market_check() -> bool:
    """Ping bridge and verify data freshness. Returns True if bridge is available."""
    import httpx

    try:
        resp = httpx.get(f"{_BRIDGE_BASE}/ping", timeout=5.0)
        resp.raise_for_status()
        log.info("phase1: bridge OK")
        return True
    except Exception as exc:
        log.warning("phase1: bridge unreachable: %s — skipping phases 2-3", exc)
        return False


# ---------------------------------------------------------------------------
# Phase 2 — Autoresearch sweep
# ---------------------------------------------------------------------------

def _phase2_autoresearch(
    combos,
    iteration_id: int,
    *,
    start_combo_index: int = 0,
    dry_run: bool = False,
) -> list:
    from .autoresearch_runner import run_combo, ComboResult
    from .checkpoint import insert_autoresearch_run, update_iteration

    results: list[ComboResult] = []
    for idx, combo in enumerate(combos):
        if idx < start_combo_index:
            log.info("phase2: skipping resumed combo %d (%s)", idx, combo.slug)
            continue

        update_iteration(iteration_id, phase="phase2", last_combo_index=idx, db_path=_DB_PATH)
        log.info("phase2: running combo %d/%d: %s", idx + 1, len(combos), combo.slug)

        result = run_combo(combo, dry_run=dry_run)
        results.append(result)

        insert_autoresearch_run(
            iteration_id,
            combo.symbol, combo.timeframe, combo.strategy,
            sharpe=result.sharpe,
            dsr=result.dsr,
            guard=result.guard,
            max_dd=result.max_dd,
            win_rate=result.win_rate,
            db_path=_DB_PATH,
        )

    return results


# ---------------------------------------------------------------------------
# Phase 3 — DSR gate + param promotion / rollback
# ---------------------------------------------------------------------------

def _snapshot_path(combo_slug: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return _SNAPSHOTS_DIR / f"{combo_slug}_{ts}.yaml"


def _promote_combo(combo, run_result, run_id: int, dsr_threshold: float) -> bool:
    """Attempt to promote params if DSR gate clears. Returns True if promoted."""
    if run_result.dsr is None or run_result.dsr <= dsr_threshold:
        return False
    if run_result.guard != "PASS":
        return False
    if run_result.error:
        return False

    combo_params_path = combo.combo_dir / "params.yaml"
    if not combo_params_path.exists():
        return False

    # Snapshot current base params
    snap = _snapshot_path(combo.slug)
    shutil.copy2(_BASE_PARAMS, snap)

    # Promote combo params → base params
    shutil.copy2(combo_params_path, _BASE_PARAMS)

    # Commit
    result = subprocess.run(
        ["git", "add",
         str(_BASE_PARAMS.relative_to(_BOT_ROOT.parent)),
         str(snap.relative_to(_BOT_ROOT.parent))],
        cwd=str(_BOT_ROOT.parent), capture_output=True, text=True,
    )
    if result.returncode == 0:
        msg = f"chore(autoresearch): promote params {combo.slug} DSR={run_result.dsr:.2f}"
        subprocess.run(
            ["git", "commit", "-m", msg],
            cwd=str(_BOT_ROOT.parent), capture_output=True, text=True,
        )

    from .checkpoint import update_autoresearch_run
    update_autoresearch_run(run_id, promoted=1, snapshot_path=str(snap), db_path=_DB_PATH)
    log.info("promoted: %s DSR=%.3f snapshot=%s", combo.slug, run_result.dsr, snap)
    return True


def _rollback_if_needed(combo, dsr_floor: float) -> bool:
    """Rollback last promotion if new DSR fell below floor. Returns True if rolled back."""
    from .checkpoint import get_last_promoted_run, update_autoresearch_run
    from .autoresearch_runner import run_combo

    last_promoted = get_last_promoted_run(
        combo.symbol, combo.timeframe, combo.strategy, db_path=_DB_PATH
    )
    if last_promoted is None or last_promoted["promoted"] != 1:
        return False

    # Check current DSR
    current = run_combo(combo, max_iterations=1)
    if current.dsr is None or current.dsr >= dsr_floor:
        return False

    snapshot_path = last_promoted["snapshot_path"]
    if not snapshot_path or not Path(snapshot_path).exists():
        log.warning("rollback: snapshot missing for %s", combo.slug)
        return False

    shutil.copy2(snapshot_path, _BASE_PARAMS)
    subprocess.run(
        ["git", "add", str(_BASE_PARAMS.relative_to(_BOT_ROOT.parent))],
        cwd=str(_BOT_ROOT.parent), capture_output=True, text=True,
    )
    msg = f"chore(autoresearch): rollback {combo.slug} DSR={current.dsr:.2f}"
    subprocess.run(
        ["git", "commit", "-m", msg],
        cwd=str(_BOT_ROOT.parent), capture_output=True, text=True,
    )

    update_autoresearch_run(int(last_promoted["id"]), promoted=-1, db_path=_DB_PATH)
    log.warning("rolled back: %s DSR=%.3f < floor=%.3f", combo.slug, current.dsr, dsr_floor)
    return True


def _phase3_promotions(
    combos,
    ar_results: list,
    iteration_id: int,
    *,
    dsr_threshold: float,
    dsr_floor: float,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Returns (promoted_count, rollback_count)."""
    from .checkpoint import get_recent_runs_for_combo

    promoted = 0
    rolled_back = 0

    for combo, result in zip(combos, ar_results):
        if dry_run:
            continue

        # Check rollback for this combo's last promotion
        if _rollback_if_needed(combo, dsr_floor):
            rolled_back += 1

        # Check promotion
        recent_runs = get_recent_runs_for_combo(
            combo.symbol, combo.timeframe, combo.strategy, n=1, db_path=_DB_PATH
        )
        if not recent_runs:
            continue
        last_run = recent_runs[0]
        run_id = int(last_run["id"])

        if _promote_combo(combo, result, run_id, dsr_threshold):
            promoted += 1

    return promoted, rolled_back


# ---------------------------------------------------------------------------
# Phase 4 — Regression detection
# ---------------------------------------------------------------------------

def _phase4_detect_regressions() -> list:
    from .regression_detector import RegressionDetector
    detector = RegressionDetector()
    return detector.scan()


# ---------------------------------------------------------------------------
# Phase 5 — Issue filing + patch agent
# ---------------------------------------------------------------------------

def _render_issue_body(regression, iteration_id: int) -> str:
    evidence_json = json.dumps(regression.evidence, indent=2)
    scope_line = regression.combo_slug or "global"
    return "\n".join([
        "## Auto-detected regression",
        "",
        f"- **Type**: {regression.regression_type}",
        f"- **Scope**: {regression.scope}",
        f"- **Combo**: {scope_line}",
        f"- **Detected at**: {datetime.now(timezone.utc).isoformat()} (iteration {iteration_id})",
        "",
        "## Evidence",
        "",
        "```json",
        evidence_json,
        "```",
        "",
        "## Reproduction",
        "",
        "```bash",
        f"python -m bot.supervisor.regression_detector --replay {iteration_id}",
        "```",
        "",
        "## Auto-patching",
        "",
        "A fix branch will be opened automatically if ANTHROPIC_API_KEY is set and daily budget allows.",
    ])


def _file_issue(regression, iteration_id: int) -> Optional[int]:
    body = _render_issue_body(regression, iteration_id)
    title = f"{regression.regression_type}: auto-detected"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as fh:
        fh.write(body)
        body_file = fh.name

    try:
        result = subprocess.run(
            ["gh", "issue", "create", "--title", title, "--body-file", body_file],
            cwd=str(_BOT_ROOT), capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            log.error("gh issue create failed: %s", result.stderr)
            return None
        import re
        match = re.search(r"/issues/(\d+)", result.stdout)
        return int(match.group(1)) if match else None
    finally:
        try:
            os.unlink(body_file)
        except OSError:
            pass


def _phase5_issues_and_patches(
    regressions,
    iteration_id: int,
    *,
    max_concurrent_patches: int,
    max_patches_per_day: int,
    dry_run: bool = False,
) -> int:
    from .checkpoint import (
        regression_already_detected,
        insert_regression_event,
        update_regression_event,
        get_patch_attempts_today,
        increment_patch_attempts,
        get_patching_regression_events,
    )
    from .patch_agent import PatchAgent

    new_regressions = 0
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    patching_count = len(get_patching_regression_events(db_path=_DB_PATH))

    for regression in regressions:
        if regression_already_detected(
            regression.regression_type, regression.combo_slug, db_path=_DB_PATH
        ):
            continue

        event_id = insert_regression_event(
            regression.regression_type,
            regression.description,
            regression.evidence,
            scope=regression.scope,
            combo_slug=regression.combo_slug,
            db_path=_DB_PATH,
        )
        new_regressions += 1
        log.info("regression event created: id=%d type=%s", event_id, regression.regression_type)

        if dry_run:
            continue

        # Issue filing
        issue_number = _file_issue(regression, iteration_id)
        if issue_number:
            update_regression_event(
                event_id, status="issue_filed",
                github_issue_number=issue_number, db_path=_DB_PATH
            )
            log.info("issue filed: #%d", issue_number)
        else:
            log.warning("issue filing failed for event %d", event_id)

        # Patch agent
        if not api_key:
            log.warning("ANTHROPIC_API_KEY not set — skipping patch for event %d", event_id)
            continue

        if get_patch_attempts_today(db_path=_DB_PATH) >= max_patches_per_day:
            log.info("daily patch budget exhausted — skipping patch for event %d", event_id)
            continue

        if patching_count >= max_concurrent_patches:
            log.info("concurrent patch limit reached — skipping event %d", event_id)
            continue

        count_after = increment_patch_attempts(db_path=_DB_PATH)
        log.info("patch attempt %d/%d for event %d", count_after, max_patches_per_day, event_id)

        update_regression_event(event_id, status="patching", db_path=_DB_PATH)

        agent = PatchAgent(api_key=api_key)
        success, pr_number = agent.run(
            regression.regression_type,
            regression.description,
            regression.evidence,
            issue_number=issue_number,
        )

        if success and pr_number:
            update_regression_event(event_id, pr_number=pr_number, db_path=_DB_PATH)
            patching_count += 1
            log.info("patch PR created: #%d for event %d", pr_number, event_id)
        else:
            update_regression_event(event_id, status="patch_failed", db_path=_DB_PATH)
            log.warning("patch failed for event %d", event_id)

    return new_regressions


# ---------------------------------------------------------------------------
# Phase 6 — CI gate sweep
# ---------------------------------------------------------------------------

def _phase6_ci_gate(*, bot_mode: str, live_merge_quiet_seconds: int) -> None:
    from .checkpoint import get_patching_regression_events, update_regression_event
    from .ci_gate import process_patching_events

    events = get_patching_regression_events(db_path=_DB_PATH)
    if not events:
        return

    updates = process_patching_events(
        events,
        bot_mode=bot_mode,
        live_merge_quiet_seconds=live_merge_quiet_seconds,
        db_path=_DB_PATH,
    )

    for event_id, new_status in updates.items():
        update_regression_event(event_id, status=new_status, db_path=_DB_PATH)
        log.info("ci_gate: event %d → %s", event_id, new_status)


# ---------------------------------------------------------------------------
# Phase 7 — Drawdown check + escalation
# ---------------------------------------------------------------------------

def _phase7_escalation(
    *,
    drawdown_alert_pct: float,
    escalation_cooldown_seconds: int,
    ntfy_topic: str,
    escalate_requested: bool,
    dry_run: bool = False,
) -> bool:
    import json
    from .notifier import escalate
    from .checkpoint import get_recent_runs_for_combo, count_consecutive_no_promotions
    from .autoresearch_runner import ALL_COMBOS

    cfg = _load_config()
    state_json = _BOT_ROOT / "checkpoints" / "state.json"
    did_escalate = False

    # Check drawdown
    if state_json.exists():
        try:
            state = json.loads(state_json.read_text())
            peak = float(state.get("peak_equity", 0.0))
            if peak > 0:
                import httpx
                try:
                    resp = httpx.get(f"{_BRIDGE_BASE}/account", timeout=5.0)
                    current_equity = float(resp.json().get("equity", peak))
                    dd = max(0.0, (peak - current_equity) / peak)
                    if dd > drawdown_alert_pct:
                        ok = escalate(
                            cause="drawdown",
                            title=f"Drawdown alert: {dd:.1%}",
                            body=f"Peak equity: {peak:.2f}\nCurrent: {current_equity:.2f}\nDrawdown: {dd:.1%}",
                            payload={"peak": peak, "current": current_equity, "dd": dd},
                            topic=ntfy_topic,
                            cooldown_seconds=escalation_cooldown_seconds,
                            db_path=_DB_PATH,
                            dry_run=dry_run,
                        )
                        if ok:
                            did_escalate = True
                except Exception:
                    pass
        except Exception:
            pass

    # Check DSR degradation per combo
    for combo in ALL_COMBOS:
        recent = get_recent_runs_for_combo(
            combo.symbol, combo.timeframe, combo.strategy, n=5, db_path=_DB_PATH
        )
        if len(recent) < 3:
            continue
        dsrs = [r["dsr"] for r in recent if r["dsr"] is not None]
        if len(dsrs) < 3:
            continue
        # Strictly monotonically decreasing last 3
        last3 = dsrs[:3]
        if last3[0] < last3[1] < last3[2]:  # recent first
            trailing_mean = sum(dsrs) / len(dsrs)
            if last3[0] < trailing_mean - 0.5:
                ok = escalate(
                    cause="dsr_degrade",
                    title=f"DSR degrading: {combo.slug}",
                    body=f"DSR trend (last 3): {last3[0]:.3f} < {last3[1]:.3f} < {last3[2]:.3f}\nTrailing mean: {trailing_mean:.3f}",
                    payload={"combo": combo.slug, "dsrs": dsrs, "trailing_mean": trailing_mean},
                    topic=ntfy_topic,
                    cooldown_seconds=escalation_cooldown_seconds,
                    db_path=_DB_PATH,
                    dry_run=dry_run,
                )
                if ok:
                    did_escalate = True

    # Manual escalation (SIGUSR1)
    if escalate_requested:
        ok = escalate(
            cause="manual",
            title="Manual escalation requested",
            body="Supervisor received SIGUSR1",
            payload={},
            topic=ntfy_topic,
            cooldown_seconds=0,
            db_path=_DB_PATH,
            dry_run=dry_run,
        )
        if ok:
            did_escalate = True

    # No improvement check
    no_improve_count = count_consecutive_no_promotions(db_path=_DB_PATH)
    if no_improve_count >= 20:
        ok = escalate(
            cause="no_improvement",
            title=f"No param promotions for {no_improve_count} iterations",
            body=f"Supervisor has run {no_improve_count} consecutive iterations with zero promotions.",
            payload={"consecutive_no_promotions": no_improve_count},
            topic=ntfy_topic,
            cooldown_seconds=escalation_cooldown_seconds,
            db_path=_DB_PATH,
            dry_run=dry_run,
        )
        if ok:
            did_escalate = True

    return did_escalate


# ---------------------------------------------------------------------------
# SupervisorLoop
# ---------------------------------------------------------------------------

class SupervisorLoop:
    def __init__(
        self,
        *,
        dry_run: bool = False,
        once: bool = False,
        skip_autoresearch: bool = False,
        verbose: bool = False,
    ) -> None:
        self.dry_run = dry_run
        self.once = once
        self.skip_autoresearch = skip_autoresearch
        self._shutdown_requested = False
        self._escalate_requested = False

    def _install_signal_handlers(self) -> None:
        def _shutdown(signum, frame):
            log.info("shutdown signal received")
            self._shutdown_requested = True

        def _escalate(signum, frame):
            log.info("SIGUSR1: manual escalation requested")
            self._escalate_requested = True

        try:
            signal.signal(signal.SIGTERM, _shutdown)
            signal.signal(signal.SIGINT, _shutdown)
            signal.signal(signal.SIGUSR1, _escalate)
        except (ValueError, OSError):
            pass

    def _startup(self) -> None:
        _check_pid_lock()
        _write_pid()
        _check_gh_auth()
        _check_git_remote()
        _check_deps()
        _bootstrap()

    def _resume_info(self) -> tuple[Optional[int], int]:
        """Return (iteration_id_to_resume, start_combo_index) or (None, 0)."""
        from .checkpoint import get_latest_iteration
        row = get_latest_iteration(db_path=_DB_PATH)
        if row and row["status"] == "running" and row["last_combo_index"] is not None:
            log.info(
                "resuming interrupted iteration id=%d at combo %d",
                row["id"], row["last_combo_index"] + 1,
            )
            return int(row["id"]), int(row["last_combo_index"]) + 1
        return None, 0

    def run(self) -> int:
        from .autoresearch_runner import ALL_COMBOS
        from .checkpoint import (
            create_iteration,
            update_iteration,
        )

        self._install_signal_handlers()
        self._startup()

        cfg = _load_config()
        sup = _supervisor_cfg(cfg)
        bot_mode = _bot_mode(cfg)

        interval_s = int(sup.get("interval_seconds", 3600))
        dsr_threshold = float(sup.get("dsr_threshold", 0.95))
        dsr_floor = float(sup.get("dsr_floor_for_rollback", 0.30))
        drawdown_alert_pct = float(sup.get("drawdown_alert_pct", 0.05))
        ntfy_topic = sup.get("ntfy_topic", "prudentia-alerts")
        max_concurrent_patches = int(sup.get("max_concurrent_patch_attempts", 3))
        max_patches_per_day = int(sup.get("max_patch_attempts_per_day", 10))
        escalation_cooldown_s = int(sup.get("escalation_cooldown_seconds", 14400))
        live_merge_quiet_s = int(sup.get("live_merge_quiet_seconds", 86400))

        try:
            while not self._shutdown_requested:
                resume_id, start_combo_index = self._resume_info()
                if resume_id is not None:
                    iteration_id = resume_id
                else:
                    iteration_id = create_iteration(db_path=_DB_PATH)

                log.info("=== iteration %d start ===", iteration_id)
                update_iteration(
                    iteration_id, phase="phase1", status="running", db_path=_DB_PATH
                )

                # Phase 1 — Market data check
                bridge_ok = _phase1_market_check()

                ar_results = []
                promoted_count = 0
                rolled_back = 0

                if bridge_ok and not self.skip_autoresearch:
                    # Phase 2 — Autoresearch sweep
                    update_iteration(iteration_id, phase="phase2", db_path=_DB_PATH)
                    ar_results = _phase2_autoresearch(
                        ALL_COMBOS, iteration_id,
                        start_combo_index=start_combo_index,
                        dry_run=self.dry_run,
                    )

                    # Phase 3 — DSR gate + promotions
                    update_iteration(iteration_id, phase="phase3", db_path=_DB_PATH)
                    promoted_count, rolled_back = _phase3_promotions(
                        ALL_COMBOS, ar_results, iteration_id,
                        dsr_threshold=dsr_threshold,
                        dsr_floor=dsr_floor,
                        dry_run=self.dry_run,
                    )
                    if rolled_back:
                        from .notifier import escalate
                        escalate(
                            cause="rollback",
                            title=f"Param rollback: {rolled_back} combo(s)",
                            body=f"{rolled_back} combo(s) were rolled back this iteration.",
                            payload={"rolled_back": rolled_back},
                            topic=ntfy_topic,
                            cooldown_seconds=escalation_cooldown_s,
                            db_path=_DB_PATH,
                            dry_run=self.dry_run,
                        )

                # Phase 4 — Regression detection
                update_iteration(iteration_id, phase="phase4", db_path=_DB_PATH)
                regressions = _phase4_detect_regressions()

                # Phase 5 — Issue filing + patches
                update_iteration(iteration_id, phase="phase5", db_path=_DB_PATH)
                new_regressions = _phase5_issues_and_patches(
                    regressions, iteration_id,
                    max_concurrent_patches=max_concurrent_patches,
                    max_patches_per_day=max_patches_per_day,
                    dry_run=self.dry_run,
                )

                # Phase 6 — CI gate
                update_iteration(iteration_id, phase="phase6", db_path=_DB_PATH)
                if not self.dry_run:
                    _phase6_ci_gate(
                        bot_mode=bot_mode,
                        live_merge_quiet_seconds=live_merge_quiet_s,
                    )

                # Phase 7 — Drawdown check + escalation
                update_iteration(iteration_id, phase="phase7", db_path=_DB_PATH)
                did_escalate = _phase7_escalation(
                    drawdown_alert_pct=drawdown_alert_pct,
                    escalation_cooldown_seconds=escalation_cooldown_s,
                    ntfy_topic=ntfy_topic,
                    escalate_requested=self._escalate_requested,
                    dry_run=self.dry_run,
                )
                self._escalate_requested = False

                # Phase 8 — Checkpoint
                update_iteration(
                    iteration_id,
                    phase="complete",
                    status="complete",
                    combos_promoted=promoted_count,
                    regressions_detected=new_regressions,
                    escalated=1 if did_escalate else 0,
                    db_path=_DB_PATH,
                )
                log.info(
                    "=== iteration %d complete: promoted=%d regressions=%d escalated=%s ===",
                    iteration_id, promoted_count, new_regressions, did_escalate,
                )

                if self.once:
                    break

                log.info("sleeping %ds until next iteration", interval_s)
                for _ in range(interval_s):
                    if self._shutdown_requested:
                        break
                    time.sleep(1)

        except KeyboardInterrupt:
            pass
        finally:
            _remove_pid()

        return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="bot.supervisor.loop")
    parser.add_argument("--dry-run", action="store_true",
                        help="Log phases but make no subprocess or API calls.")
    parser.add_argument("--once", action="store_true",
                        help="Run a single iteration then exit.")
    parser.add_argument("--skip-autoresearch", action="store_true",
                        help="Skip phases 2-3; run phases 4-8 on existing data.")
    parser.add_argument("--escalate", action="store_true",
                        help="Send a test escalation then exit.")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)

    if args.escalate:
        from .notifier import escalate
        from .checkpoint import bootstrap
        bootstrap(db_path=_DB_PATH)
        ok = escalate(
            cause="manual",
            title="Supervisor test escalation",
            body="One-shot test from --escalate flag.",
            payload={"test": True},
            cooldown_seconds=0,
            db_path=_DB_PATH,
        )
        return 0 if ok else 1

    loop = SupervisorLoop(
        dry_run=args.dry_run,
        once=args.once,
        skip_autoresearch=args.skip_autoresearch,
        verbose=args.verbose,
    )
    return loop.run()


if __name__ == "__main__":
    sys.exit(main())
