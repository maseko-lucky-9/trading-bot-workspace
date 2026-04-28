"""Local read-only web dashboard for the MT5 paper-trading bot.

Serves four polling JSON endpoints + a single static HTML page on
``127.0.0.1:8090``.  Read-only by design: the dashboard imports
``core.performance`` and ``core.regime`` and reads ``logs/trades.csv`` /
``bridge_data/history/*.parquet``, but never writes anywhere except its
own logs.

See ``dashboard/README.md`` for usage.
"""

# NOTE: do NOT re-export `app` here. Doing `from dashboard.app import app`
# binds the FastAPI instance as `dashboard.app`, shadowing the submodule
# and breaking `import dashboard.app as ...` (used by tests + monkeypatch).
# Entry points use the explicit `dashboard.app:app` factory string instead.
__all__: list[str] = []
