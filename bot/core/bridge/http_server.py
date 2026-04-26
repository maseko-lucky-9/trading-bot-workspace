"""
HTTP Bridge Server — macOS side.
The MT5 EA (Windows VM) POSTs price/account data here and GETs pending commands.
No shared folders, no SMB, no sockets DLL required.

Run:  python core/bridge/http_server.py
EA connects to:  http://192.168.64.1:8080
"""
import json
import os
import random
import time
import threading
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import pandas as pd
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Timeframe -> seconds per bar for synthetic history
_TF_SECONDS = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800,
    "H1": 3600, "H4": 14400, "D1": 86400,
}

@asynccontextmanager
async def _lifespan(app: FastAPI):
    _load_tf_bars_from_disk()
    t = threading.Thread(target=_flush_worker, daemon=True, name="tf-bars-flusher")
    t.start()
    yield

app = FastAPI(title="MT5 Bridge", lifespan=_lifespan)

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

# Max bars kept in memory per timeframe (higher-frequency = more bars needed)
_TF_MAX_BARS: dict[str, int] = {
    "M1": 20000, "M5": 10000, "M15": 5000, "M30": 2000,
    "H1": 2000,  "H4": 1000,  "D1": 500,
}

# Rolling bar buffer: symbol → timeframe → deque of OHLCV dicts.
# H1 is populated from EA tick pushes; other timeframes via POST /history-batch.
_tf_bars: dict[str, dict[str, deque]] = {}
_tf_open_bar: dict[str, dict[str, dict]] = {}

# Persistence: set of (symbol, tf) pairs that need flushing to disk.
_dirty: set[tuple[str, str]] = set()
_dirty_lock = threading.Lock()


def _bars_path(symbol: str, tf: str) -> Path:
    return DATA_DIR / "history" / f"{symbol}_{tf}.parquet"


def _flush_tf_bars(symbol: str, tf: str) -> None:
    """Snapshot _tf_bars[symbol][tf] to parquet.

    Guard: if in-memory row count < on-disk row count, skip the flush so a
    partial /history-batch chunk never overwrites a more-complete dataset.
    No cross-symbol merging — the in-memory data is authoritative for its symbol.
    """
    with _lock:
        q = _tf_bars.get(symbol, {}).get(tf)
        rows = list(q) if q else []
    if not rows:
        return

    path = _bars_path(symbol, tf)
    if path.exists():
        try:
            n_disk = pd.read_parquet(path, columns=["time"]).shape[0]
            if len(rows) < n_disk:
                return  # don't overwrite a more-complete dataset
        except Exception:
            pass  # unreadable on-disk file — proceed with fresh write

    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True).astype("datetime64[ms, UTC]")
    df = df.sort_values("time").drop_duplicates(subset=["time"]).reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def _load_tf_bars_from_disk() -> None:
    """Populate _tf_bars from existing parquet files on startup."""
    hist_dir = DATA_DIR / "history"
    if not hist_dir.exists():
        return
    for path in sorted(hist_dir.glob("*.parquet")):
        stem = path.stem  # e.g. EURUSD_H1
        parts = stem.rsplit("_", 1)
        if len(parts) != 2:
            continue
        symbol, tf = parts
        if tf not in _TF_SECONDS:
            continue
        try:
            df = pd.read_parquet(path)
            epoch_s = df["time"].map(lambda t: int(t.timestamp())).tolist()
            rows = [
                {
                    "time":   int(epoch_s[i]),
                    "open":   float(df["open"].iloc[i]),
                    "high":   float(df["high"].iloc[i]),
                    "low":    float(df["low"].iloc[i]),
                    "close":  float(df["close"].iloc[i]),
                    "volume": int(df["volume"].iloc[i]),
                }
                for i in range(len(df))
            ]
        except Exception as e:
            print(f"WARNING: failed to load {path}: {e}")
            continue
        maxlen = _TF_MAX_BARS.get(tf, 1000)
        if symbol not in _tf_bars:
            _tf_bars[symbol] = {}
        _tf_bars[symbol][tf] = deque(rows[-maxlen:], maxlen=maxlen)
        print(f"Loaded {len(_tf_bars[symbol][tf])} {symbol} {tf} bars from disk")


def _flush_worker() -> None:
    """Background daemon: flush dirty (symbol, tf) pairs to parquet every 30s."""
    while True:
        time.sleep(30)
        with _dirty_lock:
            dirty = set(_dirty)
            _dirty.clear()
        for sym, tf in dirty:
            try:
                _flush_tf_bars(sym, tf)
            except Exception as e:
                print(f"WARNING: flush failed {sym} {tf}: {e}")


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


