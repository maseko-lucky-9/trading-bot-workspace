"""Regime detector (Wave 3 — HMM / volatility-based, F14).

Classifies each bar into one of two regimes:

* ``TREND`` (0) — low-volatility, directional price action
* ``RANGE`` (1) — high-volatility, mean-reverting / choppy price action

Two methods are supported:

``"vol"`` (default)
    No external dependency.  Computes a rolling standard-deviation of
    log-returns and classifies each bar as TREND when the vol is at or
    below the rolling median, RANGE otherwise.  Robust, fast, and
    deterministic.

``"hmm"``
    Fits a 2-state Gaussian HMM (via *hmmlearn*) to the return series.
    Assigns the high-variance state to RANGE (1) and the low-variance
    state to TREND (0).  More principled but requires a warm-up period
    and is non-deterministic across random seeds unless ``random_state``
    is fixed.  Falls back silently to the vol method if *hmmlearn* is not
    installed or the fit fails.

Usage::

    from core.regime.detector import RegimeDetector

    det = RegimeDetector(method="vol", window=20)
    regime_series = det.detect(df)          # pd.Series[int], same index as df
    current = det.current_regime(df)        # int scalar — last bar's regime
"""
from __future__ import annotations

import numpy as np
import pandas as pd


class RegimeDetector:
    """Classify OHLCV bars as trending (0) or ranging (1)."""

    TREND = 0
    RANGE = 1

    def __init__(
        self,
        method: str = "vol",
        window: int = 20,
        n_states: int = 2,
        random_state: int = 42,
    ) -> None:
        if method not in ("vol", "hmm"):
            raise ValueError(f"method must be 'vol' or 'hmm', got {method!r}")
        self.method = method
        self.window = window
        self.n_states = n_states
        self.random_state = random_state
        self._hmm: object | None = None  # lazy-init GaussianHMM

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #

    def detect(self, df: pd.DataFrame) -> pd.Series:
        """Return an integer regime series aligned to *df*'s index.

        Values are ``RegimeDetector.TREND`` (0) or ``RegimeDetector.RANGE`` (1).
        """
        if self.method == "hmm":
            return self._hmm_regime(df)
        return self._vol_regime(df)

    def current_regime(self, df: pd.DataFrame) -> int:
        """Return the regime of the last bar in *df*."""
        return int(self.detect(df).iloc[-1])

    @classmethod
    def from_config(cls, config: dict) -> "RegimeDetector":
        """Build from the top-level bot config dict.

        Checks ``filters.regime`` first; falls back to the legacy
        ``strategy_config.regime`` key so existing configs continue to work.
        """
        cfg = (config.get("filters") or {}).get("regime")
        if cfg is None:
            cfg = (config.get("strategy_config") or {}).get("regime") or {}
        return cls(
            method=str(cfg.get("method", "vol")),
            window=int(cfg.get("window", 20)),
        )

    # ------------------------------------------------------------------ #
    # Private methods                                                    #
    # ------------------------------------------------------------------ #

    def _vol_regime(self, df: pd.DataFrame) -> pd.Series:
        """Classify by rolling log-return volatility vs its median."""
        log_ret = np.log(df["close"] / df["close"].shift(1))
        rolling_vol = log_ret.rolling(self.window).std()
        med = rolling_vol.median()
        regime = (rolling_vol > med).fillna(False).astype(int)
        regime.index = df.index
        return regime

    def _hmm_regime(self, df: pd.DataFrame) -> pd.Series:
        """Classify via a 2-state Gaussian HMM on log-returns.

        Falls back to vol-based classification if *hmmlearn* is unavailable
        or if the fit fails (insufficient data, degenerate covariance, etc.).
        """
        try:
            from hmmlearn.hmm import GaussianHMM
        except ImportError:
            return self._vol_regime(df)

        log_ret = np.log(df["close"] / df["close"].shift(1)).fillna(0.0)
        obs = log_ret.values.reshape(-1, 1)

        if len(obs) < self.window * 2:
            return self._vol_regime(df)

        if self._hmm is None:
            self._hmm = GaussianHMM(
                n_components=self.n_states,
                covariance_type="full",
                n_iter=100,
                random_state=self.random_state,
            )

        try:
            self._hmm.fit(obs)  # type: ignore[attr-defined]
            states = self._hmm.predict(obs)  # type: ignore[attr-defined]
        except Exception:
            return self._vol_regime(df)

        # Map the higher-variance HMM state to RANGE (1)
        state_vars = [
            float(obs[states == s].var()) if (states == s).any() else 0.0
            for s in range(self.n_states)
        ]
        high_var_state = int(np.argmax(state_vars))
        mapped = np.where(states == high_var_state, self.RANGE, self.TREND)
        return pd.Series(mapped, index=df.index, dtype=int)
