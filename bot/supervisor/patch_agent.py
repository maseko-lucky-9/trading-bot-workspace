"""Autonomous bug-patching agent.

Workflow per regression:
  1. git stash (preserve working tree)
  2. checkout feature branch from latest main
  3. POST to Claude API with regression context + relevant source files
  4. Parse response: extract # file: <path> tagged code blocks, overwrite each file
  5. pytest bot/tests/ -x --tb=short (300s timeout)
  6. If green: git push + gh pr create → update regression_event
  7. If red: git stash pop, delete branch → status=patch_failed
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_BOT_ROOT = Path(__file__).resolve().parents[1]
_PYTEST_TIMEOUT = 300
_RATE_LIMIT_BACKOFFS = (60, 120, 240)

# Files to load into context per regression type
_CONTEXT_FILES: dict[str, list[Path]] = {
    "circuit_breaker_false_positive": [
        _BOT_ROOT / "core" / "risk" / "manager.py",
        _BOT_ROOT / "main.py",
    ],
    "visited_set_cycling": [
        _BOT_ROOT / "autoresearch" / "loop.py",
    ],
}

_TAGGED_BLOCK_RE = re.compile(
    r"#\s*file:\s*(\S+)\s*\n```(?:\w+)?\n(.*?)```",
    re.DOTALL,
)


def _run(cmd: list[str], cwd: Path = _BOT_ROOT, timeout: int = 60) -> tuple[int, str]:
    result = subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout
    )
    return result.returncode, (result.stdout + result.stderr).strip()


def _branch_name(regression_type: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"fix/{regression_type}-{ts}"


def _load_source_files(regression_type: str) -> dict[str, str]:
    """Load relevant source files; fall back gracefully if absent."""
    files = {}
    for path in _CONTEXT_FILES.get(regression_type, []):
        if path.exists():
            try:
                files[str(path.relative_to(_BOT_ROOT))] = path.read_text()
            except Exception:
                pass
    return files


def _build_prompt(regression_type: str, description: str, evidence: dict,
                  source_files: dict[str, str]) -> str:
    files_section = "\n\n".join(
        f"# file: {rel_path}\n```python\n{content}\n```"
        for rel_path, content in source_files.items()
    )
    return f"""You are an expert Python developer fixing a bug in an algorithmic trading bot.

## Regression detected

**Type**: {regression_type}
**Description**: {description}
**Evidence**: {evidence}

## Source files

{files_section}

## Your task

Analyse the regression and produce a minimal fix. For each file you modify, output a fenced code block tagged with `# file: <relative/path>` immediately before the opening fence.

Example format:
# file: core/risk/manager.py
```python
<entire file content with fix applied>
```

