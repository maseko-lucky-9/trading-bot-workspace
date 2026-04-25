"""
MT5 File-based IPC Bridge Client
Reads price/account JSON from a folder shared between macOS and the UTM Windows VM.
Writes commands.json to trigger trades; reads trade_results.txt for confirmations.
"""
import json
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class MT5Client:
    def __init__(self, shared_folder: str, heartbeat_timeout: int = 10):
        self.folder = Path(shared_folder)
        self.heartbeat_timeout = heartbeat_timeout
        self._last_heartbeat: Optional[float] = None

    # ------------------------------------------------------------------ #
    # Connection                                                           #
    # ------------------------------------------------------------------ #

    def ping(self) -> bool:
        """Write PING command and wait for PONG in results file."""
        cmd = {"action": "PING", "ts": int(time.time())}
        self._write_command(cmd)
        deadline = time.time() + 5
        while time.time() < deadline:
            results = self._read_new_results()
            for r in results:
                if r.get("action") == "PING" and r.get("result") == "PONG":
                    logger.info("PONG received from MT5 EA")
                    return True
            time.sleep(0.2)
        logger.error("PING timed out — EA not responding")
        return False

    def is_connected(self) -> bool:
        """Check heartbeat file timestamp."""
        hb_file = self.folder / "heartbeat.json"
        if not hb_file.exists():
            return False
        try:
            data = json.loads(hb_file.read_text())
            ea_time = data.get("time", 0)
            return (time.time() - ea_time) < self.heartbeat_timeout
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    # Market Data                                                          #
    # ------------------------------------------------------------------ #

    def get_tick(self) -> Optional[dict]:
        """Read latest price snapshot from EA."""
        return self._read_json("price.json")

    def get_account(self) -> Optional[dict]:
        """Read account info from EA."""
        return self._read_json("account.json")

    def get_positions(self) -> list:
        """Request and read open positions."""
        self._write_command({"action": "GET_POSITIONS"})
        time.sleep(0.6)
        data = self._read_json("positions.json")
        return data if isinstance(data, list) else []

    # ------------------------------------------------------------------ #
    # Trading                                                              #
    # ------------------------------------------------------------------ #

    def buy(self, symbol: str, volume: float,
            sl: float = 0.0, tp: float = 0.0) -> dict:
        cmd = {"action": "BUY", "symbol": symbol,
               "volume": volume, "sl": sl, "tp": tp}
        return self._send_trade(cmd)

    def sell(self, symbol: str, volume: float,
             sl: float = 0.0, tp: float = 0.0) -> dict:
        cmd = {"action": "SELL", "symbol": symbol,
               "volume": volume, "sl": sl, "tp": tp}
        return self._send_trade(cmd)

    def close(self, ticket: int) -> dict:
        cmd = {"action": "CLOSE", "ticket": str(ticket)}
        return self._send_trade(cmd)

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _write_command(self, cmd: dict):
        path = self.folder / "commands.json"
        path.write_text(json.dumps(cmd))

    def _read_json(self, filename: str) -> Optional[dict]:
        path = self.folder / filename
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return None

    def _read_new_results(self) -> list:
        path = self.folder / "trade_results.txt"
        if not path.exists():
            return []
        results = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        # Clear after reading
        path.write_text("")
        return results

    def _send_trade(self, cmd: dict, timeout: int = 5) -> dict:
        self._write_command(cmd)
        deadline = time.time() + timeout
        while time.time() < deadline:
            results = self._read_new_results()
            for r in results:
                if r.get("action") == cmd["action"]:
                    return r
            time.sleep(0.3)
        return {"success": False, "error": "timeout"}


# ------------------------------------------------------------------ #
# CLI                                                                  #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    import argparse
    import sys
    import yaml

    parser = argparse.ArgumentParser(description="MT5 Bridge CLI")
    parser.add_argument("--ping",    action="store_true", help="Ping the EA")
    parser.add_argument("--tick",    action="store_true", help="Get latest tick")
    parser.add_argument("--account", action="store_true", help="Get account info")
    parser.add_argument("--config",  default="config.yaml", help="Config file path")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        cfg = yaml.safe_load(Path(args.config).read_text())
        folder = cfg["bridge"]["shared_folder"]
    except Exception:
        folder = "/Volumes/mt5bridge"  # default UTM shared folder mount

    client = MT5Client(shared_folder=folder)

    if args.ping:
        ok = client.ping()
        sys.exit(0 if ok else 1)
    elif args.tick:
        tick = client.get_tick()
        print(json.dumps(tick, indent=2) if tick else "No tick data")
    elif args.account:
        acc = client.get_account()
        print(json.dumps(acc, indent=2) if acc else "No account data")
    else:
        parser.print_help()
