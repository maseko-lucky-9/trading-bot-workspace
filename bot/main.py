"""
main.py — bot lifecycle entry point.

Usage:
    python main.py --mode paper [--resume] [--confirm-live]

In paper mode the OrderManager simulates fills against the live bridge
tick. Every loop iteration: get tick -> append to bar buffer -> generate
signal on the latest H1 bars -> size + place order. Auto-checkpoint on
shutdown.
"""
from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

_BOT_ROOT = Path(__file__).resolve().parent
if str(_BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOT_ROOT))

from core.bridge.http_client import MT5BridgeClient  # noqa: E402
from core.checkpoint.state import BotState, CheckpointManager  # noqa: E402
from core.data.history import HistoryFetcher  # noqa: E402
from core.execution.live_broker import LiveBroker, LiveModeNotEnabled  # noqa: E402
from core.execution.order_manager import OrderManager  # noqa: E402
from core.execution.paper_broker import PaperBroker  # noqa: E402
from core.performance.tracker import PerformanceTracker  # noqa: E402
from core.risk.manager import RiskManager  # noqa: E402
from core.strategy.base import Strategy  # noqa: E402
from core.strategy.ema_crossover import EMACrossover  # noqa: E402
from core.strategy.mean_reversion import BollingerBandMeanReversion  # noqa: E402
from autoresearch.loop import AutoresearchLoop  # noqa: E402


_running = True


def _handle_sigint(signum, frame) -> None:
    global _running
    _running = False


def _load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open() as f:
        return yaml.safe_load(f) or {}


def _load_strategy(params: dict) -> Strategy:
    if params.get("strategy") == "mean_reversion":
        return BollingerBandMeanReversion(
            bb_period=int(params.get("bb_period", 20)),
            bb_std=float(params.get("bb_std", 2.0)),
            rsi_period=int(params.get("rsi_period", 14)),
            rsi_oversold=float(params.get("rsi_os", 30.0)),
            rsi_overbought=float(params.get("rsi_ob", 70.0)),
            atr_sl_multiplier=float(params.get("atr_multiplier", 1.5)),
        )
    fast = int(params.get("ema_fast", 9))
    slow = int(params.get("ema_slow", 21))
    if fast >= slow:
        fast, slow = 9, 21
    return EMACrossover(fast=fast, slow=slow)


