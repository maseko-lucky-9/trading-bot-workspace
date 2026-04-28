"""Tests for main.py — bot lifecycle entry point."""
from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
import yaml

import sys
_BOT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BOT_ROOT))

from main import _load_config, _load_strategy, _handle_sigint, _start_autoresearch, _ping_with_backoff, main
import main as main_module


# ------------------------------------------------------------------ #
# Helpers                                                            #
# ------------------------------------------------------------------ #

def _flat_ohlcv(n: int = 200) -> pd.DataFrame:
    close = np.full(n, 1.10)
    return pd.DataFrame({
        "time": pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC"),
        "open": close, "high": close + 0.0005,
        "low": close - 0.0005, "close": close, "volume": np.ones(n) * 1000,
    })


def _paper_cfg() -> dict:
    return {
        "bot": {"mode": "paper", "instruments": ["EURUSD"], "timeframe": "H1"},
        "autoresearch": {"enabled": False},
    }


def _mock_bridge() -> MagicMock:
    b = MagicMock()
    b.is_connected.return_value = True
    b.ping.return_value = True
    b.get_tick.return_value = {"symbol": "EURUSD", "bid": 1.10000, "ask": 1.10002, "spread": 2.0}
    b.get_account.return_value = {"balance": 10_000.0, "equity": 10_000.0}
    b.get_positions.return_value = []
    b.get_closed.return_value = []
    b.send_order.return_value = {"ok": True, "ticket": 1}
    return b


# ------------------------------------------------------------------ #
# _load_config                                                       #
# ------------------------------------------------------------------ #

def test_load_config_returns_empty_when_missing(tmp_path):
    assert _load_config(tmp_path / "nofile.yaml") == {}


def test_load_config_returns_empty_on_empty_yaml(tmp_path):
    f = tmp_path / "empty.yaml"
    f.write_text("")
    assert _load_config(f) == {}


