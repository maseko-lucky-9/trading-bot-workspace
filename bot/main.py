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
import time
from pathlib import Path

import yaml

_BOT_ROOT = Path(__file__).resolve().parent
if str(_BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOT_ROOT))

from core.bridge.http_client import MT5BridgeClient  # noqa: E402
from core.checkpoint.state import BotState, CheckpointManager  # noqa: E402
from core.data.history import HistoryFetcher  # noqa: E402
from core.execution.order_manager import OrderManager  # noqa: E402
from core.performance.tracker import PerformanceTracker  # noqa: E402
from core.risk.manager import RiskManager  # noqa: E402
from core.strategy.ema_crossover import EMACrossover  # noqa: E402


_running = True


def _handle_sigint(signum, frame) -> None:
    global _running
    _running = False


def _load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open() as f:
        return yaml.safe_load(f) or {}


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

    bridge = MT5BridgeClient()
    bridge.ping()
    history = HistoryFetcher(bridge)
    strategy = EMACrossover()
    risk = RiskManager(cfg)
    tracker = PerformanceTracker()
    om = OrderManager(cfg, bridge)
    checkpoints = CheckpointManager()

    state = BotState()
    if args.resume:
        loaded = checkpoints.load()
        if loaded is not None:
            state = loaded
            print(f"resumed from checkpoint: iteration={state.iteration}")

    symbol = ((cfg.get("bot") or {}).get("instruments") or ["EURUSD"])[0]
    timeframe = (cfg.get("bot") or {}).get("timeframe", "H1")

    signal.signal(signal.SIGINT, _handle_sigint)
    signal.signal(signal.SIGTERM, _handle_sigint)

    started = time.time()
    print(
        f"bot start mode={args.mode} symbol={symbol} tf={timeframe} "
        f"bridge={bridge.base_url}"
    )

    try:
        while _running:
            try:
                tick = bridge.get_tick(symbol)
            except Exception as exc:
                print(f"bridge error: {exc}", file=sys.stderr)
                time.sleep(1)
                continue

            df = history.fetch(symbol=symbol, timeframe=timeframe, bars=200)
            sig = strategy.generate_signal(df)

            account = {"balance": 10_000.0, "equity": 10_000.0}
            try:
                live_acct = bridge.get_account()
                if live_acct:
                    account.update(live_acct)
            except Exception:
                pass

            ok, reason = risk.check_circuit_breakers(account, om.get_positions())
            if not ok:
                print(f"halted: {reason}")
            elif sig.action in ("BUY", "SELL"):
                volume = risk.size_position(symbol, sig, account, df)
                meta = sig.meta or {}
                placed = (
                    om.buy(symbol, volume, sl=meta.get("sl", 0.0), tp=meta.get("tp", 0.0))
                    if sig.action == "BUY"
                    else om.sell(symbol, volume, sl=meta.get("sl", 0.0), tp=meta.get("tp", 0.0))
                )
                print(
                    f"order {sig.action} vol={volume} "
                    f"ticket={placed['ticket']} reason={sig.reason}"
                )

            state.iteration += 1
            state.positions = om.get_positions()
            state.performance_summary = tracker.summary()

            if args.max_seconds and (time.time() - started) >= args.max_seconds:
                break
            time.sleep(1)
    finally:
        try:
            checkpoints.save(state)
        except Exception as exc:
            print(f"checkpoint save failed: {exc}", file=sys.stderr)
        bridge.close()
        print(f"bot stop iterations={state.iteration} positions={len(state.positions)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
