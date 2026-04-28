"""
Probe the MT5 HTTP bridge and report status.

Usage:
    python scripts/detect_bridge.py [--url URL] [--timeout N]

Exits 0 if bridge is reachable and ea_connected=true.
Exits 1 if bridge is reachable but ea_connected=false.
Exits 2 if bridge is unreachable.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

_BOT_ROOT = Path(__file__).resolve().parents[1]


def _read_bridge_url() -> str:
    cfg_path = _BOT_ROOT / "config.yaml"
    if cfg_path.exists():
        with cfg_path.open() as f:
            cfg = yaml.safe_load(f) or {}
        return (cfg.get("bridge") or {}).get("base_url", "http://localhost:8080")
    return "http://localhost:8080"


def probe(url: str, timeout: int) -> dict:
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/ping", timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError):
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe the MT5 HTTP bridge /ping endpoint")
    parser.add_argument("--url", default=None, help="Override bridge base URL from config.yaml")
    parser.add_argument("--timeout", type=int, default=5, help="Request timeout in seconds")
    args = parser.parse_args()

    url = args.url or _read_bridge_url()
    data = probe(url, args.timeout)

    if not data:
        print(f"UNREACHABLE  {url}/ping")
        print("  → Is the bridge server running? (bash scripts/start_bridge.sh)")
        print("  → Is the Windows VM online and the EA attached with AutoTrading ON?")
        return 2

    ea_connected = data.get("ea_connected", False)
    status = "OK" if ea_connected else "BRIDGE UP / EA DISCONNECTED"
    print(f"{status}  {url}/ping")
    for k, v in data.items():
        print(f"  {k}: {v}")

    return 0 if ea_connected else 1


if __name__ == "__main__":
    sys.exit(main())
