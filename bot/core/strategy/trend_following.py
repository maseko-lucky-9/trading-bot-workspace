"""
TrendFollowing strategy — FX GOAT Mastery Compendium implementation.

Top-down workflow:
    1. Resample the supplied (M15) frame to a higher timeframe (default H4).
    2. On the higher TF, classify the trend (HH/HL/LH/LL) into bias.
    3. On the original frame, detect swings + most-recent break of structure.
    4. **Standard mode**: emit a signal when BoS direction agrees with HTF bias.
    5. **Premium mode** (T03): additionally require the latest close to sit
       inside the 0.618-0.786 Fibonacci retracement of the most recent
       impulsive leg.

Stops are placed at the last opposing swing (with an ATR-based buffer) when
one is available; an ATR-multiple fallback is used otherwise. Take-profit is
calculated as a fixed multiple of the structural risk.

**v1.1 default calibration (2026-04-29):** ``sl_atr_buffer=1.0`` and
``tp_r_multiple=1.5``. The compendium prescribes a 1:2 R:R minimum, but the
empirical 38-day EUR/USD M15 backtest showed the original 0.25-ATR buffer +
1:2 target produces a 0% win rate (every BoS entry's structural stop is
clipped by ordinary noise before TP). The wider buffer is what restores a
viable win rate on real data. Documented as a deliberate v1 calibration; the
multi-leg partial-fill exit (book §3 Phase 4 — partial at 1:2 + BE-trail +
HTF target) remains on the future-roadmap path that would let us reach the
book's full 1:2+ profile.

The class is purely additive — the existing ``ema_crossover`` and
``bollinger_mean_reversion`` strategies are not affected.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import numpy as np
import pandas as pd

from core.strategy.base import Signal, Strategy
from core.strategy.indicators import atr as _atr_series
from core.strategy.structure import (
    classify_trend,
    detect_swings,
    last_break_of_structure,
)


Mode = Literal["standard", "premium"]


class TrendFollowing(Strategy):
    """FX GOAT-aligned top-down trend follower.

    Parameters
    ----------
    htf_resample_rule : str
        Pandas resample rule for the higher timeframe bias check. Default
        ``"4H"`` matches the compendium's "Daily/4H bias + 15m entry" pattern
        when the input frame is M15.
    swing_left, swing_right : int
        Fractal confirmation bars passed to :func:`detect_swings`.
    tp_r_multiple : float
        TP distance as a multiple of the structural risk (entry-to-SL).
        Compendium minimum is 2.0.
    atr_period : int
        ATR lookback used for the SL buffer and the ATR-fallback SL.
    atr_sl_multiplier : float
        Multiplier for the ATR-fallback SL when no recent opposing swing exists.
    sl_atr_buffer : float
        Fraction of ATR added beyond the swing for the structural SL buffer.
    reversal_lookback : int
        Number of HTF bars to inspect for an in-progress reversal short-circuit.
    mode : Literal["standard", "premium"]
        Standard = BoS + HTF bias align.
        Premium = Standard + Fibonacci 0.618-0.786 retracement zone confluence.
    """

    name = "trend_following"

    # Premium-zone Fibonacci window (compendium proxy for institutional zones).
    PREMIUM_FIB_LO = 0.618
    PREMIUM_FIB_HI = 0.786

    def __init__(
        self,
        htf_resample_rule: str = "4h",
        swing_left: int = 2,
        swing_right: int = 2,
        tp_r_multiple: float = 1.5,
        atr_period: int = 14,
        atr_sl_multiplier: float = 1.5,
        sl_atr_buffer: float = 1.0,
        reversal_lookback: int = 10,
        mode: Mode = "standard",
    ) -> None:
        if tp_r_multiple < 1.0:
            raise ValueError("tp_r_multiple must be >= 1.0 (compendium minimum 2.0)")
        if mode not in ("standard", "premium"):
            raise ValueError(f"mode must be 'standard' or 'premium', got {mode!r}")
        self.htf_resample_rule = htf_resample_rule
        self.swing_left = swing_left
        self.swing_right = swing_right
        self.tp_r_multiple = float(tp_r_multiple)
        self.atr_period = int(atr_period)
        self.atr_sl_multiplier = float(atr_sl_multiplier)
        self.sl_atr_buffer = float(sl_atr_buffer)
        self.reversal_lookback = int(reversal_lookback)
        self.mode = mode

    # ------------------------------------------------------------------ #
    # Internal helpers                                                   #
    # ------------------------------------------------------------------ #

    def _resample_to_htf(self, df: pd.DataFrame) -> pd.DataFrame:
        """Resample the input frame to the higher timeframe (OHLC aggregation)."""
        if "time" not in df.columns:
            raise ValueError("input frame must contain a 'time' column")
        work = df.copy()
        if not pd.api.types.is_datetime64_any_dtype(work["time"]):
            work["time"] = pd.to_datetime(work["time"], utc=True)
        work = work.set_index("time")
        agg = work.resample(self.htf_resample_rule).agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna()
        agg = agg.reset_index()
        return agg

    def _htf_bias_recent_history(self, htf_df: pd.DataFrame) -> list[str]:
        """Return the trend classification of the last ``reversal_lookback``
        suffixes of the HTF frame (one per bar). Used to spot an in-progress
        reversal — the trend in question must be **stable** to trade.
        """
        history: list[str] = []
        n = len(htf_df)
        # Need enough bars for one swing window; otherwise everything is "range".
        min_for_swing = self.swing_left + self.swing_right + 1
        for cut in range(max(min_for_swing, n - self.reversal_lookback), n + 1):
            sub = htf_df.iloc[:cut]
            sub_swings = detect_swings(sub, self.swing_left, self.swing_right)
            history.append(classify_trend(sub_swings))
        return history

    def _structural_sl(
        self,
        df: pd.DataFrame,
        swings_df: pd.DataFrame,
        direction: str,
        atr_value: float,
        entry: float,
    ) -> float:
        """Locate the most recent opposing swing and offset by ATR buffer.

        Falls back to an ATR-multiple SL when no opposing swing exists.
        """
        sh_levels = swings_df.loc[swings_df["swing_high"], "high"].to_numpy()
        sl_levels = swings_df.loc[swings_df["swing_low"], "low"].to_numpy()
        buffer = atr_value * self.sl_atr_buffer
        if direction == "bullish":
            if len(sl_levels) > 0:
                return float(sl_levels[-1] - buffer)
            return float(entry - self.atr_sl_multiplier * atr_value)
        # bearish
        if len(sh_levels) > 0:
            return float(sh_levels[-1] + buffer)
        return float(entry + self.atr_sl_multiplier * atr_value)

    def _premium_zone_check(
        self,
        df: pd.DataFrame,
        swings_df: pd.DataFrame,
        direction: str,
    ) -> bool:
        """Return True iff the latest close sits inside the 0.618-0.786 Fib
        retracement of the latest impulsive leg in ``direction``.

        The "impulsive leg" is the move from the most recent opposing swing
        to the most recent same-side swing:
            - bullish leg: last swing-low -> last swing-high
            - bearish leg: last swing-high -> last swing-low
        """
        sh_idx = np.where(swings_df["swing_high"].to_numpy())[0]
        sl_idx = np.where(swings_df["swing_low"].to_numpy())[0]
        sh_levels = swings_df["high"].to_numpy()
        sl_levels = swings_df["low"].to_numpy()
        last_close = float(df["close"].to_numpy()[-1])

        if direction == "bullish":
            if len(sl_idx) == 0 or len(sh_idx) == 0:
                return False
            leg_lo = float(sl_levels[sl_idx[-1]])
            leg_hi = float(sh_levels[sh_idx[-1]])
            if leg_hi <= leg_lo:
                return False
            span = leg_hi - leg_lo
            zone_top = leg_hi - self.PREMIUM_FIB_LO * span    # 0.618 retracement (closer to the high)
            zone_bot = leg_hi - self.PREMIUM_FIB_HI * span    # 0.786 retracement (deeper)
            return zone_bot <= last_close <= zone_top
        # bearish
        if len(sl_idx) == 0 or len(sh_idx) == 0:
            return False
        leg_hi = float(sh_levels[sh_idx[-1]])
        leg_lo = float(sl_levels[sl_idx[-1]])
        if leg_hi <= leg_lo:
            return False
        span = leg_hi - leg_lo
        zone_bot = leg_lo + self.PREMIUM_FIB_LO * span
        zone_top = leg_lo + self.PREMIUM_FIB_HI * span
        return zone_bot <= last_close <= zone_top

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute ATR — exposed so that callers / tests can introspect."""
        out = df.copy()
        out["atr"] = _atr_series(out, self.atr_period)
        return out

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        # --- preconditions ------------------------------------------------ #
        min_bars = max(
            self.swing_left + self.swing_right + 4,
            self.atr_period + 2,
            20,
        )
        if len(df) < min_bars:
            return Signal(action="HOLD", strength=0.0, reason="insufficient_bars")

        # --- HTF bias ----------------------------------------------------- #
        try:
            htf_df = self._resample_to_htf(df)
        except Exception as exc:  # malformed time column, etc.
            return Signal(action="HOLD", strength=0.0, reason=f"htf_resample_failed:{exc}")

        if len(htf_df) < self.swing_left + self.swing_right + 4:
            return Signal(action="HOLD", strength=0.0, reason="insufficient_htf_bars")

        htf_swings = detect_swings(htf_df, self.swing_left, self.swing_right)
        htf_bias = classify_trend(htf_swings)

        # Reversal short-circuit: if the most recent classification differs
        # from the prior dominant one, sit out.
        bias_history = self._htf_bias_recent_history(htf_df)
        if bias_history:
            prior = bias_history[:-1] if len(bias_history) > 1 else bias_history
            if (
                htf_bias in ("uptrend", "downtrend")
                and any(b in ("uptrend", "downtrend") and b != htf_bias for b in prior)
            ):
                return Signal(
                    action="HOLD",
                    strength=0.0,
                    reason="structural_reversal_in_progress",
                    meta={"htf_bias": htf_bias, "bias_history": bias_history},
                )

        if htf_bias == "range":
            return Signal(
                action="HOLD",
                strength=0.0,
                reason="htf_range_no_trade",
                meta={"htf_bias": htf_bias},
            )

        # --- Lower-TF BoS ------------------------------------------------- #
        ltf_swings = detect_swings(df, self.swing_left, self.swing_right)
        bos = last_break_of_structure(df, ltf_swings)
        if bos is None:
            return Signal(
                action="HOLD",
                strength=0.0,
                reason="no_break_of_structure",
                meta={"htf_bias": htf_bias},
            )

        bos_aligned = (
            (htf_bias == "uptrend" and bos["direction"] == "bullish")
            or (htf_bias == "downtrend" and bos["direction"] == "bearish")
        )
        if not bos_aligned:
            return Signal(
                action="HOLD",
                strength=0.0,
                reason="bos_against_htf_bias",
                meta={"htf_bias": htf_bias, "bos_direction": bos["direction"]},
            )

        # --- Premium zone (T03) ------------------------------------------ #
        if self.mode == "premium":
            in_zone = self._premium_zone_check(df, ltf_swings, bos["direction"])
            if not in_zone:
                return Signal(
                    action="HOLD",
                    strength=0.0,
                    reason="not_in_premium_zone",
                    meta={
                        "htf_bias": htf_bias,
                        "bos_direction": bos["direction"],
                        "mode": self.mode,
                    },
                )

        # --- Build the signal -------------------------------------------- #
        ind = self.compute_indicators(df)
        last = ind.iloc[-1]
        close = float(last["close"])
        atr_val = float(last["atr"]) if not np.isnan(last["atr"]) else 0.0

        sl = self._structural_sl(df, ltf_swings, bos["direction"], atr_val, close)
        risk = abs(close - sl)
        if risk <= 0:
            return Signal(action="HOLD", strength=0.0, reason="zero_risk")

        if bos["direction"] == "bullish":
            tp = close + self.tp_r_multiple * risk
            action = "BUY"
        else:
            tp = close - self.tp_r_multiple * risk
            action = "SELL"

        thesis = (
            f"htf_bias={htf_bias} | bos={bos['direction']} | mode={self.mode} | "
            f"R:R=1:{self.tp_r_multiple:.1f}"
        )

        meta: dict = {
            "htf_bias": htf_bias,
            "bos_direction": bos["direction"],
            "bos_level": bos["level"],
            "swing_sl": sl,
            "atr": atr_val,
            "mode": self.mode,
            "trade_thesis": thesis,
            "entry_price": close,
            "sl": sl,
            "tp": tp,
        }
        ts = last["time"] if "time" in ind.columns else datetime.now(tz=timezone.utc)
        if isinstance(ts, pd.Timestamp):
            ts = ts.to_pydatetime()
        # Strength scales with how decisively the bos broke prior structure.
        sep = abs(close - bos["level"]) / max(abs(close), 1e-9)
        strength = float(min(1.0, sep * 1000.0))
        return Signal(
            action=action, strength=strength, reason="bos_aligned_with_htf_bias",
            timestamp=ts, meta=meta,
        )
