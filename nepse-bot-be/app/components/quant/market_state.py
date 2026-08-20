"""
NEPSE Market State Detector — Phase 2 Meta-Switcher

Determines whether the current market is TRENDING or CHOPPY using four
real-time, no-lookahead signals. The composite score drives engine selection
in the meta-switcher.

Four signals (all computable from EOD data with no lookahead):
  NMS – NEPSE Momentum Score:      60-day return of the NEPSE proxy (median stock)
  RB  – Rolling Breadth:           % of liquid stocks above their 50-day MA
  VR  – Volatility Regime:         20-day annualised std of NEPSE daily returns
  MP  – Momentum Persistence:      Fraction of top-quintile 20d-momentum stocks
                                    that were also in top-quintile 5 days ago
                                    (measures whether momentum is "sticky")

Composite score = NMS_norm + RB_norm + (1 – VR_norm) + MP_norm  ∈ [0, 4]
  TRENDING  if score >= 2.5  (bull trend engine: C31+trail15%+hold40)
  CHOPPY    if score <= 1.5  (choppy engine: R83 = shorter hold, more positions)
  NEUTRAL   if 1.5 < score < 2.5  (hold current engine — no switch)

Thresholds are economically motivated, NOT tuned to backtest data:
  NMS:  > +8% → trending, < +3% → choppy  (NEPSE 60d return)
  RB:   > 60% → broad rally,  < 45% → narrow/diverging
  VR:   < 18% → calm,  > 25% → elevated (annualised)
  MP:   > 0.65 → sticky,  < 0.50 → rotating

Usage
-----
  from app.services.data.historical_provider import get_historical_provider
  from app.components.quant.market_state import compute_market_state
  from datetime import datetime

  provider = get_historical_provider()
  prices_df = provider.load_universe()          # wide df: date x symbol
  state = compute_market_state(prices_df, datetime.today())
  print(state)  # MarketState(regime='NEUTRAL', score=2.1, ...)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds (economically motivated, NOT backtest-tuned)
# ---------------------------------------------------------------------------

NMS_TREND_THRESHOLD   = 0.08   # 60d return > 8% → trending
NMS_CHOPPY_THRESHOLD  = 0.03   # 60d return < 3% → choppy

RB_TREND_THRESHOLD    = 0.60   # > 60% stocks above 50d MA → broad rally
RB_CHOPPY_THRESHOLD   = 0.45   # < 45% stocks above 50d MA → narrow/diverging

VR_CALM_THRESHOLD     = 0.18   # annualised vol < 18% → calm (trending-compatible)
VR_ELEVATED_THRESHOLD = 0.25   # annualised vol > 25% → elevated (choppy)

MP_STICKY_THRESHOLD   = 0.65   # > 65% top-quintile stocks still in top-quintile
MP_ROTATING_THRESHOLD = 0.50   # < 50% → momentum is rotating

# Composite score thresholds
TRENDING_SCORE = 2.5
CHOPPY_SCORE   = 1.5

# Hysteresis gate: must be confirmed for N consecutive observations
HYSTERESIS_CONFIRM = 3

NEPSE_TRADING_DAYS = 240  # annual trading days in Nepal


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SignalReading:
    """Raw reading for one of the four market state signals."""
    name: str
    value: float         # raw value (e.g. 0.063 for NMS)
    norm: float          # normalised 0-1 contribution to composite score
    trend_signal: bool   # True if this signal says TRENDING
    choppy_signal: bool  # True if this signal says CHOPPY
    label: str           # human readable: 'trend', 'neutral', 'choppy'


@dataclass
class MarketState:
    """Full market state reading for a given date."""
    date: datetime
    regime: str              # 'TRENDING', 'CHOPPY', 'NEUTRAL'
    score: float             # composite 0-4
    signals: List[SignalReading] = field(default_factory=list)
    nms: float = 0.0         # NEPSE 60d return
    rb: float = 0.0          # % stocks above 50d MA
    vr: float = 0.0          # annualised 20d vol
    mp: float = 0.0          # momentum persistence fraction
    engine: str = ""         # 'TREND' | 'CHOPPY' | '' (hold current)
    note: str = ""           # why this state was assigned

    def summary(self) -> str:
        return (
            f"[{self.date.date()}] {self.regime} (score={self.score:.2f})  "
            f"NMS={self.nms:+.1%}  RB={self.rb:.0%}  "
            f"VR={self.vr:.0%}  MP={self.mp:.0%}  "
            f"→ engine={self.engine or 'hold'}"
        )


# ---------------------------------------------------------------------------
# Signal 1: NEPSE Momentum Score (NMS)
# ---------------------------------------------------------------------------

def _compute_nms(
    prices_df: pd.DataFrame,
    date: datetime,
    lookback: int = 60,
) -> Tuple[float, SignalReading]:
    """60-day median stock return as NEPSE proxy."""
    df = prices_df[prices_df["date"] <= date]
    dates = sorted(df["date"].unique())

    if len(dates) < lookback:
        return 0.0, SignalReading("NMS", 0.0, 0.5, False, False, "neutral")

    start_date = dates[-lookback]
    end_date   = dates[-1]

    first = df[df["date"] == start_date].set_index("symbol")["close"]
    last  = df[df["date"] == end_date].set_index("symbol")["close"]

    common = first.index.intersection(last.index)
    common = [s for s in common if not s.startswith("SECTOR::") and s != "NEPSE"]

    if len(common) < 20:
        return 0.0, SignalReading("NMS", 0.0, 0.5, False, False, "neutral")

    returns = (last[common] / first[common] - 1)
    nms = float(returns.median())

    # Normalise: 0 = strongly choppy (< 3%), 1 = strongly trending (> 8%)
    norm = np.clip((nms - NMS_CHOPPY_THRESHOLD) / (NMS_TREND_THRESHOLD - NMS_CHOPPY_THRESHOLD), 0, 1)

    trend  = nms > NMS_TREND_THRESHOLD
    choppy = nms < NMS_CHOPPY_THRESHOLD
    label  = "trend" if trend else ("choppy" if choppy else "neutral")

    return nms, SignalReading("NMS", nms, float(norm), trend, choppy, label)


# ---------------------------------------------------------------------------
# Signal 2: Rolling Breadth (RB)
# ---------------------------------------------------------------------------

def _compute_rb(
    prices_df: pd.DataFrame,
    date: datetime,
    ma_window: int = 50,
    top_n: int = 200,
) -> Tuple[float, SignalReading]:
    """% of top-N liquid stocks currently above their 50-day MA."""
    df = prices_df[prices_df["date"] <= date]
    dates = sorted(df["date"].unique())

    if len(dates) < ma_window + 5:
        return 0.5, SignalReading("RB", 0.5, 0.5, False, False, "neutral")

    current_date = dates[-1]
    current_prices = df[df["date"] == current_date].copy()
    current_prices = current_prices[
        ~current_prices["symbol"].str.startswith("SECTOR::") &
        (current_prices["symbol"] != "NEPSE")
    ]

    # Compute turnover proxy (close × volume) to find top-N liquid symbols
    if "volume" in current_prices.columns:
        current_prices = current_prices.copy()
        current_prices["turnover"] = current_prices["close"] * current_prices["volume"].fillna(0)
        top_symbols = (
            current_prices.nlargest(top_n, "turnover")["symbol"].tolist()
        )
    else:
        top_symbols = current_prices["symbol"].tolist()[:top_n]

    if not top_symbols:
        return 0.5, SignalReading("RB", 0.5, 0.5, False, False, "neutral")

    # For each symbol, check if close > 50d MA
    above_ma = 0
    checked  = 0

    for symbol in top_symbols:
        sym_df = df[df["symbol"] == symbol].sort_values("date")
        if len(sym_df) < ma_window:
            continue

        recent = sym_df.tail(ma_window)
        ma50   = recent["close"].mean()
        today_close = recent["close"].iloc[-1]

        if today_close > ma50:
            above_ma += 1
        checked += 1

    if checked < 20:
        return 0.5, SignalReading("RB", 0.5, 0.5, False, False, "neutral")

    rb = above_ma / checked

    # Normalise
    norm = np.clip((rb - RB_CHOPPY_THRESHOLD) / (RB_TREND_THRESHOLD - RB_CHOPPY_THRESHOLD), 0, 1)

    trend  = rb > RB_TREND_THRESHOLD
    choppy = rb < RB_CHOPPY_THRESHOLD
    label  = "trend" if trend else ("choppy" if choppy else "neutral")

    return rb, SignalReading("RB", rb, float(norm), trend, choppy, label)


# ---------------------------------------------------------------------------
# Signal 3: Volatility Regime (VR)
# ---------------------------------------------------------------------------

def _compute_vr(
    prices_df: pd.DataFrame,
    date: datetime,
    lookback: int = 20,
) -> Tuple[float, SignalReading]:
    """20-day annualised standard deviation of cross-sectional median return."""
    df = prices_df[prices_df["date"] <= date]
    dates = sorted(df["date"].unique())

    if len(dates) < lookback + 2:
        return 0.20, SignalReading("VR", 0.20, 0.5, False, False, "neutral")

    recent_dates = dates[-(lookback + 1):]

    daily_rets = []
    for i in range(1, len(recent_dates)):
        d_prev = recent_dates[i - 1]
        d_cur  = recent_dates[i]

        p_prev = df[df["date"] == d_prev].set_index("symbol")["close"]
        p_cur  = df[df["date"] == d_cur].set_index("symbol")["close"]

        common = p_prev.index.intersection(p_cur.index)
        common = [s for s in common if not s.startswith("SECTOR::") and s != "NEPSE"]

        if len(common) < 10:
            continue

        rets = (p_cur[common] / p_prev[common] - 1)
        daily_rets.append(float(rets.median()))

    if len(daily_rets) < 10:
        return 0.20, SignalReading("VR", 0.20, 0.5, False, False, "neutral")

    vol = float(np.std(daily_rets, ddof=1) * np.sqrt(NEPSE_TRADING_DAYS))

    # Normalise: 0 = calm (< 18%), 1 = elevated (> 25%)
    # NOTE: for composite score, high VR = choppy, so we use (1 - VR_norm)
    norm = np.clip((vol - VR_CALM_THRESHOLD) / (VR_ELEVATED_THRESHOLD - VR_CALM_THRESHOLD), 0, 1)

    trend  = vol < VR_CALM_THRESHOLD
    choppy = vol > VR_ELEVATED_THRESHOLD
    label  = "trend" if trend else ("choppy" if choppy else "neutral")

    return vol, SignalReading("VR", vol, float(norm), trend, choppy, label)


# ---------------------------------------------------------------------------
# Signal 4: Momentum Persistence (MP)
# ---------------------------------------------------------------------------

def _compute_mp(
    prices_df: pd.DataFrame,
    date: datetime,
    momentum_window: int = 20,
    lag: int = 5,
    top_quintile_pct: float = 0.20,
) -> Tuple[float, SignalReading]:
    """
    Momentum persistence: fraction of top-quintile stocks by 20d momentum
    that were ALSO in the top quintile 5 trading days ago.

    High MP → momentum is sticky → hold-40 is safe → TRENDING.
    Low MP  → momentum is rotating → shorter holds preferred → CHOPPY.
    """
    df = prices_df[prices_df["date"] <= date]
    dates = sorted(df["date"].unique())

    if len(dates) < momentum_window + lag + 2:
        return 0.575, SignalReading("MP", 0.575, 0.5, False, False, "neutral")

    # Momentum at today's date: return over last 20 trading days
    d_end_now   = dates[-1]
    d_start_now = dates[-(momentum_window + 1)]

    # Momentum 5 days ago
    d_end_lag   = dates[-(lag + 1)]
    d_start_lag = dates[-(momentum_window + lag + 1)]

    def get_returns(start, end):
        p_start = df[df["date"] == start].set_index("symbol")["close"]
        p_end   = df[df["date"] == end].set_index("symbol")["close"]
        common  = p_start.index.intersection(p_end.index)
        common  = [s for s in common if not s.startswith("SECTOR::") and s != "NEPSE"]
        if len(common) < 20:
            return None
        rets = p_end[common] / p_start[common] - 1
        return rets.sort_values(ascending=False)

    rets_now = get_returns(d_start_now, d_end_now)
    rets_lag = get_returns(d_start_lag, d_end_lag)

    if rets_now is None or rets_lag is None:
        return 0.575, SignalReading("MP", 0.575, 0.5, False, False, "neutral")

    n_quintile_now = max(1, int(len(rets_now) * top_quintile_pct))
    n_quintile_lag = max(1, int(len(rets_lag) * top_quintile_pct))

    top_now = set(rets_now.head(n_quintile_now).index)
    top_lag = set(rets_lag.head(n_quintile_lag).index)

    if not top_lag:
        return 0.575, SignalReading("MP", 0.575, 0.5, False, False, "neutral")

    # What fraction of yesterday's top quintile is still in today's top quintile?
    persistence = len(top_lag.intersection(top_now)) / len(top_lag)
    mp = float(persistence)

    # Normalise
    norm = np.clip((mp - MP_ROTATING_THRESHOLD) / (MP_STICKY_THRESHOLD - MP_ROTATING_THRESHOLD), 0, 1)

    trend  = mp > MP_STICKY_THRESHOLD
    choppy = mp < MP_ROTATING_THRESHOLD
    label  = "trend" if trend else ("choppy" if choppy else "neutral")

    return mp, SignalReading("MP", mp, float(norm), trend, choppy, label)


# ---------------------------------------------------------------------------
# Composite state computation
# ---------------------------------------------------------------------------

def compute_market_state(
    prices_df: pd.DataFrame,
    date: datetime,
) -> MarketState:
    """
    Compute composite market state from four signals.

    Parameters
    ----------
    prices_df : pd.DataFrame
        Full price table (symbol, date, close, volume).
    date : datetime
        Evaluation date. All signals use data available on or before this date.

    Returns
    -------
    MarketState
        Full reading including raw values, normalised contributions, and regime.
    """
    nms_val, nms_sig = _compute_nms(prices_df, date)
    rb_val,  rb_sig  = _compute_rb(prices_df, date)
    vr_val,  vr_sig  = _compute_vr(prices_df, date)
    mp_val,  mp_sig  = _compute_mp(prices_df, date)

    # Composite: NMS + RB + (1 - VR) + MP
    # High VR = bad for trending → flip VR contribution
    score = nms_sig.norm + rb_sig.norm + (1 - vr_sig.norm) + mp_sig.norm

    if score >= TRENDING_SCORE:
        regime = "TRENDING"
        engine = "TREND"
        note   = f"Score {score:.2f} ≥ {TRENDING_SCORE}: majority signals favour trend engine"
    elif score <= CHOPPY_SCORE:
        regime = "CHOPPY"
        engine = "CHOPPY"
        note   = f"Score {score:.2f} ≤ {CHOPPY_SCORE}: majority signals favour choppy engine"
    else:
        regime = "NEUTRAL"
        engine = ""
        note   = f"Score {score:.2f} is ambiguous (1.5 < {score:.2f} < 2.5): hold current engine"

    trend_count  = sum(s.trend_signal  for s in [nms_sig, rb_sig, vr_sig, mp_sig])
    choppy_count = sum(s.choppy_signal for s in [nms_sig, rb_sig, vr_sig, mp_sig])
    note += f" | T={trend_count}/4 C={choppy_count}/4"

    return MarketState(
        date=date,
        regime=regime,
        score=float(score),
        signals=[nms_sig, rb_sig, vr_sig, mp_sig],
        nms=nms_val,
        rb=rb_val,
        vr=vr_val,
        mp=mp_val,
        engine=engine,
        note=note,
    )


# ---------------------------------------------------------------------------
# Historical scan (for backtesting + validation)
# ---------------------------------------------------------------------------

def scan_market_states(
    prices_df: pd.DataFrame,
    start_date: datetime,
    end_date: datetime,
    frequency: int = 5,  # every N trading days
) -> List[MarketState]:
    """
    Scan market state across a date range.

    Parameters
    ----------
    prices_df : pd.DataFrame
    start_date, end_date : datetime
    frequency : int
        Compute state every N trading days (default 5 = weekly).

    Returns
    -------
    list[MarketState]
    """
    all_dates = sorted(prices_df["date"].unique())
    dates_in_range = [d for d in all_dates if start_date <= d <= end_date]

    scan_dates = dates_in_range[::frequency]

    states = []
    for d in scan_dates:
        try:
            state = compute_market_state(prices_df, d)
            states.append(state)
            logger.debug(state.summary())
        except Exception as e:
            logger.warning(f"Error computing state for {d}: {e}")

    return states


# ---------------------------------------------------------------------------
# Hysteresis gate (for production use)
# ---------------------------------------------------------------------------

def apply_hysteresis(
    states: List[MarketState],
    confirm_required: int = HYSTERESIS_CONFIRM,
) -> List[MarketState]:
    """
    Apply hysteresis gate: only confirm a state change after N consecutive
    observations agree. Sets `engine` to '' (hold) until confirmed.

    Modifies states in-place and returns them.
    """
    if not states:
        return states

    confirmed_regime = states[0].regime
    consecutive      = 1

    for i, state in enumerate(states[1:], 1):
        if state.regime == confirmed_regime or state.regime == "NEUTRAL":
            consecutive += 1
        else:
            # State is changing
            if consecutive >= confirm_required:
                # Switch was already confirmed — now checking persistence
                confirmed_regime = state.regime
                consecutive      = 1
            else:
                # Not yet confirmed — hold current engine
                consecutive = 1
                states[i].engine = ""
                states[i].note   += f" | HYSTERESIS: {consecutive}/{confirm_required} to switch"

    return states


__all__ = [
    "MarketState", "SignalReading",
    "compute_market_state", "scan_market_states", "apply_hysteresis",
]
