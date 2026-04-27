"""Tests for SessionFilter and NewsBlackout (Wave 3 — forex session filter)."""
from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd
import pytest

# Canonical location
from core.filters.session import SessionFilter
# Legacy import path must also work (backward-compat shim)
from core.strategy.session_filter import SessionFilter as SessionFilterLegacy
from core.filters.news import NewsBlackout


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ts(hour: int, minute: int = 0) -> pd.Timestamp:
    return pd.Timestamp(f"2026-04-15 {hour:02d}:{minute:02d}:00", tz="UTC")


def _write_calendar(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "news_calendar.csv"
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["time", "impact", "currency", "event"])
        writer.writeheader()
        writer.writerows(rows)
    return path


# ─────────────────────────────────────────────────────────────────────────────
# SessionFilter — legacy keyword constructor (backward compat)
# ─────────────────────────────────────────────────────────────────────────────

class TestLegacyConstructor:
    def test_disabled_always_returns_true(self):
        sf = SessionFilter(sessions=["london"], enabled=False)
        for h in range(24):
            assert sf.is_trading_hour(_ts(h)) is True

    def test_enabled_london(self):
        sf = SessionFilter(sessions=["london"], enabled=True)
        assert sf.is_trading_hour(_ts(8)) is True
        assert sf.is_trading_hour(_ts(15)) is True
        assert sf.is_trading_hour(_ts(17)) is False
        assert sf.is_trading_hour(_ts(5)) is False

    def test_new_york(self):
        sf = SessionFilter(sessions=["new_york"], enabled=True)
        assert sf.is_trading_hour(_ts(14)) is True
        assert sf.is_trading_hour(_ts(22)) is False

    def test_sydney_overnight(self):
        sf = SessionFilter(sessions=["sydney"], enabled=True)
        assert sf.is_trading_hour(_ts(22)) is True
        assert sf.is_trading_hour(_ts(3)) is True
        assert sf.is_trading_hour(_ts(10)) is False

    def test_multi_session_overlap(self):
        sf = SessionFilter(sessions=["london", "new_york"], enabled=True)
        assert sf.is_trading_hour(_ts(13)) is True

    def test_integer_bar_index_always_true(self):
        sf = SessionFilter(sessions=["london"], enabled=True)
        assert sf.is_trading_hour(5) is True

    def test_unknown_session_name_ignored_passes_all(self):
        sf = SessionFilter(sessions=["nonexistent_session"], enabled=True)
        # Unknown names → empty _ranges → no restriction → all hours pass
        assert sf.is_trading_hour(_ts(12)) is True

    def test_naive_datetime_uses_wall_clock_hour(self):
        from datetime import datetime
        sf = SessionFilter(sessions=["london"], enabled=True)
        dt = datetime(2026, 1, 5, 9, 0, 0)
        assert sf.is_trading_hour(dt) is True

    def test_active_sessions_property(self):
        sf = SessionFilter(sessions=["tokyo"], enabled=True)
        assert "tokyo" in sf.active_sessions

    def test_legacy_import_path_works(self):
        sf = SessionFilterLegacy(sessions=["london"], enabled=True)
        assert sf.is_trading_hour(_ts(10)) is True


# ─────────────────────────────────────────────────────────────────────────────
# SessionFilter — config-dict constructor (canonical)
# ─────────────────────────────────────────────────────────────────────────────

