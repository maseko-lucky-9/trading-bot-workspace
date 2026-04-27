"""Meta-labelling wrapper (Wave 3 — AFML Ch. 3, F-meta).

Wraps any ``Strategy`` with a secondary ML classifier that estimates the
probability that the base signal will be profitable.  Only signals above a
configurable threshold are passed through; others are converted to HOLD.

The classifier (logistic regression by default, gradient-boosted trees as a
richer alternative) is trained incrementally on *closed* trade outcomes.
Training begins only when at least ``min_train_trades`` labelled examples
are available, and the model is retrained every ``retrain_every`` new trades
to stay current.

Features extracted per signal bar (7 dimensions)
-------------------------------------------------
ret_1    — 1-bar log return
ret_5    — 5-bar log return
ret_20   — 20-bar log return
atr_norm — ATR(14) / close  (normalised volatility)
rsi_norm — RSI(14) / 100    (momentum, 0..1)
ema_spread — (EMA12 - EMA26) / close  (trend strength)
regime   — 0 = TREND, 1 = RANGE  (from RegimeDetector if provided)

Usage::

    from core.strategy.meta_labeller import MetaLabeller
    from core.strategy.ema_crossover import EMACrossover

    base = EMACrossover(fast=9, slow=21)
    ml   = MetaLabeller(base, threshold=0.55)

    # ... after each closed trade:
    ml.record_outcome(features, profit)

    # At signal time:
    signal = ml.generate_signal(df)
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from core.strategy.base import Signal, Strategy
from core.strategy.indicators import atr as _atr_series

# Avoid circular import: regime detector imported lazily inside from_config


def _build_base_strategy(strategy_name: str, params: dict) -> Strategy:
    """Construct the named base strategy from a params dict."""
    if strategy_name == "ema_crossover":
        from core.strategy.ema_crossover import EMACrossover
        return EMACrossover(
            fast=int(params.get("ema_fast", 9)),
            slow=int(params.get("ema_slow", 21)),
            atr_period=int(params.get("atr_period", 14)),
            atr_sl_multiplier=float(params.get("atr_multiplier", 1.5)),
            atr_tp_multiplier=float(params.get("atr_multiplier", 1.5)) * 2.0,
        )
    if strategy_name == "mean_reversion":
        from core.strategy.mean_reversion import BollingerBandMeanReversion
        return BollingerBandMeanReversion(
            bb_period=int(params.get("bb_period", 20)),
            bb_std=float(params.get("bb_std", 2.0)),
            atr_period=int(params.get("atr_period", 14)),
            atr_sl_multiplier=float(params.get("atr_multiplier", 1.5)),
        )
    raise ValueError(f"Unknown strategy: {strategy_name!r}")


class MetaLabeller(Strategy):
    """Probability-of-profit filter layered on top of a base strategy."""

    name = "meta_labeller"

    def __init__(
        self,
        base_strategy: Strategy,
        threshold: float = 0.55,
        min_train_trades: int = 20,
        retrain_every: int = 10,
        regime_detector: object | None = None,
        use_gradient_boost: bool = False,
    ) -> None:
        self._base = base_strategy
        self.threshold = threshold
        self.min_train_trades = min_train_trades
        self.retrain_every = retrain_every
        self._regime = regime_detector
        self._use_gb = use_gradient_boost
        self._model: object | None = None
        self._trained = False
        self._buffer: list[tuple[np.ndarray, int]] = []
        self._trades_since_retrain = 0
        self._last_features: np.ndarray | None = None

    # ------------------------------------------------------------------ #
    # Feature extraction                                                 #
    # ------------------------------------------------------------------ #

    def extract_features(self, df: pd.DataFrame) -> np.ndarray:
        """Extract a fixed-length feature vector from the last bar of *df*."""
        close = df["close"].values.astype(float)
        n = len(close)

        # Log returns at three horizons
        ret1  = math.log(close[-1] / close[-2]) if n >= 2 and close[-2] > 0 else 0.0
        ret5  = math.log(close[-1] / close[-6]) if n >= 6 and close[-6] > 0 else 0.0
        ret20 = math.log(close[-1] / close[-21]) if n >= 21 and close[-21] > 0 else 0.0

        # ATR-normalised volatility
        atr_raw = float(_atr_series(df, 14).iloc[-1])
        atr_norm = atr_raw / close[-1] if close[-1] > 0 else 0.0

        # RSI (Wilder EWM)
        prices = pd.Series(close)
        delta = prices.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        loss = (-delta).clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        rs = float(gain.iloc[-1]) / float(loss.iloc[-1]) if float(loss.iloc[-1]) > 0 else 0.0
        rsi_norm = (100.0 - 100.0 / (1.0 + rs)) / 100.0

        # EMA spread
        ema12 = float(prices.ewm(span=12, adjust=False).mean().iloc[-1])
        ema26 = float(prices.ewm(span=26, adjust=False).mean().iloc[-1])
        ema_spread = (ema12 - ema26) / close[-1] if close[-1] > 0 else 0.0

        # Regime
        regime = 0
        if self._regime is not None:
            try:
                regime = int(self._regime.current_regime(df))  # type: ignore[attr-defined]
            except Exception:
                regime = 0

        features = np.array(
            [ret1, ret5, ret20, atr_norm, rsi_norm, ema_spread, regime],
            dtype=float,
        )
        # Replace any non-finite value with 0 (degenerate data guard)
        features = np.where(np.isfinite(features), features, 0.0)
        return features

    # ------------------------------------------------------------------ #
    # Training                                                           #
    # ------------------------------------------------------------------ #

    def record_outcome(self, features: np.ndarray, profit: float) -> None:
        """Record a labelled trade outcome and retrain when threshold is met."""
        label = 1 if profit > 0 else 0
        self._buffer.append((features, label))
        self._trades_since_retrain += 1
        if (
            len(self._buffer) >= self.min_train_trades
            and self._trades_since_retrain >= self.retrain_every
        ):
            self._retrain()

    def _retrain(self) -> None:
        X = np.array([f for f, _ in self._buffer], dtype=float)
        y = np.array([l for _, l in self._buffer], dtype=int)
        if len(np.unique(y)) < 2:
            return  # need at least one positive and one negative example
        try:
            if self._use_gb:
                from sklearn.ensemble import GradientBoostingClassifier
                model = GradientBoostingClassifier(n_estimators=50, max_depth=3, random_state=42)
            else:
                from sklearn.linear_model import LogisticRegression
                model = LogisticRegression(max_iter=1000, random_state=42)
            model.fit(X, y)
            self._model = model
            self._trained = True
            self._trades_since_retrain = 0
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Strategy interface                                                 #
    # ------------------------------------------------------------------ #

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        """Return the base signal filtered by the probability-of-profit model."""
        base_sig = self._base.generate_signal(df)

        if not self._trained or base_sig.action == "HOLD":
            # Store features so the caller can label this signal after the
            # trade closes without having to re-compute them.
            if base_sig.action != "HOLD":
                self._last_features = self.extract_features(df)
            return base_sig

        try:
            features = self.extract_features(df)
            self._last_features = features
            if not np.all(np.isfinite(features)):
                return base_sig

            prob = float(
                self._model.predict_proba(features.reshape(1, -1))[0][1]  # type: ignore[attr-defined]
            )
            if prob < self.threshold:
                return Signal(
                    action="HOLD",
                    strength=prob,
                    reason=f"meta_filtered p={prob:.2f}",
                    meta=base_sig.meta,
                )
            return Signal(
                action=base_sig.action,
                strength=prob,
                reason=base_sig.reason,
                timestamp=base_sig.timestamp,
                meta=base_sig.meta,
            )
        except Exception:
            return base_sig

    # ------------------------------------------------------------------ #
    # Construction helpers                                               #
    # ------------------------------------------------------------------ #

    @classmethod
    def from_config(
        cls,
        config: dict,
        base_strategy: Strategy | None = None,
        params: dict | None = None,
    ) -> "MetaLabeller":
        """Build from the top-level bot config dict.

        Reads ``meta_labeller`` under ``filters`` (canonical) or at the top
        level (legacy).  ``base_strategy`` overrides construction from params.

        Parameters
        ----------
        config:
            Full bot config dict (from config.yaml).
        base_strategy:
            Pre-built Strategy instance.  If omitted, one is constructed
            from ``params`` + ``config.bot.instruments[0]`` strategy.
        params:
            Autoresearch params dict used to build the base strategy when
            ``base_strategy`` is None.
        """
        cfg = (config.get("filters") or {}).get("meta_labeller") or \
              config.get("meta_labeller") or {}

        threshold = float(cfg.get("threshold", 0.55))
        min_train = int(cfg.get("min_train_trades", 20))
        retrain_every = int(cfg.get("retrain_every", 10))
        use_gb = bool(cfg.get("use_gradient_boost", False))

        if base_strategy is None and params is not None:
            strat_name = str(params.get("strategy", "ema_crossover"))
            base_strategy = _build_base_strategy(strat_name, params)

        if base_strategy is None:
            from core.strategy.ema_crossover import EMACrossover
            base_strategy = EMACrossover()

        regime_det = None
        regime_cfg = (config.get("filters") or {}).get("regime") or {}
        if bool(regime_cfg.get("enabled", True)):
            from core.regime.detector import RegimeDetector
            regime_det = RegimeDetector.from_config(config)

        return cls(
            base_strategy=base_strategy,
            threshold=threshold,
            min_train_trades=min_train,
            retrain_every=retrain_every,
            regime_detector=regime_det,
            use_gradient_boost=use_gb,
        )

    @property
    def base_strategy(self) -> Strategy:
        """Expose underlying base strategy for introspection."""
        return self._base
