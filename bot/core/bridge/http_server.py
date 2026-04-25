"""
HTTP Bridge Server — macOS side.
The MT5 EA (Windows VM) POSTs price/account data here and GETs pending commands.
No shared folders, no SMB, no sockets DLL required.

Run:  python core/bridge/http_server.py
EA connects to:  http://192.168.64.1:8080
"""
import json
import random
import time
import threading
from collections import deque
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Timeframe -> seconds per bar for synthetic history
_TF_SECONDS = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800,
    "H1": 3600, "H4": 14400, "D1": 86400,
}

app = FastAPI(title="MT5 Bridge")

# In-memory store — written to disk for the bot to read
DATA_DIR = Path(__file__).parent.parent.parent / "bridge_data"
DATA_DIR.mkdir(exist_ok=True)

_state = {
    "tick":     {},
    "account":  {},
    "heartbeat": 0,
    "positions": [],
}
_command_queue: deque = deque(maxlen=10)
_result_log: list = []
_lock = threading.Lock()

# Rolling H1 bar buffer keyed by symbol — populated from EA tick pushes.
# Serves real history once the EA is live; synthetic fallback otherwise.
_H1_MAX_BARS = 1000
_h1_bars: dict[str, deque] = {}   # symbol -> deque of OHLCV dicts
_h1_open_bar: dict[str, dict] = {}  # symbol -> in-progress bar


# ------------------------------------------------------------------ #
# EA → Server  (EA pushes data on every tick / timer)                #
# ------------------------------------------------------------------ #

class TickData(BaseModel):
    symbol: str
    bid: float
    ask: float
    spread: float
    time: int
    volume: Optional[int] = 0
    h1_open: Optional[float] = None
    h1_high: Optional[float] = None
    h1_low: Optional[float] = None
    h1_close: Optional[float] = None

class AccountData(BaseModel):
    balance: float
    equity: float
    margin: float
    free_margin: float
    profit: float
    leverage: int
    currency: str
    server: str

class TradeResult(BaseModel):
    action: str
    success: bool
    ticket: Optional[int] = None
    retcode: Optional[int] = None
    comment: Optional[str] = None
    error: Optional[str] = None


def _accumulate_h1(data: TickData) -> None:
    """Fold an EA tick into the rolling H1 bar buffer (called under _lock)."""
    sym = data.symbol
    if data.h1_open is None:
        return  # EA didn't attach OHLCV — nothing to accumulate

    # Align to H1 boundary
    bar_time = int(data.time) - (int(data.time) % 3600)

    if sym not in _h1_bars:
        _h1_bars[sym] = deque(maxlen=_H1_MAX_BARS)

    current = _h1_open_bar.get(sym)
    if current is None or current["time"] != bar_time:
        # Seal the previous bar
        if current is not None:
            _h1_bars[sym].append(current)
        # Open a new in-progress bar from EA OHLCV snapshot
        _h1_open_bar[sym] = {
            "time":   bar_time,
            "open":   round(data.h1_open, 5),
            "high":   round(data.h1_high, 5),
            "low":    round(data.h1_low, 5),
            "close":  round(data.h1_close, 5),
            "volume": data.volume or 0,
        }
    else:
        # Update in-progress bar: EA sends realtime OHLCV for current bar
        current["high"]   = round(max(current["high"], data.h1_high), 5)
        current["low"]    = round(min(current["low"], data.h1_low), 5)
        current["close"]  = round(data.h1_close, 5)
        current["volume"] = data.volume or current["volume"]


@app.post("/tick")
def push_tick(data: TickData):
    with _lock:
        _state["tick"] = data.model_dump()
        _state["heartbeat"] = time.time()
        _accumulate_h1(data)
        (DATA_DIR / "price.json").write_text(json.dumps(_state["tick"]))
    return {"ok": True}


@app.post("/account")
def push_account(data: AccountData):
    with _lock:
        _state["account"] = data.model_dump()
        (DATA_DIR / "account.json").write_text(json.dumps(_state["account"]))
    return {"ok": True}


