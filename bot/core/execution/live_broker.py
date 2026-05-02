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
        # EA expects action="BUY" or "SELL" (not "OPEN") — matches PollCommand()
        # handler in PythonBridgeHTTP.mq5 which dispatches on action directly.
        cmd = {
            "action": side.upper(),
            "symbol": symbol,
            "volume": float(volume),
            "sl": float(sl),
            "tp": float(tp),
        }
        return self.bridge.send_order(cmd)

    def close_position(self, ticket: int) -> dict:
        cmd = {"action": "CLOSE", "ticket": ticket}
        return self.bridge.send_order(cmd)

    def partial_close(self, ticket: int, fraction: float) -> dict:
        """Queue a partial-close command for the EA.

        NOTE: PythonBridgeHTTP.mq5 does not yet handle PARTIAL_CLOSE — the
        Python side is correct; EA-side support is a follow-up task.
        """
        if not (0 < fraction < 1):
            raise ValueError(f"fraction must be in (0, 1), got {fraction}")
        cmd = {"action": "PARTIAL_CLOSE", "ticket": ticket, "fraction": float(fraction)}
        return self.bridge.send_order(cmd)

    def modify_sl(self, ticket: int, new_sl: float) -> dict:
        """Queue a MODIFY command to update the stop-loss on the EA."""
        cmd = {"action": "MODIFY", "ticket": ticket, "sl": float(new_sl)}
        return self.bridge.send_order(cmd)

    def get_positions(self) -> list[dict]:
        state = self.bridge.get_state() or {}
        return list(state.get("positions") or [])

    def get_closed(self) -> list[dict]:
        return self.bridge.get_results()

    def get_account(self) -> dict:
        return self.bridge.get_account() or {}