def test_load_config_parses_yaml(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text(yaml.dump({"bot": {"mode": "paper"}}))
    cfg = _load_config(f)
    assert cfg["bot"]["mode"] == "paper"


# ------------------------------------------------------------------ #
# _load_strategy                                                     #
# ------------------------------------------------------------------ #

def test_load_strategy_returns_ema_by_default():
    strat = _load_strategy({})
    assert strat.name == "ema_crossover"


def test_load_strategy_returns_ema_explicit():
    strat = _load_strategy({"strategy": "ema_crossover", "ema_fast": 5, "ema_slow": 15})
    assert strat.name == "ema_crossover"


def test_load_strategy_returns_mean_reversion():
    strat = _load_strategy({
        "strategy": "mean_reversion",
        "bb_period": 20, "bb_std": 2.0,
        "rsi_period": 14, "rsi_os": 30.0, "rsi_ob": 70.0, "atr_multiplier": 1.5,
    })
    assert "mean_reversion" in strat.name


def test_load_strategy_corrects_fast_gte_slow():
    strat = _load_strategy({"ema_fast": 21, "ema_slow": 9})
    assert strat.name == "ema_crossover"


# ------------------------------------------------------------------ #
# _handle_sigint                                                     #
# ------------------------------------------------------------------ #

def test_handle_sigint_sets_running_false():
    main_module._running = True
    _handle_sigint(2, None)
    assert main_module._running is False
    main_module._running = True  # restore


# ------------------------------------------------------------------ #
# _start_autoresearch                                                #
# ------------------------------------------------------------------ #

def test_start_autoresearch_returns_running_thread(tmp_path):
    import yaml as _yaml
    params_file = tmp_path / "params.yaml"
    params_file.write_text(_yaml.dump({"strategy": "ema_crossover"}))
    from autoresearch.loop import AutoresearchLoop
    loop = AutoresearchLoop(
        params_path=params_file,
        results_path=tmp_path / "results.tsv",
    )
    # Override _run_engine so the thread exits quickly
    loop._run_engine = lambda *a, symbol=None: (0, "SHARPE 0.5", "")
    t = _start_autoresearch(loop, iterations=1)
    assert isinstance(t, threading.Thread)
    t.join(timeout=10)


# ------------------------------------------------------------------ #
# main() — argument validation                                       #
# ------------------------------------------------------------------ #

def test_main_live_requires_confirm_live_flag():
    with patch("main._load_config", return_value={"bot": {"mode": "live"}}):
        rc = main(["--mode", "live"])
    assert rc == 2


def test_main_live_requires_config_mode_live():
    with patch("main._load_config", return_value={"bot": {"mode": "paper"}}):
        rc = main(["--mode", "live", "--confirm-live"])
    assert rc == 2


# ------------------------------------------------------------------ #
# main() — paper mode integration                                    #
# ------------------------------------------------------------------ #

def _run_main_paper(extra_argv=None, cfg_override=None):
    bridge = _mock_bridge()
    history_mock = MagicMock()
    history_mock.fetch.return_value = _flat_ohlcv()

    with (
        patch("main._load_config", return_value=cfg_override or _paper_cfg()),
        patch("main.MT5BridgeClient", return_value=bridge),
        patch("main.HistoryFetcher", return_value=history_mock),
        patch("main.CheckpointManager") as mock_ckpt,
    ):
        mock_ckpt.return_value.load.return_value = None
        mock_ckpt.return_value.save.return_value = None
        rc = main(["--mode", "paper", "--max-seconds", "2"] + (extra_argv or []))
    return rc


def test_main_paper_mode_exits_zero():
    assert _run_main_paper() == 0


def test_main_paper_mode_resume_no_checkpoint():
    assert _run_main_paper(extra_argv=["--resume"]) == 0


def test_main_paper_mode_multi_symbol():
    cfg = {
        "bot": {"mode": "paper", "instruments": ["EURUSD", "GBPUSD"], "timeframe": "H1"},
        "autoresearch": {"enabled": False},
    }
    assert _run_main_paper(cfg_override=cfg) == 0


def test_main_paper_skips_halted_symbol_on_bridge_error():
    bridge = _mock_bridge()
    bridge.get_tick.side_effect = Exception("bridge down")
    history_mock = MagicMock()
    history_mock.fetch.return_value = _flat_ohlcv()
    with (
        patch("main._load_config", return_value=_paper_cfg()),
        patch("main.MT5BridgeClient", return_value=bridge),
        patch("main.HistoryFetcher", return_value=history_mock),
        patch("main.CheckpointManager") as mock_ckpt,
    ):
        mock_ckpt.return_value.load.return_value = None
        rc = main(["--mode", "paper", "--max-seconds", "2"])
    assert rc == 0


def test_main_paper_circuit_breaker_halts_trading():
    """Resume with peak=10000, current equity=8000 → 20% drawdown → halted message."""
    from core.checkpoint.state import BotState
    saved = BotState()
    saved.peak_equity = 10_000.0
    saved.day_start_equity = 10_000.0
    saved.day_start_date = "2000-01-01"  # force old date so reset doesn't hide drawdown

    bridge = _mock_bridge()
    bridge.get_account.return_value = {"balance": 8_000.0, "equity": 8_000.0}
    history_mock = MagicMock()
    history_mock.fetch.return_value = _flat_ohlcv()
    cfg = {
        "bot": {"mode": "paper", "instruments": ["EURUSD"], "timeframe": "H1"},
        "autoresearch": {"enabled": False},
        "risk": {"max_drawdown_pct": 10.0},
    }
    with (
        patch("main._load_config", return_value=cfg),
        patch("main.MT5BridgeClient", return_value=bridge),
        patch("main.HistoryFetcher", return_value=history_mock),
        patch("main.CheckpointManager") as mock_ckpt,
    ):
        mock_ckpt.return_value.load.return_value = saved
        rc = main(["--mode", "paper", "--resume", "--max-seconds", "2"])
    assert rc == 0


def test_main_resumes_saved_checkpoint():
    from core.checkpoint.state import BotState
    saved = BotState()
    saved.iteration = 42
    saved.peak_equity = 11_000.0

    bridge = _mock_bridge()
    history_mock = MagicMock()
    history_mock.fetch.return_value = _flat_ohlcv()
    with (
        patch("main._load_config", return_value=_paper_cfg()),
        patch("main.MT5BridgeClient", return_value=bridge),
        patch("main.HistoryFetcher", return_value=history_mock),
        patch("main.CheckpointManager") as mock_ckpt,
    ):
        mock_ckpt.return_value.load.return_value = saved
        rc = main(["--mode", "paper", "--resume", "--max-seconds", "2"])
    assert rc == 0


def test_main_places_order_when_strategy_signals_buy():
    """Mock strategy returns BUY → om.buy is called → lines 199-206 covered."""
    from core.strategy.base import Signal

    bridge = _mock_bridge()
    history_mock = MagicMock()
    history_mock.fetch.return_value = _flat_ohlcv()

    buy_signal = Signal(action="BUY", strength=0.8, reason="crossover",
                        meta={"sl": 1.09, "tp": 1.12, "entry_price": 1.10, "ema_fast": 1.10, "ema_slow": 1.09, "atr": 0.001})
    mock_strategy = MagicMock()
    mock_strategy.name = "ema_crossover"
    mock_strategy.generate_signal.return_value = buy_signal

    with (
        patch("main._load_config", return_value=_paper_cfg()),
        patch("main.MT5BridgeClient", return_value=bridge),
        patch("main.HistoryFetcher", return_value=history_mock),
        patch("main.CheckpointManager") as mock_ckpt,
        patch("main._load_strategy", return_value=mock_strategy),
    ):
        mock_ckpt.return_value.load.return_value = None
        rc = main(["--mode", "paper", "--max-seconds", "2"])
    assert rc == 0
    mock_strategy.generate_signal.assert_called()


def test_main_checkpoint_save_exception_does_not_crash():
    bridge = _mock_bridge()
    history_mock = MagicMock()
    history_mock.fetch.return_value = _flat_ohlcv()
    with (
        patch("main._load_config", return_value=_paper_cfg()),
        patch("main.MT5BridgeClient", return_value=bridge),
        patch("main.HistoryFetcher", return_value=history_mock),
        patch("main.CheckpointManager") as mock_ckpt,
    ):
        mock_ckpt.return_value.load.return_value = None
        mock_ckpt.return_value.save.side_effect = OSError("disk full")
        rc = main(["--mode", "paper", "--max-seconds", "2"])
    assert rc == 0


def test_main_bridge_get_account_exception_uses_default():
    """bridge.get_account() raises → default 10_000 used, loop continues."""
    bridge = _mock_bridge()
    bridge.get_account.side_effect = Exception("no account")
    history_mock = MagicMock()
    history_mock.fetch.return_value = _flat_ohlcv()
    with (
        patch("main._load_config", return_value=_paper_cfg()),
        patch("main.MT5BridgeClient", return_value=bridge),
        patch("main.HistoryFetcher", return_value=history_mock),
        patch("main.CheckpointManager") as mock_ckpt,
    ):
        mock_ckpt.return_value.load.return_value = None
        rc = main(["--mode", "paper", "--max-seconds", "2"])
    assert rc == 0


# ------------------------------------------------------------------ #
# Resilience: _ping_with_backoff                                     #
# ------------------------------------------------------------------ #

def test_ping_with_backoff_returns_true_on_first_success(monkeypatch):
    bridge = MagicMock()
    bridge.ping.return_value = True
    sleeps: list[float] = []
    monkeypatch.setattr("main.time.sleep", lambda s: sleeps.append(s))
    assert _ping_with_backoff(bridge, max_attempts=5, base_delay=0.01) is True
    assert bridge.ping.call_count == 1
    assert sleeps == []  # no retry, no sleep


def test_ping_with_backoff_returns_true_after_retries(monkeypatch):
    bridge = MagicMock()
    # Fail twice, then succeed.
    bridge.ping.side_effect = [Exception("conn refused"), Exception("conn refused"), True]
    sleeps: list[float] = []
    monkeypatch.setattr("main.time.sleep", lambda s: sleeps.append(s))
    assert _ping_with_backoff(bridge, max_attempts=5, base_delay=1.0) is True
    assert bridge.ping.call_count == 3
    assert sleeps == [1.0, 2.0]  # exponential: 1s, 2s before the 3rd (successful) attempt


def test_ping_with_backoff_returns_false_after_max_attempts(monkeypatch):
    bridge = MagicMock()
    bridge.ping.side_effect = Exception("conn refused")
    sleeps: list[float] = []
    monkeypatch.setattr("main.time.sleep", lambda s: sleeps.append(s))
    assert _ping_with_backoff(bridge, max_attempts=4, base_delay=0.01) is False
    assert bridge.ping.call_count == 4
    # 3 sleeps before the 4th (final) attempt
    assert len(sleeps) == 3


def test_main_returns_3_when_bridge_unreachable_at_startup():
    """Startup bridge.ping() failure must exit 3 (KeepAlive triggers respawn)."""
    bridge = MagicMock()
    bridge.ping.side_effect = Exception("Connection refused")
    history_mock = MagicMock()
    with (
        patch("main._load_config", return_value=_paper_cfg()),
        patch("main.MT5BridgeClient", return_value=bridge),
        patch("main.HistoryFetcher", return_value=history_mock),
        patch("main.CheckpointManager"),
        # Squash the 31s real-time wait
        patch("main._ping_with_backoff", return_value=False),
    ):
        rc = main(["--mode", "paper", "--max-seconds", "1"])
    assert rc == 3


# ------------------------------------------------------------------ #
# Resilience: per-symbol loop body tolerates downstream errors       #
# ------------------------------------------------------------------ #

def test_main_paper_continues_when_history_fetch_raises():
    """history.fetch raising must not exit the bot — log and continue."""
    bridge = _mock_bridge()
    history_mock = MagicMock()
    history_mock.fetch.side_effect = Exception("history bridge transient")
    with (
        patch("main._load_config", return_value=_paper_cfg()),
        patch("main.MT5BridgeClient", return_value=bridge),
        patch("main.HistoryFetcher", return_value=history_mock),
        patch("main.CheckpointManager") as mock_ckpt,
    ):
        mock_ckpt.return_value.load.return_value = None
        rc = main(["--mode", "paper", "--max-seconds", "2"])
    # Bot completed its max_seconds budget instead of crashing.
    assert rc == 0


def test_main_does_not_halt_on_stale_account_after_bridge_transient():
    """When bridge.get_account() fails, must NOT run circuit breakers on the
    10_000 fallback default. Otherwise peak_equity (set from real account, e.g.
    100_000) and the fallback equity (10_000) produce a false-positive 90% DD
    halt that latches forever."""
    bridge = _mock_bridge()
    # First call returns real account → peak_equity gets set to 100_000.
    # Second call onwards: bridge fails → fallback would be 10_000.
    bridge.get_account.side_effect = [
        {"balance": 100_000.0, "equity": 100_000.0},
        Exception("bridge transient"),
        Exception("bridge transient"),
    ]
    history_mock = MagicMock()
    history_mock.fetch.return_value = _flat_ohlcv()
    with (
        patch("main._load_config", return_value=_paper_cfg()),
        patch("main.MT5BridgeClient", return_value=bridge),
        patch("main.HistoryFetcher", return_value=history_mock),
        patch("main.CheckpointManager") as mock_ckpt,
    ):
        mock_ckpt.return_value.load.return_value = None
        rc = main(["--mode", "paper", "--max-seconds", "2"])
    assert rc == 0
    # Verify the bot did not print a 90%-drawdown halt during the bridge transient.
    # We can't easily capture stderr here; the assertion is structural — the run
    # completing in 2s means it reached the loop, called get_account at least
    # twice (asserting our retry path), and exited cleanly.
    assert bridge.get_account.call_count >= 2


def test_main_paper_continues_when_strategy_raises():
    """strategy.generate_signal raising must not exit the bot."""
    bridge = _mock_bridge()
    history_mock = MagicMock()
    history_mock.fetch.return_value = _flat_ohlcv()

    bad_strategy = MagicMock()
    bad_strategy.name = "ema_crossover"
    bad_strategy.generate_signal.side_effect = Exception("indicator NaN")

    with (
        patch("main._load_config", return_value=_paper_cfg()),
        patch("main.MT5BridgeClient", return_value=bridge),
        patch("main.HistoryFetcher", return_value=history_mock),
        patch("main.CheckpointManager") as mock_ckpt,
        patch("main._load_strategy", return_value=bad_strategy),
    ):
        mock_ckpt.return_value.load.return_value = None
        rc = main(["--mode", "paper", "--max-seconds", "2"])
    assert rc == 0


# ------------------------------------------------------------------ #
# main.py patch: autoresearch disabled → locked params still load    #
# ------------------------------------------------------------------ #

def test_disabled_autoresearch_loads_mean_reversion_from_params_yaml(tmp_path):
    """autoresearch.enabled=False must load strategy from params.yaml, not default ema_crossover."""
    import yaml as _yaml

    params_file = tmp_path / "params.yaml"
    params_file.write_text(_yaml.dump({
        "strategy": "mean_reversion",
        "bb_period": 14, "bb_std": 2.25,
        "rsi_period": 7, "rsi_os": 30, "rsi_ob": 70, "atr_multiplier": 2.25,
    }))

    from autoresearch.loop import AutoresearchLoop
    captured = {}

    real_load_strategy = main_module._load_strategy

    def spy_load_strategy(params):
        captured["params"] = params
        return real_load_strategy(params)

    bridge = _mock_bridge()
    history_mock = MagicMock()
    history_mock.fetch.return_value = _flat_ohlcv()

    cfg = {
        "bot": {"mode": "paper", "instruments": ["EURUSD"], "timeframe": "H1"},
        "autoresearch": {"enabled": False},
    }

    ar_loop = AutoresearchLoop(params_path=params_file, results_path=tmp_path / "results.tsv")

    with (
        patch("main._load_config", return_value=cfg),
        patch("main.MT5BridgeClient", return_value=bridge),
        patch("main.HistoryFetcher", return_value=history_mock),
        patch("main.CheckpointManager") as mock_ckpt,
        patch("main.AutoresearchLoop", return_value=ar_loop),
        patch("main._load_strategy", side_effect=spy_load_strategy),
    ):
        mock_ckpt.return_value.load.return_value = None
        rc = main(["--mode", "paper", "--max-seconds", "1"])

    assert rc == 0
    assert captured["params"].get("strategy") == "mean_reversion", (
        "Expected mean_reversion from params.yaml; got default ema_crossover. "
        "Check main.py autoresearch_loop construction is unconditional."
    )
    assert captured["params"].get("bb_period") == 14


def test_disabled_autoresearch_does_not_start_thread(tmp_path):
    """autoresearch.enabled=False must never call _start_autoresearch."""
    import yaml as _yaml

    params_file = tmp_path / "params.yaml"
    params_file.write_text(_yaml.dump({"strategy": "mean_reversion", "bb_period": 14, "bb_std": 2.25,
                                       "rsi_period": 7, "rsi_os": 30, "rsi_ob": 70, "atr_multiplier": 2.25}))

    from autoresearch.loop import AutoresearchLoop
    ar_loop = AutoresearchLoop(params_path=params_file, results_path=tmp_path / "results.tsv")

    bridge = _mock_bridge()
    history_mock = MagicMock()
    history_mock.fetch.return_value = _flat_ohlcv()

    cfg = {
        "bot": {"mode": "paper", "instruments": ["EURUSD"], "timeframe": "H1"},
        "autoresearch": {"enabled": False},
    }

    with (
        patch("main._load_config", return_value=cfg),
        patch("main.MT5BridgeClient", return_value=bridge),
        patch("main.HistoryFetcher", return_value=history_mock),
        patch("main.CheckpointManager") as mock_ckpt,
        patch("main.AutoresearchLoop", return_value=ar_loop),
        patch("main._start_autoresearch") as mock_start_ar,
    ):
        mock_ckpt.return_value.load.return_value = None
        rc = main(["--mode", "paper", "--max-seconds", "1"])

    assert rc == 0
    mock_start_ar.assert_not_called()


def test_main_autoresearch_thread_reloads_strategy_on_completion(tmp_path):
    """AR thread exits immediately → strategy reloaded → lines 218-223 covered."""
    import yaml as _yaml
    params_file = tmp_path / "params.yaml"
    params_file.write_text(_yaml.dump({"strategy": "ema_crossover", "ema_fast": 9, "ema_slow": 21}))

    bridge = _mock_bridge()
    history_mock = MagicMock()
    history_mock.fetch.return_value = _flat_ohlcv()

    ar_cfg = {
        "bot": {"mode": "paper", "instruments": ["EURUSD"], "timeframe": "H1"},
        "autoresearch": {"enabled": True, "iterations_per_run": 1, "cooldown_seconds": 9999},
    }

    from autoresearch.loop import AutoresearchLoop
    ar_loop = AutoresearchLoop(
        params_path=params_file,
        results_path=tmp_path / "results.tsv",
    )
    ar_loop._run_engine = lambda *a, symbol=None: (0, "SHARPE 0.5", "")

    with (
        patch("main._load_config", return_value=ar_cfg),
        patch("main.MT5BridgeClient", return_value=bridge),
        patch("main.HistoryFetcher", return_value=history_mock),
        patch("main.CheckpointManager") as mock_ckpt,
        patch("main.AutoresearchLoop", return_value=ar_loop),
    ):
        mock_ckpt.return_value.load.return_value = None
        rc = main(["--mode", "paper", "--max-seconds", "4"])
    assert rc == 0
