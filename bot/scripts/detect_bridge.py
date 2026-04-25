"""
Detect the UTM shared folder mount point and update config.yaml automatically.
Run this after the Windows VM is started and the shared folder is mapped.
"""
import json
import os
import time
from pathlib import Path

import yaml

KNOWN_MOUNT_HINTS = [
    "/Volumes/mt5bridge",
    "/Volumes/share",
    "/Volumes/Shared",
    "/Volumes/SHARE",
]

CONFIG_PATH = Path(__file__).parent.parent.parent / "bot" / "config.yaml"


def find_bridge_folder() -> Path | None:
    # 1 — check hints first
    for p in KNOWN_MOUNT_HINTS:
        if Path(p).exists():
            return Path(p)

    # 2 — scan all volumes for heartbeat.json written by the EA
    for vol in Path("/Volumes").iterdir():
        if (vol / "heartbeat.json").exists():
            return vol

    # 3 — UTM may also mount via /tmp or ~/Library/Containers
    utm_share = Path.home() / "Library" / "Containers" / "com.utmapp.UTM" / "Data" / "Documents"
    if utm_share.exists():
        for d in utm_share.rglob("heartbeat.json"):
            return d.parent

    return None


def wait_for_mount(timeout: int = 60) -> Path | None:
    print(f"Scanning for MT5 bridge folder (timeout {timeout}s)...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        folder = find_bridge_folder()
        if folder:
            print(f"  Found: {folder}")
            return folder
        print("  Not found yet, retrying in 3s...")
        time.sleep(3)
    return None


def update_config(folder: Path):
    cfg_path = Path(__file__).resolve().parent.parent / "config.yaml"
    if not cfg_path.exists():
        cfg_path = Path(__file__).resolve().parent.parent.parent / "bot" / "config.yaml"

    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)

    cfg["bridge"]["shared_folder"] = str(folder)

    with open(cfg_path, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False)

    print(f"  config.yaml updated → bridge.shared_folder: {folder}")


def check_heartbeat(folder: Path) -> bool:
    hb = folder / "heartbeat.json"
    if not hb.exists():
        return False
    try:
        data = json.loads(hb.read_text())
        age = time.time() - data.get("time", 0)
        return age < 15
    except Exception:
        return False


if __name__ == "__main__":
    folder = wait_for_mount(timeout=60)
    if not folder:
        print("\nNot found. Checklist:")
        print("  1. Is the Windows VM running?")
        print("  2. Is PythonBridge EA attached to a chart with AutoTrading ON?")
        print("  3. In UTM → VM Settings → Sharing — is the shared folder enabled?")
        print("  4. In Windows, is the shared folder accessible (Z:\\ or \\\\mac\\)?")
        print("     Change EA input SHARED_FOLDER to that Windows path.")
        raise SystemExit(1)

    print(f"\nBridge folder found: {folder}")

    alive = check_heartbeat(folder)
    print(f"EA heartbeat: {'ALIVE' if alive else 'STALE — EA may not be running'}")

    update_config(folder)
    print("\nStart the bridge server:")
    print("  python core/bridge/http_server.py")