def _accumulate_bar(data: TickData, timeframe: str = "H1") -> None:
    """Fold an EA tick into the rolling bar buffer for the given timeframe (called under _lock)."""
    sym = data.symbol
    if data.h1_open is None:
        return  # EA didn't attach OHLCV — nothing to accumulate

    tf = timeframe.upper()
    seconds = _TF_SECONDS.get(tf, 3600)
    bar_time = int(data.time) - (int(data.time) % seconds)

    if sym not in _tf_bars:
        _tf_bars[sym] = {}
    if tf not in _tf_bars[sym]:
        _tf_bars[sym][tf] = deque(maxlen=_TF_MAX_BARS.get(tf, 1000))
    if sym not in _tf_open_bar:
        _tf_open_bar[sym] = {}

    current = _tf_open_bar[sym].get(tf)
    if current is None or current["time"] != bar_time:
        # Seal the previous bar
        if current is not None:
            _tf_bars[sym][tf].append(current)
            with _dirty_lock:
                _dirty.add((sym, tf))
        # Open a new in-progress bar from EA OHLCV snapshot
        _tf_open_bar[sym][tf] = {
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
        _accumulate_bar(data, "H1")
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


class HistoryBar(BaseModel):
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: int = 0


class HistoryBatch(BaseModel):
    symbol: str
    timeframe: str
    bars: list[HistoryBar]


@app.get("/history")
def get_history(
    symbol: str = Query("EURUSD"),
    timeframe: str = Query("H1"),
    bars: int = Query(500, ge=1, le=20000),
    from_time: Optional[int] = Query(None),
    offset: int = Query(0, ge=0),
):
    """OHLCV history for the requested symbol/timeframe.

    Serves real accumulated bars (any timeframe) when available.
    Falls back to a deterministic synthetic random walk before the EA supplies
    real data, so backtests run end-to-end immediately.

    Pagination params:
      from_time — only include bars with time <= this epoch value
      offset    — skip the last N bars before applying the bars limit

    When offset > 0 or from_time is set (pagination mode) no synthetic padding
    is applied — callers use a shorter result to detect end-of-history.

    Response includes total_available (after from_time filter, before slicing)
    for client-side pagination depth checks.
    """
    tf = timeframe.upper()
    with _lock:
        last_tick = dict(_state.get("tick") or {})
        available: list[dict] = []
        sym_tf = _tf_bars.get(symbol, {}).get(tf)
        if sym_tf is not None:
            available = list(sym_tf)
            open_bar = _tf_open_bar.get(symbol, {}).get(tf)
            if open_bar:
                available.append(dict(open_bar))

    if from_time is not None:
        available = [b for b in available if b["time"] <= from_time]

    total_available = len(available)
    base_price = float(last_tick.get("bid") or 1.10000) or 1.10000
    pagination_mode = offset > 0 or from_time is not None

    if available:
        end   = max(0, len(available) - offset) if offset > 0 else None
        start = max(0, (end if end is not None else len(available)) - bars)
        page  = available[start:end]

        if pagination_mode or len(page) >= bars:
            return {"symbol": symbol, "timeframe": timeframe,
                    "bars": page, "source": "live",
                    "total_available": total_available}

        # Default mode: not enough live bars — pad front with synthetic
        n_synthetic = bars - len(page)
        synthetic = _synthetic_bars(symbol, timeframe, n_synthetic, base_price)
        return {"symbol": symbol, "timeframe": timeframe,
                "bars": synthetic + page, "source": "live+synthetic",
                "total_available": total_available}

    if pagination_mode:
        return {"symbol": symbol, "timeframe": timeframe,
                "bars": [], "source": "live", "total_available": 0}
    out = _synthetic_bars(symbol, timeframe, bars, base_price)
    return {"symbol": symbol, "timeframe": timeframe,
            "bars": out, "source": "synthetic", "total_available": 0}


@app.post("/history-batch")
def push_history_batch(batch: HistoryBatch):
    """Bulk bar ingestion — receives CopyRates output from the MQL5 EA.

    Merges incoming bars into _tf_bars[symbol][timeframe] deduplicating by
    time (incoming bar wins on conflict) and maintaining ascending order.
    """
    tf = batch.timeframe.upper()
    if tf not in _TF_SECONDS:
        raise HTTPException(status_code=400, detail=f"unknown timeframe: {tf}")

    if not batch.bars:
        return {"ingested": 0, "total": 0}

    incoming = sorted(
        [b.model_dump() for b in batch.bars],
        key=lambda b: b["time"],
    )

    with _lock:
        if batch.symbol not in _tf_bars:
            _tf_bars[batch.symbol] = {}
        if tf not in _tf_bars[batch.symbol]:
            _tf_bars[batch.symbol][tf] = deque(maxlen=_TF_MAX_BARS.get(tf, 1000))

        q = _tf_bars[batch.symbol][tf]
        existing = {b["time"]: b for b in q}
        for b in incoming:
            existing[b["time"]] = b  # incoming wins on conflict
        merged = sorted(existing.values(), key=lambda b: b["time"])
        maxlen = _TF_MAX_BARS.get(tf, 1000)
        q.clear()
        q.extend(merged[-maxlen:])
        total = len(q)

    _flush_tf_bars(batch.symbol, tf)
    return {"ingested": len(incoming), "total": total}


if __name__ == "__main__":
    print("MT5 HTTP Bridge listening on http://0.0.0.0:8080")
    print(f"EA should connect to: http://192.168.64.1:8080")
    print(f"Bridge data dir: {DATA_DIR}")
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")
