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
from core.monitoring.position_monitor import PositionMonitor  # noqa: E402
from core.performance.tracker import PerformanceTracker  # noqa: E402
from core.risk.manager import RiskManager  # noqa: E402
from core.strategy.base import Strategy  # noqa: E402
from core.strategy.ema_crossover import EMACrossover  # noqa: E402
from core.strategy.mean_reversion import BollingerBandMeanReversion  # noqa: E402
from core.strategy.trend_following import TrendFollowing  # noqa: E402
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
    if params.get("strategy") == "trend_following":
        return TrendFollowing(
            htf_resample_rule=str(params.get("htf_resample_rule", "4h")),
            swing_left=int(params.get("swing_left", 2)),
            swing_right=int(params.get("swing_right", 2)),
            tp_r_multiple=float(params.get("tp_r_multiple", 1.5)),
            atr_period=int(params.get("atr_period", 14)),
            atr_sl_multiplier=float(params.get("atr_sl_multiplier", 1.5)),
            sl_atr_buffer=float(params.get("sl_atr_buffer", 1.0)),
            reversal_lookback=int(params.get("reversal_lookback", 10)),
            mode=str(params.get("mode", "standard")),
        )
    fast = int(params.get("ema_fast", 9))
    slow = int(params.get("ema_slow", 21))
    if fast >= slow:
        fast, slow = 9, 21
    return EMACrossover(fast=fast, slow=slow)


