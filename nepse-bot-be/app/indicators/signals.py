"""
Quant Signal Generators

Lightweight standalone implementations of the signal families documented
in the nepse-quant-terminal README:

    volume          - volume breakout above N-day average with price confirmation
    low_vol         - low realised volatility with positive momentum
    mean_reversion  - RSI-oversold plus distance below 52-week high
    xsec_momentum   - cross-sectional 6m-minus-1m momentum (skip last month)

Each generator is a pure function operating on a symbol-indexed price panel:
    prices_df : DataFrame with a DatetimeIndex and one column per symbol,
                filled with adjusted close prices.

Each returns a list of scored candidates:
    [{"symbol": str, "score": float in [0, 1], "reason": str}, ...]

These are re-implementations following the publicly documented formulas —
no code from the quant-terminal is used directly here, so the same module
does not drag in any of its DB / runtime assumptions.
"""

from __future__ import annotations

from typing import List, Dict

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _safe_series(col: pd.Series) -> pd.Series:
    return col.dropna().astype(float)


def _rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    """Classic Wilder RSI."""
    delta = closes.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)


def _score_from_zscore(z: float, lo: float = -2.0, hi: float = 2.0) -> float:
    """Clip z-score into [0, 1] — higher z → higher score."""
    z = float(np.clip(z, lo, hi))
    return (z - lo) / (hi - lo)


# --------------------------------------------------------------------------- #
# Volume breakout                                                              #
# --------------------------------------------------------------------------- #

def volume_breakout_signals(
    prices_df: pd.DataFrame,
    volumes_df: pd.DataFrame,
    lookback: int = 20,
    min_ratio: float = 1.5,
) -> List[Dict]:
    """
    Flag symbols whose latest volume is ≥ `min_ratio` × its N-day average
    AND whose latest close is above the lookback close.
    """
    out: List[Dict] = []
    for symbol in prices_df.columns:
        closes = _safe_series(prices_df[symbol])
        vols = _safe_series(volumes_df[symbol]) if symbol in volumes_df else pd.Series(dtype=float)
        if len(closes) < lookback + 1 or len(vols) < lookback + 1:
            continue
        latest_vol = vols.iloc[-1]
        avg_vol = vols.iloc[-(lookback + 1): -1].mean()
        if avg_vol <= 0:
            continue
        ratio = latest_vol / avg_vol
        price_confirm = closes.iloc[-1] > closes.iloc[-(lookback + 1)]
        if ratio >= min_ratio and price_confirm:
            score = float(np.clip((ratio - min_ratio) / (3.0 - min_ratio), 0.0, 1.0))
            out.append({
                "symbol": symbol,
                "score": round(score, 4),
                "reason": f"volume {ratio:.1f}× avg, price above {lookback}d ago",
            })
    out.sort(key=lambda d: d["score"], reverse=True)
    return out


# --------------------------------------------------------------------------- #
# Low volatility + positive momentum                                           #
# --------------------------------------------------------------------------- #

def low_volatility_signals(
    prices_df: pd.DataFrame,
    window: int = 60,
    top_pct: float = 0.30,
) -> List[Dict]:
    """
    Rank symbols by low `window`-day realised volatility, keeping only those
    with positive `window`-day return. Scores favour the lowest-vol names.
    """
    candidates: List[Dict] = []
    for symbol in prices_df.columns:
        closes = _safe_series(prices_df[symbol])
        if len(closes) < window + 1:
            continue
        returns = closes.pct_change().dropna().iloc[-window:]
        if len(returns) < window:
            continue
        vol = float(returns.std(ddof=1))
        mom = float((closes.iloc[-1] - closes.iloc[-(window + 1)]) / closes.iloc[-(window + 1)])
        if mom <= 0 or vol <= 0:
            continue
        candidates.append({"symbol": symbol, "vol": vol, "mom": mom})

    if not candidates:
        return []

    vols = np.array([c["vol"] for c in candidates])
    lo, hi = vols.min(), vols.max()
    span = hi - lo if hi > lo else 1.0
    # keep top_pct with the lowest vol
    cutoff_idx = int(max(1, len(candidates) * top_pct))
    candidates.sort(key=lambda c: c["vol"])
    out: List[Dict] = []
    for c in candidates[:cutoff_idx]:
        score = 1.0 - (c["vol"] - lo) / span  # inverted: lower vol ⇒ higher score
        out.append({
            "symbol": c["symbol"],
            "score": round(float(score), 4),
            "reason": f"vol={c['vol']:.3f}, {window}d mom={c['mom']:.2%}",
        })
    return out