def _start_autoresearch(loop: AutoresearchLoop, iterations: int) -> threading.Thread:
    t = threading.Thread(
        target=loop.run, kwargs={"max_iterations": iterations}, daemon=True
    )
    t.start()
    print(f"autoresearch started iterations_per_run={iterations}")
    return t


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MT5 Autonomous Trading Bot")
    parser.add_argument("--mode", choices=["paper", "live"], default="paper")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument(
        "--max-seconds",
        type=int,
        default=0,
        help="exit after this many seconds (0 = run forever)",
    )
    args = parser.parse_args(argv)

    cfg = _load_config(_BOT_ROOT / "config.yaml")
    cfg_mode = (cfg.get("bot") or {}).get("mode", "paper")
    if args.mode == "live":
        if not args.confirm_live or cfg_mode != "live":
            print(
                "ERROR: live mode requires --confirm-live AND config.yaml bot.mode=live",
                file=sys.stderr,
            )
            return 2

    cfg["bot"] = {**(cfg.get("bot") or {}), "mode": args.mode}

    ar_cfg = cfg.get("autoresearch") or {}
    ar_enabled = bool(ar_cfg.get("enabled", True))
    ar_iterations = int(ar_cfg.get("iterations_per_run", 5))
    ar_cooldown = float(ar_cfg.get("cooldown_seconds", 3600))

    bridge = MT5BridgeClient()
    bridge.ping()
    history = HistoryFetcher(bridge)
    risk = RiskManager(cfg)
    tracker = PerformanceTracker()
    checkpoints = CheckpointManager()

    autoresearch_loop: AutoresearchLoop | None = AutoresearchLoop() if ar_enabled else None
    autoresearch_thread: threading.Thread | None = None
    autoresearch_last_run: float = 0.0

    strategy = _load_strategy(autoresearch_loop._load_params() if autoresearch_loop else {})

    if ar_enabled and autoresearch_loop is not None:
        autoresearch_thread = _start_autoresearch(autoresearch_loop, ar_iterations)
        autoresearch_last_run = time.time()

    if args.mode == "live":
        try:
            broker = LiveBroker(bridge, cfg)
        except LiveModeNotEnabled as exc:
            print(f"ERROR: live mode not enabled — {exc}", file=sys.stderr)
            return 2
    else:
        broker = PaperBroker(bridge)
    om = OrderManager(cfg, broker, tracker=tracker)

    state = BotState()
    if args.resume:
        loaded = checkpoints.load()
        if loaded is not None:
            state = loaded
            print(f"resumed from checkpoint: iteration={state.iteration}")

    symbols: list[str] = ((cfg.get("bot") or {}).get("instruments") or ["EURUSD"])
    timeframe = (cfg.get("bot") or {}).get("timeframe", "H1")

    signal.signal(signal.SIGINT, _handle_sigint)
    signal.signal(signal.SIGTERM, _handle_sigint)

    started = time.time()
    print(
        f"bot start mode={args.mode} symbols={','.join(symbols)} tf={timeframe} "
        f"strategy={strategy.name} bridge={bridge.base_url}"
    )

    try:
        while _running:
            # Account and circuit breaker check once per iteration
            account = {"balance": 10_000.0, "equity": 10_000.0}
            try:
                live_acct = bridge.get_account()
                if live_acct:
                    account.update(live_acct)
            except Exception:
                pass

            equity = float(account.get("equity", account.get("balance", 10_000.0)))
            if state.peak_equity == 0.0 or equity > state.peak_equity:
                state.peak_equity = equity
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if state.day_start_equity == 0.0 or state.day_start_date != today:
                state.day_start_equity = equity
                state.day_start_date = today

            ok, reason = risk.check_circuit_breakers(
                account,
                om.get_positions(),
                recent_closed=om.get_closed(),
                peak_equity=state.peak_equity,
                day_start_equity=state.day_start_equity,
            )
            if not ok:
                print(f"halted: {reason}")
            else:
                for sym in symbols:
                    try:
                        bridge.get_tick(sym)
                    except Exception as exc:
                        print(f"bridge error {sym}: {exc}", file=sys.stderr)
                        continue

                    df = history.fetch(symbol=sym, timeframe=timeframe, bars=200)
                    sig = strategy.generate_signal(df)

                    if sig.action in ("BUY", "SELL"):
                        volume = risk.size_position(sym, sig, account, df)
                        meta = sig.meta or {}
                        placed = (
                            om.buy(sym, volume, sl=meta.get("sl", 0.0), tp=meta.get("tp", 0.0))
                            if sig.action == "BUY"
                            else om.sell(sym, volume, sl=meta.get("sl", 0.0), tp=meta.get("tp", 0.0))
                        )
                        print(
                            f"order {sig.action} sym={sym} vol={volume} "
                            f"ticket={placed['ticket']} reason={sig.reason}"
                        )

            state.iteration += 1
            state.positions = om.get_positions()
            state.performance_summary = tracker.summary()

            # Reload strategy from params.yaml when autoresearch run completes;
            # restart after cooldown so optimisation continues in the background.
            if ar_enabled and autoresearch_loop is not None:
                if autoresearch_thread is not None and not autoresearch_thread.is_alive():
                    strategy = _load_strategy(autoresearch_loop._load_params())
                    print(
                        f"autoresearch done — strategy reloaded name={strategy.name}"
                    )
                    autoresearch_thread = None
                if autoresearch_thread is None and (time.time() - autoresearch_last_run) >= ar_cooldown:
                    autoresearch_thread = _start_autoresearch(autoresearch_loop, ar_iterations)
                    autoresearch_last_run = time.time()

            if args.max_seconds and (time.time() - started) >= args.max_seconds:
                break
            time.sleep(1)
    finally:
        try:
            checkpoints.save(state)
            checkpoints.rotate(keep=10)
        except Exception as exc:
            print(f"checkpoint save failed: {exc}", file=sys.stderr)
        bridge.close()
        print(f"bot stop iterations={state.iteration} positions={len(state.positions)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
