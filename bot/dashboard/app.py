"""FastAPI application — read-only dashboard for the MT5 paper-trading bot.

Routes:

* ``GET /``                   → static HTML page (vanilla JS + Chart.js CDN)
* ``GET /static/<file>``      → CSS / JS assets
* ``GET /api/health``         → process probe + bridge probe + regime + drawdown
* ``GET /api/equity``         → cumulative-PnL series + peak + drawdown
* ``GET /api/trades``         → last N rows, filterable by side/symbol
* ``GET /api/metrics``        → Sharpe, DSR, expectancy, win rate, payoff ratio

All ``/api/*`` routes are wrapped in a try/except so a degraded artefact
(bridge down, bot killed, parquet missing) never produces a 500.

Hard-locks:

* CSP locks scripts to ``self`` + ``cdn.jsdelivr.net`` (Chart.js).
* No CORS middleware — same-origin only.
* Static mount is read-only.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from dashboard import sources

_PKG_ROOT = Path(__file__).resolve().parent
_TEMPLATES = _PKG_ROOT / "templates"
_STATIC = _PKG_ROOT / "static"

_CSP = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Apply CSP + a couple of belt-and-braces hardening headers."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", _CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response


app = FastAPI(title="MT5 Bot Dashboard", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SecurityHeadersMiddleware)
app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")


# --------------------------------------------------------------------------- #
# Page                                                                        #
# --------------------------------------------------------------------------- #


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(_TEMPLATES / "index.html", media_type="text/html")


# --------------------------------------------------------------------------- #
# /api/health                                                                 #
# --------------------------------------------------------------------------- #


@app.get("/api/health")
def api_health() -> JSONResponse:
    try:
        cfg = sources.load_config()
        process = sources.probe_process()
        bridge = sources.probe_bridge((cfg.get("bridge") or {}).get("base_url"))
        regime = sources.current_regime(cfg)

        # Best-effort drawdown from trades.csv: read once and fold.
        trades_df = sources.read_trades()
        _, closed = sources.split_open_closed(trades_df)
        equity = sources.compute_equity_series(closed)

        return JSONResponse(
            {
                "status": "ok",
                "process": process,
                "bridge": bridge,
                "regime": regime,
                "circuit_breaker": {
                    "current_drawdown": equity.get("current_drawdown", 0.0),
                    "peak_equity": equity.get("peak_equity", 0.0),
                    "trade_count": int(len(closed)),
                },
            }
        )
    except Exception as exc:  # pragma: no cover — defensive
        return JSONResponse({"status": "unavailable", "error": str(exc)})


# --------------------------------------------------------------------------- #
# /api/equity                                                                 #
# --------------------------------------------------------------------------- #


@app.get("/api/equity")
def api_equity(limit: int = Query(10000, ge=1, le=100000)) -> JSONResponse:
    try:
        trades_df = sources.read_trades()
        _, closed = sources.split_open_closed(trades_df)
        if len(closed) > limit:
            closed = closed.tail(limit)
        return JSONResponse(sources.compute_equity_series(closed))
    except Exception as exc:  # pragma: no cover
        return JSONResponse({"status": "unavailable", "error": str(exc)})


# --------------------------------------------------------------------------- #
# /api/trades                                                                 #
# --------------------------------------------------------------------------- #


@app.get("/api/trades")
def api_trades(
    limit: int = Query(100, ge=1, le=1000),
    side: str = Query("ALL"),
    symbol: str | None = Query(None),
) -> JSONResponse:
    try:
        trades_df = sources.read_trades()
        _, closed = sources.split_open_closed(trades_df)
        side_u = (side or "ALL").upper()
        if side_u in ("BUY", "SELL") and "type" in closed.columns:
            closed = closed[closed["type"].astype(str).str.upper() == side_u]
        if symbol and "symbol" in closed.columns:
            closed = closed[closed["symbol"].astype(str).str.upper() == symbol.upper()]
        if "close_time" in closed.columns:
            closed = closed.sort_values("close_time")
        rows = closed.tail(limit).to_dict(orient="records")
        # Replace NaN with None so JSON stays valid.
        cleaned = []
        for r in rows:
            cleaned.append({k: (None if (isinstance(v, float) and (v != v)) else v) for k, v in r.items()})
        return JSONResponse(
            {
                "status": "ok",
                "count": len(cleaned),
                "rows": cleaned,
            }
        )
    except Exception as exc:  # pragma: no cover
        return JSONResponse({"status": "unavailable", "error": str(exc)})


# --------------------------------------------------------------------------- #
# /api/metrics                                                                #
# --------------------------------------------------------------------------- #


@app.get("/api/metrics")
def api_metrics() -> JSONResponse:
    try:
        trades_df = sources.read_trades()
        _, closed = sources.split_open_closed(trades_df)
        return JSONResponse(sources.compute_metrics(closed))
    except Exception as exc:  # pragma: no cover
        return JSONResponse({"status": "unavailable", "error": str(exc)})