def _ping_with_backoff(bridge: MT5BridgeClient, max_attempts: int = 5, base_delay: float = 1.0) -> bool:
    """Retry bridge.ping() with exponential backoff. Returns True on success.

    Sequence: 1s, 2s, 4s, 8s, 16s — total ~31s before giving up.
    Logs each retry to stderr so launchd captures the trail in paper.log.
    """
    delay = base_delay
    for attempt in range(1, max_attempts + 1):
        try:
            bridge.ping()
            if attempt > 1:
                print(f"bridge ping ok after {attempt} attempts", file=sys.stderr)
            return True
        except Exception as exc:
            if attempt == max_attempts:
                print(
                    f"bridge ping failed after {max_attempts} attempts: {exc}",
                    file=sys.stderr,
                )
                return False
            print(
                f"bridge ping attempt {attempt}/{max_attempts} failed ({exc}); "
                f"retrying in {delay:.1f}s",
                file=sys.stderr,
            )
            time.sleep(delay)
            delay *= 2
    return False


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
    if not _ping_with_backoff(bridge):
        # Exit non-zero so launchd KeepAlive respawns us after ThrottleInterval.
        # By then the bridge may have come back; if not, we'll loop again.
        return 3
    history = HistoryFetcher(bridge)
    risk = RiskManager(cfg)
    tracker = PerformanceTracker()
    checkpoints = CheckpointManager()

    autoresearch_loop: AutoresearchLoop = AutoresearchLoop()
    autoresearch_thread: threading.Thread | None = None
    autoresearch_last_run: float = 0.0

    strategy = _load_strategy(autoresearch_loop._load_params())

    if ar_enabled:
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

    position_monitor: PositionMonitor | None = None
    if args.mode == "live":
        position_monitor = PositionMonitor(broker, cfg)
        position_monitor.start()
        print("position_monitor started")

    state = BotState()
    if args.resume:
        loaded = checkpoints.load()
        if loaded is not None:
            state = loaded
            if not hasattr(state, "cooling_off_until"):
                state.cooling_off_until = 0.0
            print(f"resumed from checkpoint: iteration={state.iteration}")

    cooling_off_hours = float((cfg.get("risk") or {}).get("cooling_off_hours", 24))
    _tp1_hit_tickets: set[int] = set()

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
            # Cooling-off gate: skip new orders while in the post-loss pause window.
            if args.max_seconds and (time.time() - started) >= args.max_seconds:
                break

            if state.cooling_off_until > 0.0:
                now_ts = time.time()
                if now_ts < state.cooling_off_until:
                    resumes = datetime.fromtimestamp(
                        state.cooling_off_until, tz=timezone.utc
                    ).isoformat()
                    print(f"cooling off — resumes at {resumes}", file=sys.stderr)
                    time.sleep(1)
                    continue
                else:
                    state.cooling_off_until = 0.0

        # Account and circuit breaker check once per iteration.
            # We MUST distinguish fresh account data (real equity from the bridge)
            # from the fallback default — running circuit breakers on the
            # 10_000 fallback after peak_equity has been set from real account
            # data produces a false-positive 90% drawdown halt that latches
            # state.peak_equity at the wrong level forever.
            account = {"balance": 10_000.0, "equity": 10_000.0}
            account_fresh = False
            try:
                live_acct = bridge.get_account()
                if live_acct and "equity" in live_acct:
                    account.update(live_acct)
                    account_fresh = True
            except Exception:
                pass

            if account_fresh:
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
            else:
                # Bridge transient: skip the circuit-breaker pass rather than
                # halt on stale data. Next iteration will re-check.
                ok, reason = True, "skipped (bridge transient)"
                print("skipping circuit-breaker check: stale account data", file=sys.stderr)

            if not ok:
                # DIAGNOSTIC: surface the values driving the halt for offline review.
                print(
                    f"halted: {reason} "
                    f"[equity={float(account.get('equity', 0.0)):.2f} "
                    f"peak={state.peak_equity:.2f} "
                    f"day0={state.day_start_equity:.2f} "
                    f"fresh={account_fresh}]"
                )
                # Set cooling-off period on consecutive-loss halt (first trigger only).
                if "consecutive" in reason and state.cooling_off_until == 0.0:
                    state.cooling_off_until = time.time() + cooling_off_hours * 3600
                    print(
                        f"cooling-off set for {cooling_off_hours}h "
                        f"until {datetime.fromtimestamp(state.cooling_off_until, tz=timezone.utc).isoformat()}"
                    )
            else:
                for sym in symbols:
                    # Outer try wraps the entire per-symbol pipeline so a single
                    # bridge transient (history fetch, broker call, etc.) becomes
                    # a no-op iteration instead of crashing the bot. KeepAlive
                    # respawns are reserved for genuinely fatal errors.
                    try:
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
                    except Exception as exc:
                        # Anything reaching here is a downstream failure (history,
                        # strategy, risk, or order manager). Log and move on; the
                        # next iteration will retry naturally.
                        print(f"iteration error {sym}: {exc}", file=sys.stderr)
                        continue

            # Position management: partial-close at TP1 then move to break-even.
            # Runs regardless of circuit-breaker state (manages existing risk).
            for sym in symbols:
                try:
                    tick = bridge.get_tick(sym)
                    current_bid = float(tick.get("bid", 0.0))
                    current_ask = float(tick.get("ask", 0.0))
                except Exception:
                    continue
                for pos in om.get_positions():
                    if pos.get("symbol") != sym:
                        continue
                    ticket = pos["ticket"]
                    if ticket in _tp1_hit_tickets:
                        continue
                    tp1 = float(pos.get("tp", 0.0))
                    if not tp1:
                        continue
                    side = pos.get("type", "")
                    tp1_reached = (
                        (side == "BUY" and current_bid >= tp1)
                        or (side == "SELL" and current_ask <= tp1)
                    )
                    if tp1_reached:
                        try:
                            om.partial_close(ticket, fraction=0.5)
                            om.set_breakeven(ticket)
                            _tp1_hit_tickets.add(ticket)
                            print(
                                f"tp1 hit ticket={ticket} sym={sym} "
                                f"partial_close=0.5 be_set=True"
                            )
                        except Exception as exc:
                            print(f"position management error ticket={ticket}: {exc}", file=sys.stderr)

            state.iteration += 1
            state.positions = om.get_positions()
            state.performance_summary = tracker.summary()

            # Reload strategy from params.yaml when autoresearch run completes;
            # restart after cooldown so optimisation continues in the background.
            if ar_enabled:
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
        if position_monitor is not None:
            try:
                position_monitor.stop(timeout=2.0)
            except Exception as exc:
                print(f"position_monitor stop failed: {exc}", file=sys.stderr)
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
