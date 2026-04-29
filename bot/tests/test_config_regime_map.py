"""Tests for filters.regime.strategy_regime_map (T07)."""
from __future__ import annotations

from pathlib import Path

import yaml

_BOT_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_PATH = _BOT_ROOT / "config.yaml"


def _load_regime_map() -> dict:
    cfg = yaml.safe_load(_CONFIG_PATH.read_text())
    return ((cfg.get("filters") or {}).get("regime") or {}).get("strategy_regime_map") or {}


def test_existing_strategy_keys_unchanged():
    rm = _load_regime_map()
    # The two pre-existing keys must keep their original regime mappings.
    assert rm.get("ema_crossover") == [0]
    assert rm.get("mean_reversion") == [1]


def test_trend_following_key_present_with_trend_regime():
    rm = _load_regime_map()
    assert "trend_following" in rm, "trend_following must be registered in regime map"
    assert rm["trend_following"] == [0], "trend_following must trade only in TREND regime (0)"


def test_autoresearch_enabled_remains_false():
    # T07 must not have flipped the OOS-window lock.
    cfg = yaml.safe_load(_CONFIG_PATH.read_text())
    assert (cfg.get("autoresearch") or {}).get("enabled") is False, (
        "autoresearch.enabled must remain false during the OOS window"
    )
