"""
Session-scoped fixtures shared across all test files.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest


@pytest.fixture(scope="session")
def ohlcv_200() -> pd.DataFrame:
    """Deterministic 200-bar OHLCV DataFrame seeded for reproducibility."""
    rng = np.random.default_rng(42)
    n = 200
    close = 1.10 + np.cumsum(rng.normal(0, 0.0005, n))
    spread = 0.00002
    high = close + rng.uniform(0.0001, 0.0010, n)
    low = close - rng.uniform(0.0001, 0.0010, n)
    open_ = close + rng.normal(0, 0.0003, n)
    volume = rng.integers(100, 1000, n).astype(float)
    times = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame(
        {"time": times, "open": open_, "high": high, "low": low, "close": close, "volume": volume}
    )


@pytest.fixture(scope="session")
def mock_bridge() -> MagicMock:
    """MagicMock of MT5BridgeClient with canned responses."""
    b = MagicMock()
    b.is_connected.return_value = True
    b.ping.return_value = True
    b.get_tick.return_value = {"symbol": "EURUSD", "bid": 1.10000, "ask": 1.10002, "spread": 2.0}
    b.get_account.return_value = {"balance": 10000.0, "equity": 10000.0}
    b.get_state.return_value = {
        "tick": {"symbol": "EURUSD", "bid": 1.10000, "ask": 1.10002, "spread": 2.0},
        "account": {"balance": 10000.0, "equity": 10000.0},
        "connected": True,
    }
    b.send_order.return_value = {"ok": True, "ticket": 1}
    return b


@pytest.fixture(scope="session")
def utc_now() -> datetime:
    """Pinned UTC datetime for deterministic timestamp assertions."""
    return datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