# --------------------------------------------------------------------------- #
# Mean reversion                                                               #
# --------------------------------------------------------------------------- #

def mean_reversion_signals(
    prices_df: pd.DataFrame,
    rsi_period: int = 14,
    rsi_oversold: float = 30.0,
    high_window: int = 252,
    min_discount: float = 0.15,
) -> List[Dict]:
    """
    Surface oversold names:
        RSI(14) < `rsi_oversold` AND
        close is at least `min_discount` below its `high_window` high.
    """
    out: List[Dict] = []
    for symbol in prices_df.columns:
        closes = _safe_series(prices_df[symbol])
        if len(closes) < max(high_window, rsi_period * 2):
            continue
        rsi_val = float(_rsi(closes, rsi_period).iloc[-1])
        if rsi_val >= rsi_oversold:
            continue
        window_high = float(closes.iloc[-high_window:].max())
        if window_high <= 0:
            continue
        discount = (window_high - float(closes.iloc[-1])) / window_high
        if discount < min_discount:
            continue
        rsi_part = (rsi_oversold - rsi_val) / rsi_oversold
        disc_part = float(np.clip((discount - min_discount) / 0.35, 0.0, 1.0))
        score = float(np.clip(0.5 * rsi_part + 0.5 * disc_part, 0.0, 1.0))
        out.append({
            "symbol": symbol,
            "score": round(score, 4),
            "reason": f"rsi={rsi_val:.1f}, {discount:.1%} below {high_window}d high",
        })
    out.sort(key=lambda d: d["score"], reverse=True)
    return out


# --------------------------------------------------------------------------- #
# Cross-sectional 6m-minus-1m momentum                                         #
# --------------------------------------------------------------------------- #

def xsec_momentum_signals(
    prices_df: pd.DataFrame,
    long_window: int = 126,   # ≈ 6 months of trading days
    skip_window: int = 21,    # ≈ 1 month
) -> List[Dict]:
    """
    Cross-sectional momentum with a 1-month skip to avoid short-term reversal:
        momentum(t) = return(t-skip_window, t - long_window)
    Score is the cross-sectional rank in (0, 1].
    """
    per_symbol = []
    for symbol in prices_df.columns:
        closes = _safe_series(prices_df[symbol])
        if len(closes) < long_window + skip_window + 1:
            continue
        end_idx = -(skip_window + 1)
        start_idx = -(long_window + skip_window + 1)
        end_price = float(closes.iloc[end_idx])
        start_price = float(closes.iloc[start_idx])
        if start_price <= 0:
            continue
        ret = (end_price - start_price) / start_price
        per_symbol.append({"symbol": symbol, "ret": ret})

    if len(per_symbol) < 2:
        return []

    per_symbol.sort(key=lambda d: d["ret"], reverse=True)
    out: List[Dict] = []
    n = len(per_symbol)
    for rank, entry in enumerate(per_symbol):
        score = 1.0 - (rank / n)
        out.append({
            "symbol": entry["symbol"],
            "score": round(float(score), 4),
            "reason": f"6m-1m ret={entry['ret']:.2%} (rank {rank + 1}/{n})",
        })
    return out


__all__ = [
    "volume_breakout_signals",
    "low_volatility_signals",
    "mean_reversion_signals",
    "xsec_momentum_signals",
]
