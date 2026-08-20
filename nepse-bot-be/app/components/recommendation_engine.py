"""
Script Recommendation Engine
============================

Produces ranked buy/watch recommendations for NEPSE stocks by combining:

- **Trend strength**   : 20/50/200-day moving average alignment and slope.
- **Momentum**         : 14-period RSI sweet-spot (50–70) and MACD histogram.
- **Volume confirmation** : current vs. 20-day average traded volume.
- **Volatility control**: annualised volatility of daily log returns.
- **Drawdown buffer**  : distance from 252-day high (avoids chasing tops).

Design goals
------------
1. **Deterministic**: pure-function scoring; same data in → same ranking out.
2. **Source-agnostic**: accepts an OHLCV dict-of-DataFrames or loads from the
   ported `HistoricalDataProvider` (quant-terminal SQLite) so it works offline
   when the live NEPSE scraper is geo-blocked.
3. **Explainable**: every recommendation carries per-factor sub-scores and a
   human-readable rationale string — never a black box.

This module intentionally has no SQLAlchemy dependency: the recommendation
layer operates on pandas DataFrames and is easy to unit test.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, asdict
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Tunable factor weights (sum ≈ 1.0). Keep exposed for unit tests.             #
# --------------------------------------------------------------------------- #

WEIGHTS: Dict[str, float] = {
    "trend": 0.30,
    "momentum": 0.25,
    "volume": 0.15,
    "volatility": 0.15,
    "drawdown": 0.15,
}

# Minimum rows required to score a symbol. Prevents noisy picks from sparse
# data (IPOs with <60 trading days, suspended scrips, etc.).
MIN_HISTORY_DAYS = 60


# --------------------------------------------------------------------------- #
# Result dataclass                                                             #
# --------------------------------------------------------------------------- #

@dataclass
class Recommendation:
    """A single scored stock recommendation."""

    symbol: str
    action: str                 # BUY / WATCH / AVOID
    score: float                # 0..100
    last_close: float
    change_1d_pct: Optional[float]
    change_5d_pct: Optional[float]
    change_20d_pct: Optional[float]
    rsi_14: Optional[float]
    macd_hist: Optional[float]
    volume_ratio: Optional[float]
    volatility_annualised: Optional[float]
    drawdown_from_high_pct: Optional[float]
    factor_scores: Dict[str, float] = field(default_factory=dict)
    rationale: List[str] = field(default_factory=list)
    as_of_date: Optional[str] = None

    def as_dict(self) -> Dict:
        d = asdict(self)
        # Round for clean JSON output
        for k in (
            "score", "last_close", "change_1d_pct", "change_5d_pct",
            "change_20d_pct", "rsi_14", "macd_hist", "volume_ratio",
            "volatility_annualised", "drawdown_from_high_pct",
        ):
            v = d.get(k)
            if isinstance(v, float) and math.isfinite(v):
                d[k] = round(v, 4)
        d["factor_scores"] = {k: round(float(v), 4) for k, v in d["factor_scores"].items()}
        return d


# --------------------------------------------------------------------------- #
# Indicator helpers (local, no TA-Lib dependency)                              #
# --------------------------------------------------------------------------- #

def _sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)


def _macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def _annualised_volatility(close: pd.Series, window: int = 20) -> float:
    if len(close) < window + 1:
        return float("nan")
    log_ret = np.log(close / close.shift(1)).dropna()
    if log_ret.empty:
        return float("nan")
    recent = log_ret.iloc[-window:]
    return float(recent.std(ddof=0) * math.sqrt(252))


def _pct_change(series: pd.Series, periods: int) -> Optional[float]:
    if len(series) <= periods or periods <= 0:
        return None
    old = series.iloc[-periods - 1]
    new = series.iloc[-1]
    if old == 0 or pd.isna(old) or pd.isna(new):
        return None
    return float((new - old) / old * 100.0)


# --------------------------------------------------------------------------- #
# Per-factor sub-scores (each in 0..1)                                         #
# --------------------------------------------------------------------------- #

def _trend_score(close: pd.Series) -> float:
    """20 > 50 > 200 MA alignment with positive 20-day slope = strong uptrend."""
    if len(close) < 200:
        return 0.0
    sma20 = _sma(close, 20).iloc[-1]
    sma50 = _sma(close, 50).iloc[-1]
    sma200 = _sma(close, 200).iloc[-1]
    last = close.iloc[-1]
    if any(pd.isna(x) for x in (sma20, sma50, sma200)):
        return 0.0

    score = 0.0
    if last > sma20:
        score += 0.3
    if sma20 > sma50:
        score += 0.3
    if sma50 > sma200:
        score += 0.2

    # Slope of the 20-day MA over the last 10 days
    slope_window = _sma(close, 20).iloc[-10:].dropna()
    if len(slope_window) >= 2:
        slope = (slope_window.iloc[-1] - slope_window.iloc[0]) / slope_window.iloc[0]
        if slope > 0:
            score += 0.2
    return float(min(1.0, score))


def _momentum_score(close: pd.Series) -> tuple[float, Optional[float], Optional[float]]:
    if len(close) < 30:
        return 0.0, None, None
    rsi = _rsi(close).iloc[-1]
    _, _, hist = _macd(close)
    last_hist = hist.iloc[-1] if len(hist) else float("nan")

    # RSI sweet-spot: prefer 50–70 (trending up, not overbought).
    if pd.isna(rsi):
        rsi_component = 0.0
    elif 50 <= rsi <= 70:
        rsi_component = 1.0 - (abs(60 - rsi) / 20.0)  # peak at 60
    elif 40 <= rsi < 50:
        rsi_component = 0.4
    elif 30 <= rsi < 40:
        rsi_component = 0.2
    elif rsi > 70:
        rsi_component = max(0.0, 1.0 - (rsi - 70) / 15.0)
    else:  # rsi < 30 oversold
        rsi_component = 0.3

    # MACD histogram positive & increasing → momentum turning up
    if pd.isna(last_hist):
        macd_component = 0.0
    else:
        macd_component = 1.0 if last_hist > 0 else 0.0
        if len(hist) >= 3 and hist.iloc[-1] > hist.iloc[-2] > hist.iloc[-3]:
            macd_component = min(1.0, macd_component + 0.3)

    score = float(np.clip(0.6 * rsi_component + 0.4 * macd_component, 0.0, 1.0))
    return score, (None if pd.isna(rsi) else float(rsi)), (None if pd.isna(last_hist) else float(last_hist))


def _volume_score(volume: pd.Series) -> tuple[float, Optional[float]]:
    if len(volume) < 21:
        return 0.0, None
    avg20 = volume.iloc[-21:-1].mean()  # exclude today for a true "vs. average" ratio
    today = volume.iloc[-1]
    if avg20 <= 0 or pd.isna(avg20):
        return 0.0, None
    ratio = float(today / avg20)
    if ratio >= 2.5:
        comp = 1.0
    elif ratio >= 1.5:
        comp = 0.75
    elif ratio >= 1.0:
        comp = 0.45
    elif ratio >= 0.5:
        comp = 0.2
    else:
        comp = 0.05
    return comp, ratio


def _volatility_score(close: pd.Series) -> tuple[float, Optional[float]]:
    vol = _annualised_volatility(close)
    if math.isnan(vol):
        return 0.0, None
    # Sweet spot: 15–35 % annualised. Penalise extreme moves.
    if 0.15 <= vol <= 0.35:
        comp = 1.0
    elif vol < 0.15:
        comp = 0.4 + (vol / 0.15) * 0.3
    elif vol <= 0.6:
        comp = max(0.0, 1.0 - (vol - 0.35) / 0.25)
    else:
        comp = 0.0
    return float(comp), float(vol)


def _drawdown_score(close: pd.Series) -> tuple[float, Optional[float]]:
    window = min(252, len(close))
    if window < 30:
        return 0.0, None
    high = close.iloc[-window:].max()
    last = close.iloc[-1]
    if high <= 0 or pd.isna(high):
        return 0.0, None
    dd = float((last - high) / high * 100.0)  # <= 0
    # Prefer stocks that are 2-12 % off their 252-day high — pullback zone.
    if -12 <= dd <= -2:
        comp = 1.0
    elif -20 <= dd < -12:
        comp = 0.6
    elif -2 < dd <= 0:
        comp = 0.4
    elif -30 <= dd < -20:
        comp = 0.35
    else:
        comp = 0.1
    return comp, dd


# --------------------------------------------------------------------------- #
# Core scoring                                                                 #
# --------------------------------------------------------------------------- #

def score_symbol(
    symbol: str,
    df: pd.DataFrame,
    weights: Optional[Dict[str, float]] = None,
) -> Optional[Recommendation]:
    """
    Score one symbol's OHLCV DataFrame.

    The DataFrame must contain columns ``[date, open, high, low, close, volume]``
    (case insensitive). It must be sorted chronologically ascending.

    Returns ``None`` when there isn't enough data to score.
    """
    if df is None or df.empty:
        return None
    w = dict(WEIGHTS)
    if weights:
        w.update({k: float(v) for k, v in weights.items() if k in w})

    # Normalise columns (case-insensitive)
    cols = {c.lower(): c for c in df.columns}
    required = {"close"}
    if not required.issubset(cols.keys()):
        return None
    close = pd.to_numeric(df[cols["close"]], errors="coerce").dropna()
    volume = (
        pd.to_numeric(df[cols["volume"]], errors="coerce").fillna(0.0)
        if "volume" in cols
        else pd.Series([0.0] * len(close))
    )
    if len(close) < MIN_HISTORY_DAYS:
        return None

    # Per-factor scores
    trend = _trend_score(close)
    momentum, rsi_val, macd_hist_val = _momentum_score(close)
    vol_score, vol_ratio = _volume_score(volume)
    volatility, vol_annual = _volatility_score(close)
    drawdown, dd_pct = _drawdown_score(close)

    factor_scores = {
        "trend": round(trend, 4),
        "momentum": round(momentum, 4),
        "volume": round(vol_score, 4),
        "volatility": round(volatility, 4),
        "drawdown": round(drawdown, 4),
    }

    composite = (
        w["trend"] * trend
        + w["momentum"] * momentum
        + w["volume"] * vol_score
        + w["volatility"] * volatility
        + w["drawdown"] * drawdown
    )
    score_100 = float(round(np.clip(composite * 100.0, 0.0, 100.0), 2))

    if score_100 >= 65:
        action = "BUY"
    elif score_100 >= 45:
        action = "WATCH"
    else:
        action = "AVOID"

    rationale: List[str] = []
    if trend >= 0.8:
        rationale.append("Strong uptrend: price > 20MA > 50MA > 200MA")
    elif trend >= 0.5:
        rationale.append("Partial trend alignment")
    else:
        rationale.append("Weak or broken trend")
    if rsi_val is not None:
        if 50 <= rsi_val <= 70:
            rationale.append(f"RSI {rsi_val:.0f} in momentum sweet-spot")
        elif rsi_val > 70:
            rationale.append(f"RSI {rsi_val:.0f} overbought")
        elif rsi_val < 30:
            rationale.append(f"RSI {rsi_val:.0f} oversold")
    if vol_ratio is not None and vol_ratio >= 1.5:
        rationale.append(f"Volume {vol_ratio:.1f}× 20-day average")
    if vol_annual is not None:
        rationale.append(f"Ann. vol {vol_annual*100:.1f}%")
    if dd_pct is not None:
        rationale.append(f"{dd_pct:+.1f}% vs 252-day high")

    as_of = None
    if "date" in cols:
        as_of = str(df[cols["date"]].iloc[-1])[:10]

    return Recommendation(
        symbol=symbol.upper(),
        action=action,
        score=score_100,
        last_close=float(close.iloc[-1]),
        change_1d_pct=_pct_change(close, 1),
        change_5d_pct=_pct_change(close, 5),
        change_20d_pct=_pct_change(close, 20),
        rsi_14=rsi_val,
        macd_hist=macd_hist_val,
        volume_ratio=vol_ratio,
        volatility_annualised=vol_annual,
        drawdown_from_high_pct=dd_pct,
        factor_scores=factor_scores,
        rationale=rationale,
        as_of_date=as_of,
    )


def rank_universe(
    ohlcv_by_symbol: Dict[str, pd.DataFrame],
    top_n: int = 20,
    min_score: float = 0.0,
    weights: Optional[Dict[str, float]] = None,
) -> List[Recommendation]:
    """
    Rank a universe of symbols → top N recommendations by composite score.

    - ``ohlcv_by_symbol`` : mapping from symbol → OHLCV DataFrame (chronological).
    - ``top_n``           : truncate to this many (0 ⇒ return all ranked).
    - ``min_score``       : filter out scores below this threshold (0..100).
    - ``weights``         : optional override for factor weights.
    """
    scored: List[Recommendation] = []
    for symbol, df in ohlcv_by_symbol.items():
        try:
            rec = score_symbol(symbol, df, weights=weights)
        except Exception as exc:
            logger.debug("Scoring %s failed: %s", symbol, exc)
            continue
        if rec is None:
            continue
        if rec.score >= min_score:
            scored.append(rec)

    scored.sort(key=lambda r: r.score, reverse=True)
    if top_n and top_n > 0:
        scored = scored[:top_n]
    return scored


__all__ = [
    "Recommendation",
    "WEIGHTS",
    "MIN_HISTORY_DAYS",
    "score_symbol",
    "rank_universe",
]
