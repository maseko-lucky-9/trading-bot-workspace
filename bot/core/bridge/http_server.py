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


@app.post("/tick")
def push_tick(data: TickData):
    with _lock:
        _state["tick"] = data.model_dump()
        _state["heartbeat"] = time.time()
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


@app.get("/history")
def get_history(
    symbol: str = Query("EURUSD"),
    timeframe: str = Query("H1"),
    bars: int = Query(500, ge=1, le=20000),
):
    """Synthetic OHLCV history for the requested symbol/timeframe.

    Real MT5 history will replace this once the EA exposes ``CopyRates``
    via the bridge. Until then we synthesise a deterministic random walk
    seeded from the last live tick price so backtests run end-to-end.
    """
    seconds = _TF_SECONDS.get(timeframe.upper(), 3600)
    with _lock:
        last_tick = dict(_state.get("tick") or {})
    base_price = float(last_tick.get("bid") or 1.10000) or 1.10000
    seed_int = int((base_price * 1_000_000) % 2**31) ^ hash(symbol) & 0x7fffffff
    rng = random.Random(seed_int)

    now = int(time.time())
    # Align to bar boundary
    end = now - (now % seconds)
    out: list[dict] = []
    price = base_price
    # Walk backwards then reverse so 'time' is ascending
    walk: list[float] = []
    p = price
    for _ in range(bars):
        step = rng.gauss(0, 0.0008)  # ~8 pips H1 vol
        p = max(0.5, p + step)
        walk.append(p)
    walk.reverse()

    for i, close in enumerate(walk):
        bar_time = end - (bars - 1 - i) * seconds
        prev = walk[i - 1] if i > 0 else close
        high = max(prev, close) + abs(rng.gauss(0, 0.0003))
        low = min(prev, close) - abs(rng.gauss(0, 0.0003))
        open_p = prev
        out.append({
            "time": bar_time,
            "open": round(open_p, 5),
            "high": round(high, 5),
            "low": round(low, 5),
            "close": round(close, 5),
            "volume": rng.randint(50, 5000),
        })
    return {"symbol": symbol, "timeframe": timeframe, "bars": out}


if __name__ == "__main__":
    print("MT5 HTTP Bridge listening on http://0.0.0.0:8080")
    print(f"EA should connect to: http://192.168.64.1:8080")
    print(f"Bridge data dir: {DATA_DIR}")
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")
