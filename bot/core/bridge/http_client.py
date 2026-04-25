"""
HTTP Bridge Client (US-001).

Connects the bot (macOS host) to the FastAPI bridge server which mediates
all MT5 communication. The bridge runs on the local host (default
http://localhost:8080) and forwards orders/state to the MT5 EA inside the
UTM Windows VM via HTTP polling.

Public surface:
    MT5BridgeClient
        ping()           -> bool
        get_tick()       -> dict
        get_account()    -> dict
        get_state()      -> dict
        get_history()    -> list[dict]
        send_order()     -> dict
        get_results()    -> list[dict]
        is_connected()   -> bool
"""
from __future__ import annotations

import time
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
)


class BridgeDisconnected(Exception):
    """Raised when the bridge cannot be reached after retries."""


_RETRYABLE = (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.HTTPError)


class MT5BridgeClient:
    """HTTP client to the MT5 bridge server.

    Defaults to http://localhost:8080 (the bridge runs on the macOS host).
    For UTM-based deployments target http://192.168.64.1:8080 explicitly.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        timeout: float = 5.0,
        heartbeat_timeout: int = 10,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.heartbeat_timeout = heartbeat_timeout
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout)
        self._last_heartbeat: float = 0.0

    # ------------------------------------------------------------------ #
    # Internal helpers                                                   #
    # ------------------------------------------------------------------ #

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_fixed(1),
        retry=retry_if_exception_type(_RETRYABLE),
        reraise=True,
    )
    def _get(self, path: str, params: dict | None = None) -> Any:
        try:
            r = self._client.get(path, params=params)
            r.raise_for_status()
            return r.json()
        except _RETRYABLE:
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_fixed(1),
        retry=retry_if_exception_type(_RETRYABLE),
        reraise=True,
    )
    def _post(self, path: str, json: dict | None = None) -> Any:
        try:
            r = self._client.post(path, json=json or {})
            r.raise_for_status()
            return r.json()
        except _RETRYABLE:
            raise

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #

    def ping(self) -> bool:
        """Return True if the bridge is reachable.

        Note: this is *bridge* reachability — `ea_connected` may be False
        even when the bridge itself is up. Use `is_connected()` for a
        full liveness check.
        """
        try:
            data = self._get("/ping")
        except Exception:
            return False
        self._last_heartbeat = time.time()
        return bool(data.get("pong"))

    def is_connected(self) -> bool:
        """True iff bridge ping succeeded recently AND EA is connected."""
        try:
            data = self._get("/ping")
        except Exception:
            return False
        self._last_heartbeat = time.time()
        return bool(data.get("ea_connected"))

    def get_tick(self, symbol: str = "EURUSD") -> dict:
        state = self._get("/state")
        tick = state.get("tick", {}) or {}
        # if a different symbol was requested but bridge holds another,
        # still return what's there — caller can filter by 'symbol' field.
        if not tick:
            raise BridgeDisconnected("no tick available")
        return tick

    def get_account(self) -> dict:
        state = self._get("/state")
        acct = state.get("account", {}) or {}
        return acct

    def get_state(self) -> dict:
        return self._get("/state")

    def get_history(
        self, symbol: str = "EURUSD", timeframe: str = "H1", bars: int = 500
    ) -> list[dict]:
        data = self._get(
            "/history",
            params={"symbol": symbol, "timeframe": timeframe, "bars": bars},
        )
        return data.get("bars", [])

    def send_order(self, cmd: dict) -> dict:
        return self._post("/order", json=cmd)

    def get_results(self) -> list[dict]:
        try:
            return self._get("/results") or []
        except Exception:
            return []

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    def __enter__(self) -> "MT5BridgeClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
