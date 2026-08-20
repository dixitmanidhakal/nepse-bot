"""
Market Regime Detection

Lightweight regime classifier adapted from nepse-quant-terminal.

Uses a rolling-window return on the NEPSE benchmark to classify the market
into one of three regimes:

    bull    : rolling return above `bull_threshold`
    bear    : rolling return below `bear_threshold` (negative)
    neutral : everything in between

An exposure multiplier is returned to scale capital deployment:
    bull    -> 1.00   (full exposure)
    neutral -> 0.70   (reduced exposure)
    bear    -> 0.30   (capital preservation)

This is the *rule-based* classifier — pandas + numpy only, no scipy/hmmlearn.
The heavier HMM / BOCPD detectors from the quant-terminal project can be
added later if scipy and hmmlearn are installed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, Sequence

import numpy as np
import pandas as pd

Regime = Literal["bull", "neutral", "bear"]


@dataclass
class RegimeResult:
    regime: Regime
    rolling_return: float
    volatility: float
    window: int
    exposure_multiplier: float

    def as_dict(self) -> dict:
        return {
            "regime": self.regime,
            "rolling_return": self.rolling_return,
            "volatility": self.volatility,
            "window": self.window,
            "exposure_multiplier": self.exposure_multiplier,
        }


EXPOSURE_BY_REGIME: dict[Regime, float] = {
    "bull": 1.00,
    "neutral": 0.70,
    "bear": 0.30,
}


def classify_regime(
    closes: Sequence[float] | pd.Series,
    window: int = 60,
    bull_threshold: float = 0.05,
    bear_threshold: float = -0.05,
) -> RegimeResult:
    """
    Classify the current market regime from a series of benchmark closes.

    Args:
        closes: Chronologically ordered close prices of the NEPSE benchmark.
                Must contain at least `window + 1` points.
        window: Rolling window length in trading days (default 60).
        bull_threshold: Rolling-return cutoff to qualify as bull (default +5%).
        bear_threshold: Rolling-return cutoff to qualify as bear (default -5%).

    Returns:
        RegimeResult with regime label, rolling return, realised vol, and
        the recommended exposure multiplier.

    Raises:
        ValueError: If the series is too short or contains non-numeric data.
    """
    series = pd.Series(closes, dtype=float).reset_index(drop=True)
    series = series.dropna()

    if len(series) < window + 1:
        raise ValueError(
            f"need at least {window + 1} close prices; got {len(series)}"
        )

    last = float(series.iloc[-1])
    anchor = float(series.iloc[-(window + 1)])
    if anchor == 0:
        raise ValueError("anchor close is zero, cannot compute rolling return")

    rolling_return = (last - anchor) / anchor

    returns = series.pct_change().dropna().iloc[-window:]
    # Annualised realised vol assuming ~252 trading days.
    volatility = float(returns.std(ddof=1) * np.sqrt(252)) if len(returns) > 1 else 0.0

    if rolling_return >= bull_threshold:
        regime: Regime = "bull"
    elif rolling_return <= bear_threshold:
        regime = "bear"
    else:
        regime = "neutral"

    return RegimeResult(
        regime=regime,
        rolling_return=float(rolling_return),
        volatility=volatility,
        window=window,
        exposure_multiplier=EXPOSURE_BY_REGIME[regime],
    )


def classify_regime_from_returns(
    returns: Sequence[float] | pd.Series,
    window: int = 60,
    bull_threshold: float = 0.05,
    bear_threshold: float = -0.05,
) -> RegimeResult:
    """
    Alternative entry point: classify regime from a series of daily returns.

    Computes synthetic cumulative close prices, then delegates to
    `classify_regime`.
    """
    r = pd.Series(returns, dtype=float).dropna()
    if len(r) < window:
        raise ValueError(f"need at least {window} returns; got {len(r)}")
    synthetic_closes = (1.0 + r).cumprod()
    synthetic_closes = pd.concat(
        [pd.Series([1.0]), synthetic_closes], ignore_index=True
    )
    return classify_regime(
        synthetic_closes, window=window,
        bull_threshold=bull_threshold, bear_threshold=bear_threshold,
    )


__all__ = [
    "Regime",
    "RegimeResult",
    "EXPOSURE_BY_REGIME",
    "classify_regime",
    "classify_regime_from_returns",
]
