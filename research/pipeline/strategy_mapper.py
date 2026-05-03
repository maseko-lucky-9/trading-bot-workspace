"""Map an LLM-extracted ``StrategyCandidate`` to one of the four existing
backtestable strategy types in ``bot/core/strategy/``.

Mapping rules (declared in plan):

| Pattern                       | Mapped type      |
|-------------------------------|------------------|
| crossover / MA cross          | ema_crossover    |
| band / oversold / mean revert | mean_reversion   |
| trend + structure / breakout  | trend_following  |
| spread / cointegration / pair | pairs_trading    |
| NN / ML / classifier          | mean_reversion   |

Parameter values are taken from the candidate's ``parameters`` dict where the
keys match (case-insensitive substring match against well-known parameter
names); otherwise sensible defaults from the existing strategy classes are
used.
"""
from __future__ import annotations

import re
from typing import Any

from .models import (
    EXISTING_STRATEGY_TYPES,
    MappedStrategy,
    StrategyCandidate,
)


# --------------------------------------------------------------------------- #
# Default parameters — sourced from existing Strategy implementations         #
# --------------------------------------------------------------------------- #

DEFAULT_PARAMS: dict[str, dict[str, Any]] = {
    "ema_crossover": {
        "ema_fast": 9,
        "ema_slow": 21,
        "atr_period": 14,
        "atr_sl_multiplier": 1.5,
        "atr_tp_multiplier": 3.0,
    },
    "mean_reversion": {
        "bb_period": 20,
        "bb_std": 2.0,
        "rsi_period": 14,
        "rsi_oversold": 30.0,
        "rsi_overbought": 70.0,
        "atr_sl_multiplier": 1.5,
        "atr_tp_multiplier": 2.0,
    },
    "trend_following": {
        "htf_resample_rule": "4h",
        "swing_left": 2,
        "swing_right": 2,
        "tp_r_multiple": 1.5,
        "atr_period": 14,
        "atr_sl_multiplier": 1.5,
        "sl_atr_buffer": 1.0,
        "reversal_lookback": 10,
        "mode": "standard",
    },
    "pairs_trading": {
        "symbol1": "EURUSD",
        "symbol2": "GBPUSD",
        "entry_zscore": 2.0,
        "spread_window": 60,
        "hedge_window": 60,
        "atr_sl_multiplier": 1.5,
    },
}


# --------------------------------------------------------------------------- #
# Pattern matchers (ordered by specificity — first match wins)                #
# --------------------------------------------------------------------------- #

_PAIRS_PATTERNS = [
    r"\bspread\b", r"\bcointegrat", r"\bhedge\s+ratio", r"\bpair[s]?\s+trad",
    r"\brelative\s+value", r"\bz[-\s]?score", r"\bstat[\.\s]?arb",
]
_TREND_PATTERNS = [
    r"\bbreakout\b", r"\bswing\s+(?:high|low|point)", r"\bhigher\s+high",
    r"\blower\s+low", r"\bmarket\s+structure", r"\btrend[\s-]+follow",
    r"\bbreak\s+of\s+structure\b", r"\bdonchian\b", r"\bchannel\s+breakout\b",
]
_MR_PATTERNS = [
    r"\bbollinger\b", r"\bband\b", r"\bmean[\s-]+revers", r"\boversold\b",
    r"\boverbought\b", r"\brsi\b", r"\bstochastic\b", r"\breversal\b",
    r"\bfade\b",
]
_CROSS_PATTERNS = [
    r"\bcross(?:over|ing)?\b", r"\bma\s+cross", r"\bmoving\s+average\s+cross",
    r"\bema\s+\d+\s*[/x]\s*\d+", r"\bsma\s+\d+\s*[/x]\s*\d+",
]
_ML_PATTERNS = [
    r"\bneural\b", r"\bml\s+model", r"\bclassifier\b", r"\brandom\s+forest\b",
    r"\bgradient\s+boost", r"\bdeep\s+learning\b", r"\bmeta[\s-]?label",
    r"\bsvm\b", r"\bautoencoder\b",
]