class TestConfigDictConstructor:
    def test_disabled_passes_all(self):
        sf = SessionFilter({"enabled": False})
        for h in range(24):
            assert sf.is_active(_ts(h))

    def test_empty_allowed_passes_all(self):
        sf = SessionFilter({"enabled": True, "allowed": []})
        for h in range(24):
            assert sf.is_active(_ts(h))

    def test_london_in(self):
        sf = SessionFilter({"enabled": True, "allowed": ["london"]})
        for h in range(7, 16):
            assert sf.is_active(_ts(h))

    def test_london_out(self):
        sf = SessionFilter({"enabled": True, "allowed": ["london"]})
        for h in list(range(0, 7)) + list(range(16, 24)):
            assert not sf.is_active(_ts(h))

    def test_new_york_in(self):
        sf = SessionFilter({"enabled": True, "allowed": ["new_york"]})
        for h in range(12, 21):
            assert sf.is_active(_ts(h))

    def test_sydney_crosses_midnight(self):
        sf = SessionFilter({"enabled": True, "allowed": ["sydney"]})
        for h in [22, 23] + list(range(0, 7)):
            assert sf.is_active(_ts(h)), f"hour {h}"
        for h in range(7, 22):
            assert not sf.is_active(_ts(h)), f"hour {h}"

    def test_london_newyork_dead_zone_blocked(self):
        sf = SessionFilter({"enabled": True, "allowed": ["london", "new_york"]})
        for h in list(range(0, 7)) + list(range(21, 24)):
            assert not sf.is_active(_ts(h))

    def test_integer_passes(self):
        sf = SessionFilter({"enabled": True, "allowed": ["london"]})
        assert sf.is_active(0)
        assert sf.is_active(9999)

    def test_custom_ranges(self):
        sf = SessionFilter({"enabled": True, "allowed": [], "custom_ranges": [[9, 11]]})
        assert sf.is_active(_ts(9))
        assert sf.is_active(_ts(10))
        assert not sf.is_active(_ts(11))

    def test_is_trading_hour_alias(self):
        sf = SessionFilter({"enabled": True, "allowed": ["london"]})
        assert sf.is_trading_hour(_ts(10)) == sf.is_active(_ts(10))


# ─────────────────────────────────────────────────────────────────────────────
# SessionFilter — from_config
# ─────────────────────────────────────────────────────────────────────────────

class TestFromConfig:
    def test_reads_filters_sessions_key(self):
        cfg = {"filters": {"sessions": {"enabled": True, "allowed": ["london"]}}}
        sf = SessionFilter.from_config(cfg)
        assert sf.is_active(_ts(10))
        assert not sf.is_active(_ts(3))

    def test_legacy_strategy_config_key(self):
        cfg = {"strategy_config": {"session_filter": {"enabled": True, "sessions": ["tokyo"]}}}
        sf = SessionFilter.from_config(cfg)
        assert sf.enabled is True
        assert "tokyo" in sf.active_sessions

    def test_legacy_disabled_by_default(self):
        sf = SessionFilter.from_config({})
        assert sf.enabled is False   # legacy default

    def test_canonical_key_takes_precedence(self):
        cfg = {
            "filters": {"sessions": {"enabled": True, "allowed": ["london"]}},
            "strategy_config": {"session_filter": {"enabled": False, "sessions": ["tokyo"]}},
        }
        sf = SessionFilter.from_config(cfg)
        # canonical path wins
        assert sf.enabled is True
        assert "london" in sf.active_sessions


# ─────────────────────────────────────────────────────────────────────────────
# NewsBlackout
# ─────────────────────────────────────────────────────────────────────────────

class TestNewsBlackoutDisabled:
    def test_disabled_passes_any_time(self):
        nb = NewsBlackout({"enabled": False})
        assert nb.is_active(_ts(14))

    def test_enabled_no_calendar_passes_all(self):
        nb = NewsBlackout({"enabled": True, "buffer_minutes": 30})
        assert nb.is_active(_ts(14))