Rules:
- Output the **entire file content** for each modified file (not a diff or partial snippet).
- Only modify files that are strictly necessary.
- Do not add new dependencies.
- Preserve all existing tests.
- Write no explanatory prose after the code blocks.
"""


def _apply_edits(tagged_blocks: list[tuple[str, str]]) -> list[Path]:
    """Overwrite files from (relative_path, content) pairs. Returns written paths."""
    written = []
    for rel_path, content in tagged_blocks:
        target = _BOT_ROOT / rel_path
        if not target.exists():
            log.warning("patch target does not exist: %s — skipping", target)
            continue
        target.write_text(content)
        written.append(target)
        log.info("patched: %s", target)
    return written


def _run_pytest() -> bool:
    rc, out = _run(
        [sys.executable, "-m", "pytest", "tests/", "-x", "--tb=short", "-q"],
        cwd=_BOT_ROOT,
        timeout=_PYTEST_TIMEOUT,
    )
    log.info("pytest exit=%d", rc)
    if rc != 0:
        log.warning("pytest output:\n%s", out[:2000])
    return rc == 0


def _gh_pr_create(branch: str, regression_type: str, issue_number: Optional[int]) -> Optional[int]:
    """Create a GitHub PR and return its number."""
    title = f"fix({regression_type}): auto-patch"
    body_lines = [
        f"Auto-generated fix for regression: **{regression_type}**",
        "",
        "This PR was opened by the autonomous supervisor patch agent.",
        "CI must be green and paper-trading Sharpe must improve before auto-merge.",
    ]
    if issue_number:
        body_lines.append(f"\nCloses #{issue_number}")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as fh:
        fh.write("\n".join(body_lines))
        body_file = fh.name

    try:
        rc, out = _run(
            ["gh", "pr", "create",
             "--title", title,
             "--body-file", body_file,
             "--head", branch,
             "--base", "main"],
            cwd=_BOT_ROOT,
            timeout=60,
        )
        if rc != 0:
            log.error("gh pr create failed: %s", out)
            return None
        # Extract PR number from output like "https://github.com/.../pull/42"
        match = re.search(r"/pull/(\d+)", out)
        return int(match.group(1)) if match else None
    finally:
        try:
            os.unlink(body_file)
        except OSError:
            pass


class PatchAgent:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-sonnet-4-6",
        dry_run: bool = False,
    ) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model
        self.dry_run = dry_run

    def _call_claude(self, prompt: str) -> Optional[str]:
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key)
        for attempt, backoff in enumerate([0] + list(_RATE_LIMIT_BACKOFFS)):
            if backoff:
                log.info("rate-limit backoff %ds (attempt %d)", backoff, attempt + 1)
                time.sleep(backoff)
            try:
                msg = client.messages.create(
                    model=self.model,
                    max_tokens=8192,
                    messages=[{"role": "user", "content": prompt}],
                )
                return msg.content[0].text
            except anthropic.RateLimitError:
                if attempt >= len(_RATE_LIMIT_BACKOFFS):
                    raise
                continue
        return None

    def run(
        self,
        regression_type: str,
        description: str,
        evidence: dict,
        *,
        issue_number: Optional[int] = None,
    ) -> tuple[bool, Optional[int]]:
        """Attempt to patch a regression. Returns (success, pr_number)."""
        if self.dry_run:
            log.info("[dry-run] patch_agent.run: %s", regression_type)
            return False, None

        stashed = False
        branch = _branch_name(regression_type)

        # 1. Stash working tree
        rc, _ = _run(["git", "stash"], cwd=_BOT_ROOT)
        stashed = rc == 0

        try:
            # 2. Checkout fresh branch from main
            _run(["git", "checkout", "main"], cwd=_BOT_ROOT)
            _run(["git", "pull", "--ff-only", "origin", "main"], cwd=_BOT_ROOT)
            rc, out = _run(["git", "checkout", "-b", branch], cwd=_BOT_ROOT)
            if rc != 0:
                log.error("could not create branch %s: %s", branch, out)
                return False, None

            # 3. Load source files + call Claude
            source_files = _load_source_files(regression_type)
            prompt = _build_prompt(regression_type, description, evidence, source_files)

            log.info("calling Claude API for patch: %s", regression_type)
            response = self._call_claude(prompt)
            if not response:
                log.error("no response from Claude API")
                return False, None

            # 4. Parse and apply edits
            tagged_blocks = _TAGGED_BLOCK_RE.findall(response)
            if not tagged_blocks:
                log.warning("no tagged code blocks in Claude response")
                return False, None

            written = _apply_edits(tagged_blocks)
            if not written:
                log.warning("no files were patched")
                return False, None

            # Stage explicit files only
            for path in written:
                _run(["git", "add", str(path)], cwd=_BOT_ROOT)

            # 5. Run pytest
            if not _run_pytest():
                log.warning("tests failed after patch; abandoning branch")
                return False, None

            # Commit
            _run(
                ["git", "commit", "-m", f"fix({regression_type}): auto-patch"],
                cwd=_BOT_ROOT,
            )

            # 6. Push + PR
            rc, out = _run(
                ["git", "push", "-u", "origin", branch],
                cwd=_BOT_ROOT,
                timeout=120,
            )
            if rc != 0:
                log.error("git push failed: %s", out)
                return False, None

            pr_number = _gh_pr_create(branch, regression_type, issue_number)
            if pr_number:
                log.info("PR created: #%d branch=%s", pr_number, branch)
                return True, pr_number
            return False, None

        except Exception:
            log.exception("patch_agent.run failed for %s", regression_type)
            return False, None

        finally:
            # On failure: restore working tree and delete branch
            if not stashed:
                return  # noqa: B012  # already handled
            # Only pop stash if we didn't successfully push
            try:
                current_branch_rc, current_branch = _run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=_BOT_ROOT
                )
                if current_branch == branch:
                    _run(["git", "checkout", "main"], cwd=_BOT_ROOT)
                    _run(["git", "branch", "-D", branch], cwd=_BOT_ROOT)
            except Exception:
                pass
            if stashed:
                _run(["git", "stash", "pop"], cwd=_BOT_ROOT)
