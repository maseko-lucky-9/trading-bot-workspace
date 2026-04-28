"""FastAPI endpoint tests using ``TestClient``.

The ``dashboard.sources`` adapters are monkeypatched at the module level
that ``dashboard.app`` imports them from, so no live subprocess /
network / disk reads happen during these tests.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import dashboard.app as app_module
from dashboard import sources as real_sources


@pytest.fixture
def client(monkeypatch, trades_csv_mixed) -> TestClient:
    """Patch every IO seam to deterministic returns; return TestClient."""

    monkeypatch.setattr(app_module.sources, "load_config", lambda config_path=None: {
        "bridge": {"base_url": "http://localhost:8080"},
        "bot": {"instruments": ["EURUSD"], "timeframe": "M15"},
    })
    monkeypatch.setattr(app_module.sources, "probe_process", lambda: {
        "status": "running", "pid": 99999, "etime": "00:42:00",
    })
    monkeypatch.setattr(app_module.sources, "probe_bridge", lambda *a, **kw: {
        "status": "ok", "pong": True, "ea_connected": True, "latency_ms": 12.3, "error": None,
    })
    monkeypatch.setattr(app_module.sources, "current_regime", lambda *a, **kw: {
        "status": "ok", "label": "trend", "regime_id": 0,
        "symbol": "EURUSD", "timeframe": "M15", "bars_used": 200, "error": None,
    })

    # Capture the *original* read_trades before patching — `real_sources` is the
    # same module object as `app_module.sources`, so re-reading the attribute
    # after monkeypatch would recurse into the wrapper. Bind a local first.
    original_read_trades = real_sources.read_trades

    def _read_trades(path=None):
        return original_read_trades(trades_csv_mixed)

    monkeypatch.setattr(app_module.sources, "read_trades", _read_trades)
    return TestClient(app_module.app)


# --------------------------------------------------------------------------- #
# Smoke                                                                       #
# --------------------------------------------------------------------------- #


def test_get_root_returns_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "MT5 Bot Dashboard" in r.text
    # Hardening headers from the middleware
    assert "Content-Security-Policy" in r.headers
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert "https://cdn.jsdelivr.net" in r.headers["Content-Security-Policy"]
    assert r.headers.get("X-Frame-Options") == "DENY"


# --------------------------------------------------------------------------- #
# /api/health                                                                 #
# --------------------------------------------------------------------------- #


def test_api_health_happy_path(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["process"]["status"] == "running"
    assert body["bridge"]["status"] == "ok"
    assert body["regime"]["label"] == "trend"
    assert "circuit_breaker" in body
    assert body["circuit_breaker"]["trade_count"] == 3


def test_api_health_when_bridge_unreachable(client, monkeypatch):
    monkeypatch.setattr(app_module.sources, "probe_bridge", lambda *a, **kw: {
        "status": "unreachable", "pong": None, "ea_connected": None,
        "latency_ms": None, "error": "Connection refused",
    })
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["bridge"]["status"] == "unreachable"
    # Other panes still populate
    assert body["process"]["status"] == "running"
    assert body["circuit_breaker"]["trade_count"] == 3


def test_api_health_when_bot_not_running(client, monkeypatch):
    monkeypatch.setattr(app_module.sources, "probe_process", lambda: {
        "status": "not_running", "pid": None, "etime": None,
    })
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["process"]["status"] == "not_running"
    # Trades-derived data still rendered
    assert body["circuit_breaker"]["peak_equity"] == 15.0


# --------------------------------------------------------------------------- #
# /api/equity                                                                 #
# --------------------------------------------------------------------------- #


def test_api_equity_returns_series(client):
    r = client.get("/api/equity")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["equity"] == pytest.approx([10.0, 15.0, 5.0])
    assert body["peak_equity"] == 15.0


# --------------------------------------------------------------------------- #
# /api/trades                                                                 #
# --------------------------------------------------------------------------- #


def test_api_trades_default_returns_closed_only(client):
    r = client.get("/api/trades")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["count"] == 3
    sides = {row["type"] for row in body["rows"]}
    assert sides <= {"BUY", "SELL"}


def test_api_trades_filter_by_buy(client):
    r = client.get("/api/trades?side=BUY")
    body = r.json()
    assert body["count"] == 2
    for row in body["rows"]:
        assert row["type"] == "BUY"


def test_api_trades_filter_by_symbol(client):
    r = client.get("/api/trades?symbol=GBPUSD")
    body = r.json()
    assert body["count"] == 1
    assert body["rows"][0]["symbol"] == "GBPUSD"


def test_api_trades_limit_clamps(client):
    r = client.get("/api/trades?limit=1")
    body = r.json()
    assert body["count"] == 1


# --------------------------------------------------------------------------- #
# /api/metrics                                                                #
# --------------------------------------------------------------------------- #


def test_api_metrics_populates_keys(client):
    r = client.get("/api/metrics")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["trade_count"] == 3
    for k in ("sharpe", "dsr", "expectancy", "win_rate", "payoff_ratio"):
        assert k in body


# --------------------------------------------------------------------------- #
# Graceful degradation — every endpoint must return 200 even on failure       #
# --------------------------------------------------------------------------- #


def test_endpoints_never_500_when_trades_explode(client, monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(app_module.sources, "read_trades", _boom)
    for path in ("/api/health", "/api/equity", "/api/trades", "/api/metrics"):
        r = client.get(path)
        assert r.status_code == 200, f"{path} returned {r.status_code}"
        body = r.json()
        assert body.get("status") in ("ok", "unavailable")