class TestNewsBlackoutBlocking:
    def test_blocks_within_buffer(self, tmp_path):
        cal = _write_calendar(tmp_path, [
            {"time": "2026-04-15 14:00:00+00:00", "impact": "High", "currency": "GBP", "event": "CPI"},
        ])
        nb = NewsBlackout({"enabled": True, "buffer_minutes": 30, "calendar_path": str(cal)})
        assert not nb.is_active(_ts(14, 0))
        assert not nb.is_active(_ts(13, 45))
        assert not nb.is_active(_ts(14, 29))

    def test_passes_outside_buffer(self, tmp_path):
        cal = _write_calendar(tmp_path, [
            {"time": "2026-04-15 14:00:00+00:00", "impact": "High", "currency": "GBP", "event": "CPI"},
        ])
        nb = NewsBlackout({"enabled": True, "buffer_minutes": 30, "calendar_path": str(cal)})
        assert nb.is_active(_ts(13, 29))
        assert nb.is_active(_ts(14, 31))

    def test_ignores_non_high_impact(self, tmp_path):
        cal = _write_calendar(tmp_path, [
            {"time": "2026-04-15 14:00:00+00:00", "impact": "Medium", "currency": "GBP", "event": "PMI"},
            {"time": "2026-04-15 14:00:00+00:00", "impact": "Low", "currency": "USD", "event": "Misc"},
        ])
        nb = NewsBlackout({"enabled": True, "buffer_minutes": 30, "calendar_path": str(cal)})
        assert nb.is_active(_ts(14, 0))

    def test_currency_filter_blocks_matching_symbol(self, tmp_path):
        cal = _write_calendar(tmp_path, [
            {"time": "2026-04-15 14:00:00+00:00", "impact": "High", "currency": "GBP", "event": "Rate"},
        ])
        nb = NewsBlackout({"enabled": True, "buffer_minutes": 30, "calendar_path": str(cal)})
        assert not nb.is_active(_ts(14), symbol="GBPUSD")
        assert nb.is_active(_ts(14), symbol="EURUSD")

    def test_empty_symbol_blocks_all_currencies(self, tmp_path):
        cal = _write_calendar(tmp_path, [
            {"time": "2026-04-15 14:00:00+00:00", "impact": "High", "currency": "JPY", "event": "BoJ"},
        ])
        nb = NewsBlackout({"enabled": True, "buffer_minutes": 30, "calendar_path": str(cal)})
        assert not nb.is_active(_ts(14), symbol="")

    def test_missing_calendar_file_passes_all(self, tmp_path):
        nb = NewsBlackout(
            {"enabled": True, "buffer_minutes": 30, "calendar_path": str(tmp_path / "missing.csv")},
            bot_root=tmp_path,
        )
        assert nb.is_active(_ts(14))

    def test_from_config(self, tmp_path):
        cal = _write_calendar(tmp_path, [
            {"time": "2026-04-15 14:00:00+00:00", "impact": "High", "currency": "USD", "event": "NFP"},
        ])
        cfg = {"filters": {"news_blackout": {
            "enabled": True, "buffer_minutes": 60, "calendar_path": str(cal),
        }}}
        nb = NewsBlackout.from_config(cfg)
        assert not nb.is_active(_ts(14), symbol="EURUSD")


# ─────────────────────────────────────────────────────────────────────────────
# Engine integration — session filter gates entries without crashing
# ─────────────────────────────────────────────────────────────────────────────

class TestEngineSessionIntegration:
    def _run_engine(self, session_cfg: dict) -> int:
        import subprocess, sys, yaml
        bot_root = Path(__file__).resolve().parents[1]
        cfg = yaml.safe_load((bot_root / "config.yaml").read_text())
        cfg.setdefault("filters", {})["sessions"] = session_cfg
        tmp_cfg = Path("/tmp/_test_session_engine.yaml")
        tmp_cfg.write_text(yaml.dump(cfg))
        r = subprocess.run(
            [sys.executable, str(bot_root / "backtest" / "engine.py"),
             "--params", str(bot_root / "autoresearch" / "params.yaml"),
             "--metric", "sharpe",
             "--symbol", "GBPUSD", "--timeframe", "M15", "--bars", "500",
             "--config", str(tmp_cfg)],
            capture_output=True, text=True, cwd=str(bot_root),
        )
        assert r.returncode == 0, f"engine crashed:\n{r.stderr[:400]}"
        return r.returncode

    def test_all_sessions_enabled(self):
        self._run_engine({
            "enabled": True,
            "allowed": ["london", "new_york", "tokyo", "sydney"],
        })

    def test_session_filter_disabled(self):
        self._run_engine({"enabled": False})

    def test_single_session_london_only(self):
        self._run_engine({"enabled": True, "allowed": ["london"]})