def _match_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def _classify(candidate: StrategyCandidate) -> str:
    """Return one of the four EXISTING_STRATEGY_TYPES."""
    haystack = " ".join(
        [candidate.name, candidate.hypothesis]
        + candidate.entry_rules
        + candidate.exit_rules
    ).lower()
    # parameter names are also a strong signal
    haystack += " " + " ".join(candidate.parameters.keys()).lower()

    if _match_any(haystack, _PAIRS_PATTERNS):
        return "pairs_trading"
    if _match_any(haystack, _CROSS_PATTERNS):
        return "ema_crossover"
    if _match_any(haystack, _TREND_PATTERNS):
        return "trend_following"
    if _match_any(haystack, _MR_PATTERNS):
        return "mean_reversion"
    if _match_any(haystack, _ML_PATTERNS):
        # ML/NN strategies map to mean_reversion per plan
        return "mean_reversion"
    # Final fallback — mean_reversion is the safest default for unknown
    # signals because its guard parameters (RSI thresholds + bands) tend to
    # produce few false positives.
    return "mean_reversion"


# --------------------------------------------------------------------------- #
# Parameter inference                                                         #
# --------------------------------------------------------------------------- #

# Maps canonical YAML keys to substrings the LLM might use.
_PARAM_ALIASES: dict[str, list[str]] = {
    "ema_fast": ["fast", "short_ma", "fast_period", "fast_ema", "fast_ma"],
    "ema_slow": ["slow", "long_ma", "slow_period", "slow_ema", "slow_ma"],
    "atr_period": ["atr_period", "atr_window"],
    "atr_sl_multiplier": ["sl_mult", "stop_atr", "sl_atr", "atr_sl"],
    "atr_tp_multiplier": ["tp_mult", "tp_atr", "atr_tp"],
    "bb_period": ["bb_period", "bollinger_period", "band_period"],
    "bb_std": ["bb_std", "bollinger_std", "band_std", "std_dev"],
    "rsi_period": ["rsi_period", "rsi_window"],
    "rsi_oversold": ["rsi_oversold", "oversold", "rsi_lower"],
    "rsi_overbought": ["rsi_overbought", "overbought", "rsi_upper"],
    "entry_zscore": ["zscore", "z_score", "entry_zscore", "entry_z"],
    "spread_window": ["spread_window", "lookback"],
    "tp_r_multiple": ["r_multiple", "rr", "risk_reward"],
}


def _coerce_number(value: Any) -> Any:
    """Try to coerce a value to int/float; pass through strings."""
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        s = value.strip().rstrip("%")
        try:
            if "." in s:
                return float(s)
            return int(s)
        except ValueError:
            return value
    return value


def _infer_params(strategy_type: str, candidate: StrategyCandidate) -> dict[str, Any]:
    """Overlay candidate parameters onto defaults for ``strategy_type``."""
    params = dict(DEFAULT_PARAMS[strategy_type])

    # Build a normalized lookup over the candidate parameter dict
    cand_norm: dict[str, Any] = {
        k.lower().replace(" ", "_").replace("-", "_"): _coerce_number(v)
        for k, v in candidate.parameters.items()
    }

    for canonical_key in list(params.keys()):
        if canonical_key not in _PARAM_ALIASES:
            continue
        for alias in _PARAM_ALIASES[canonical_key]:
            if alias in cand_norm:
                params[canonical_key] = cand_norm[alias]
                break

    # Strategy-specific sanity gates (mirror engine's silent fallback rules)
    if strategy_type == "ema_crossover":
        if (params.get("ema_fast", 0) >= params.get("ema_slow", 0)):
            params["ema_fast"] = 9
            params["ema_slow"] = 21
    if strategy_type == "mean_reversion":
        if not (0.0 < params.get("rsi_oversold", -1) < 50.0):
            params["rsi_oversold"] = 30.0
        if not (50.0 < params.get("rsi_overbought", -1) < 100.0):
            params["rsi_overbought"] = 70.0

    return params


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #

def map_strategy(candidate: StrategyCandidate) -> MappedStrategy:
    """Return a ``MappedStrategy`` ready to be written as a YAML overlay."""
    strategy_type = _classify(candidate)
    if strategy_type not in EXISTING_STRATEGY_TYPES:
        raise ValueError(f"Mapper produced unknown type: {strategy_type}")
    params = _infer_params(strategy_type, candidate)
    yaml_params: dict[str, Any] = {"strategy": strategy_type, **params}
    return MappedStrategy(
        candidate=candidate,
        mapped_type=strategy_type,
        yaml_params=yaml_params,
    )


__all__ = ["map_strategy", "DEFAULT_PARAMS"]
