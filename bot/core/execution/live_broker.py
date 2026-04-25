"""
LiveBroker — routes orders through the MT5 bridge (US-010).

Pre-flight checks on construction: EA connectivity + minimum equity.
Raises LiveModeNotEnabled before touching any state if checks fail.
"""
from __future__ import annotations

from core.bridge.http_client import MT5BridgeClient

MIN_EQUITY_USD = 1_000.0


class LiveModeNotEnabled(Exception):
    """Raised when live-mode pre-flight checks fail."""


class LiveBroker:
    """Routes orders through the MT5 bridge.

    Parameters
    ----------
    bridge : MT5BridgeClient
    config : dict
        Parsed config.yaml; reads ``risk.min_equity`` if present.
    """

    def __init__(self, bridge: MT5BridgeClient, config: dict | None = None) -> None:
        self.bridge = bridge
        cfg = config or {}
        self._min_equity = float(
            (cfg.get("risk") or {}).get("min_equity", MIN_EQUITY_USD)
        )
        self._preflight()

    def _preflight(self) -> None:
        if not self.bridge.is_connected():
            raise LiveModeNotEnabled("EA not connected — check bridge and MT5 EA")
        acct = self.bridge.get_account() or {}
        equity = float(acct.get("equity") or acct.get("balance") or 0.0)
        if equity < self._min_equity:
            raise LiveModeNotEnabled(
                f"equity {equity:.2f} < minimum {self._min_equity:.2f}"
            )

    # ------------------------------------------------------------------ #
    # Broker interface                                                   #
    # ------------------------------------------------------------------ #

    def place_order(
        self,
        symbol: str,
        side: str,
        volume: float,
        sl: float = 0.0,
        tp: float = 0.0,
    ) -> dict:
        cmd = {
            "action": "OPEN",
            "symbol": symbol,
            "type": side,
            "volume": float(volume),
            "sl": float(sl),
            "tp": float(tp),
        }
        return self.bridge.send_order(cmd)

    def close_position(self, ticket: int) -> dict:
        cmd = {"action": "CLOSE", "ticket": ticket}
        return self.bridge.send_order(cmd)

    def get_positions(self) -> list[dict]:
        state = self.bridge.get_state() or {}
        return list(state.get("positions") or [])

    def get_closed(self) -> list[dict]:
        return self.bridge.get_results()

    def get_account(self) -> dict:
        return self.bridge.get_account() or {}