@app.post("/result")
def push_result(data: TradeResult):
    with _lock:
        _result_log.append(data.model_dump())
    return {"ok": True}


@app.post("/heartbeat")
def push_heartbeat():
    with _lock:
        _state["heartbeat"] = time.time()
    return {"ok": True, "time": int(time.time())}


# ------------------------------------------------------------------ #
# Server → EA  (EA polls for pending commands)                        #
# ------------------------------------------------------------------ #

@app.get("/command")
def get_command():
    with _lock:
        if _command_queue:
            cmd = _command_queue.popleft()
            return JSONResponse(content=cmd)
    return JSONResponse(content={"action": "NONE"})


# ------------------------------------------------------------------ #
# Python bot → Server  (bot reads state, sends commands)             #
# ------------------------------------------------------------------ #

@app.get("/state")
def get_state():
    with _lock:
        return {
            "tick":      _state["tick"],
            "account":   _state["account"],
            "connected": (time.time() - _state["heartbeat"]) < 15,
        }

@app.post("/order")
def send_order(cmd: dict):
    with _lock:
        _command_queue.append(cmd)
    return {"ok": True, "queued": len(_command_queue)}

@app.get("/results")
def get_results():
    with _lock:
        results = list(_result_log)
        _result_log.clear()
    return results

@app.get("/ping")
def ping():
    connected = (time.time() - _state["heartbeat"]) < 15
    return {"pong": True, "ea_connected": connected, "time": int(time.time())}


def _synthetic_bars(symbol: str, timeframe: str, bars: int, base_price: float) -> list[dict]:
    """Deterministic random-walk fallback when real EA history is unavailable."""
    seconds = _TF_SECONDS.get(timeframe.upper(), 3600)
    seed_int = int((base_price * 1_000_000) % 2**31) ^ hash(symbol) & 0x7fffffff
    rng = random.Random(seed_int)
    now = int(time.time())
    end = now - (now % seconds)
    walk: list[float] = []
    p = base_price
    for _ in range(bars):
        p = max(0.5, p + rng.gauss(0, 0.0008))
        walk.append(p)
    walk.reverse()
    out = []
    for i, close in enumerate(walk):
        bar_time = end - (bars - 1 - i) * seconds
        prev = walk[i - 1] if i > 0 else close
        high = max(prev, close) + abs(rng.gauss(0, 0.0003))
        low  = min(prev, close) - abs(rng.gauss(0, 0.0003))
        out.append({
            "time":   bar_time,
            "open":   round(prev, 5),
            "high":   round(high, 5),
            "low":    round(low, 5),
            "close":  round(close, 5),
            "volume": rng.randint(50, 5000),
        })
    return out


@app.get("/history")
def get_history(
    symbol: str = Query("EURUSD"),
    timeframe: str = Query("H1"),
    bars: int = Query(500, ge=1, le=20000),
):
    """OHLCV history for the requested symbol/timeframe.

    Serves real accumulated bars from EA tick pushes when available (H1 only).
    Falls back to a deterministic synthetic random walk for other timeframes
    or before the EA is live, so backtests run end-to-end immediately.
    """
    with _lock:
        last_tick = dict(_state.get("tick") or {})
        real_bars: list[dict] = []
        if timeframe.upper() == "H1" and symbol in _h1_bars:
            real_bars = list(_h1_bars[symbol])
            # Append in-progress bar if present
            current = _h1_open_bar.get(symbol)
            if current:
                real_bars.append(dict(current))

    if real_bars:
        # Return newest `bars` bars in ascending time order
        return {"symbol": symbol, "timeframe": timeframe, "bars": real_bars[-bars:], "source": "live"}

    base_price = float(last_tick.get("bid") or 1.10000) or 1.10000
    out = _synthetic_bars(symbol, timeframe, bars, base_price)
    return {"symbol": symbol, "timeframe": timeframe, "bars": out, "source": "synthetic"}


if __name__ == "__main__":
    print("MT5 HTTP Bridge listening on http://0.0.0.0:8080")
    print(f"EA should connect to: http://192.168.64.1:8080")
    print(f"Bridge data dir: {DATA_DIR}")
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")
