"""Entrypoint: ``python -m dashboard``.

Binds uvicorn explicitly to 127.0.0.1 — never 0.0.0.0 — to keep the
dashboard off the LAN. Port is fixed at 8090; override via the
``DASHBOARD_PORT`` env var if needed.
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = "127.0.0.1"  # locked: never expose to LAN
    port = int(os.environ.get("DASHBOARD_PORT", "8090"))
    uvicorn.run(
        "dashboard.app:app",
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
