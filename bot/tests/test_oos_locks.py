"""OOS-window lock & isolation regression suite (T08).

Every test in this module is a *canary*: it must fail loud if the OOS
paper-trading window v2 is silently broken by a future edit. The
fixture lives in tests/fixtures/oos_locks_snapshot.json and is **only**
to be updated by deliberate, reviewed human action — never regenerated
from inside the test runner.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml


_BOT_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "oos_locks_snapshot.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def snapshot() -> dict:
    return json.loads(_FIXTURE.read_text())


def test_locked_params_yaml_unchanged(snapshot):
    expected = snapshot["autoresearch/params.yaml"]
    actual = _sha256(_BOT_ROOT / "autoresearch" / "params.yaml")
    assert actual == expected, (
        "autoresearch/params.yaml has been mutated. The OOS window v2 is "
        "compromised. If this change is intentional, update "
        "tests/fixtures/oos_locks_snapshot.json explicitly."
    )


def test_locked_ema_crossover_unchanged(snapshot):
    expected = snapshot["core/strategy/ema_crossover.py"]
    actual = _sha256(_BOT_ROOT / "core" / "strategy" / "ema_crossover.py")
    assert actual == expected, "core/strategy/ema_crossover.py is locked."


def test_locked_mean_reversion_unchanged(snapshot):
    expected = snapshot["core/strategy/mean_reversion.py"]
    actual = _sha256(_BOT_ROOT / "core" / "strategy" / "mean_reversion.py")
    assert actual == expected, "core/strategy/mean_reversion.py is locked."


def test_autoresearch_enabled_is_false():
    cfg = yaml.safe_load((_BOT_ROOT / "config.yaml").read_text())
    assert (cfg.get("autoresearch") or {}).get("enabled") is False, (
        "config.yaml: autoresearch.enabled must remain false during the OOS "
        "window. Re-enable only after DSR re-evaluation at >=200 closed paper "
        "trades."
    )


def test_params_trend_yaml_not_imported_under_autoresearch():
    """The isolated params.trend.yaml must never be loaded by the autoresearch package.

    A grep across `autoresearch/` for the literal "params.trend" guards the
    invariant: if anyone wires this seed file into the loop, the test fails.
    """
    autoresearch_dir = _BOT_ROOT / "autoresearch"
    bad_hits: list[str] = []
    for path in autoresearch_dir.rglob("*.py"):
        try:
            text = path.read_text()
        except Exception:
            continue
        if "params.trend" in text:
            bad_hits.append(str(path.relative_to(_BOT_ROOT)))
    assert not bad_hits, (
        "autoresearch package references params.trend.yaml — that file is "
        f"human-review only. Hits: {bad_hits}"
    )


def test_params_trend_yaml_not_imported_in_main_or_loop():
    """Belt-and-braces: ensure neither main.py nor autoresearch/loop.py loads it."""
    for rel in ("main.py", "autoresearch/loop.py"):
        text = (_BOT_ROOT / rel).read_text()
        assert "params.trend" not in text, (
            f"{rel} references params.trend.yaml; that file is human-review only."
        )
