"""
EMA Crossover Bot (NEPSE-optimised)
=====================================
Strategy: Fast EMA crosses above mid EMA while price is above the slow EMA,
confirmed by a volume surge. Parameters scale with timeframe:

  Daily   — EMA(9/21/50),    vol≥1.5×, stop=2.5%, target=6%,  hold≤10d
  Weekly  — EMA(21/50/100),  vol≥1.3×, stop=3.5%, target=9%,  hold≤25d
  Monthly — EMA(50/100/200), vol≥1.2×, stop=5.0%, target=15%, hold≤60d

Entry conditions (all must pass for any timeframe):
  1. Fast EMA crossed above mid EMA within the last 2 bars.
  2. Price is above slow EMA (broader trend confirmation).
  3. Volume > threshold × 20-day average.
  4. Fast EMA slope is positive.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.components.bots.base_bot import BaseBot
from app.components.bots.nepse_universe import get_nepse_universe, get_sector, get_sector_map

logger = logging.getLogger("bot.ema_crossover")

# ── Timeframe parameter sets ───────────────────────────────────────────────────
_TF_PARAMS: Dict[str, Dict[str, Any]] = {
    "daily": {
        "ema_fast":   9,
        "ema_mid":    21,
        "ema_slow":   50,
        "vol_min":    1.5,    # minimum volume ratio
        "min_rows":   60,
        "stop_pct":   2.5,
        "target_pct": 6.0,
    },
    "weekly": {
        "ema_fast":   21,
        "ema_mid":    50,
        "ema_slow":   100,
        "vol_min":    1.3,
        "min_rows":   110,
        "stop_pct":   3.5,
        "target_pct": 9.0,
    },
    "monthly": {
        "ema_fast":   50,
        "ema_mid":    100,
        "ema_slow":   200,
        "vol_min":    1.2,
        "min_rows":   210,
        "stop_pct":   5.0,
        "target_pct": 15.0,
    },
}


def _ema_series(closes: pd.Series, span: int) -> pd.Series:
    return closes.ewm(span=span, adjust=False).mean()


def _rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.diff()
    gain  = delta.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _ema_cross_score(
    ema_fast_now: float,
    ema_fast_prev: float,
    ema_mid_now: float,
    ema_mid_prev: float,
    ema_slow_now: float,
    close_now: float,
    volume_ratio: float,
    vol_min: float,
) -> Optional[float]:
    """Return 0-100 signal score, or None if entry conditions not met."""
    # Gate 1: Price must be above slow EMA
    if close_now < ema_slow_now:
        return None

    # Gate 2: Fast EMA currently above mid EMA
    if ema_fast_now <= ema_mid_now:
        return None

    # Gate 3: Fresh crossover (fast was at or below mid last bar, OR already above)
    cross_this_bar = ema_fast_prev <= ema_mid_prev
    cross_last_bar = (ema_fast_now > ema_mid_now) and (ema_fast_prev > ema_mid_prev)
    if not (cross_this_bar or cross_last_bar):
        return None

    # Gate 4: Volume confirmation
    if volume_ratio < vol_min:
        return None

    # Gate 5: Fast EMA slope positive
    if ema_fast_now <= ema_fast_prev:
        return None

    # ── Score ──────────────────────────────────────────────────────────
    score = 50.0

    # Volume bonus (up to 20 pts): vol_min+0 → 0 pts, vol_min+1.5 → 20 pts
    vol_bonus = min(20.0, (volume_ratio - vol_min) / 1.5 * 20.0)
    score += vol_bonus

    # Fast−mid EMA separation bonus (up to 15 pts)
    separation_pct = (ema_fast_now - ema_mid_now) / ema_mid_now * 100
    score += min(15.0, separation_pct * 5.0)

    # Fresh crossover bonus
    score += 10.0 if cross_this_bar else 5.0

    # Distance above slow EMA bonus (up to 5 pts)
    dist_slow = (close_now - ema_slow_now) / ema_slow_now * 100
    score += min(5.0, dist_slow * 1.0)

    return min(100.0, score)


class EMACrossoverBot(BaseBot):
    BOT_ID   = "ema_crossover_bot"
    BOT_NAME = "EMA Crossover Bot"
    STRATEGY = "ema_crossover"

    # Daily defaults (overridden per-timeframe in _open_trade via signal keys)
    DEFAULT_STOP_PCT   = 2.5
    DEFAULT_TARGET_PCT = 6.0
    MAX_HOLD_DAYS      = 10

    def generate_signals(self, db: Session, timeframe: str = "daily") -> List[Dict[str, Any]]:
        p = _TF_PARAMS.get(timeframe, _TF_PARAMS["daily"])

        signals: List[Dict[str, Any]] = []

        try:
            from app.services.data.historical_provider import get_historical_provider
        except ImportError as e:
            logger.error("Imports failed: %s", e)
            return []

        provider = get_historical_provider()
        if not provider.is_available():
            logger.warning("EMA Crossover Bot: HistoricalDataProvider not available")
            return []

        universe = get_nepse_universe(provider)
        sector_map = get_sector_map()
        logger.info(
            "EMA Crossover Bot[%s] scanning %d symbols | EMA(%d/%d/%d) vol≥%.1f×",
            timeframe, len(universe), p["ema_fast"], p["ema_mid"], p["ema_slow"], p["vol_min"],
        )

        panel = provider.load_universe(universe, min_rows=p["min_rows"])

        for symbol, df in panel.items():
            try:
                if len(df) < p["min_rows"] - 5:
                    continue

                closes  = df["close"]
                volumes = df["volume"] if "volume" in df.columns else pd.Series([1.0] * len(df))

                ema_fast = _ema_series(closes, p["ema_fast"])
                ema_mid  = _ema_series(closes, p["ema_mid"])
                ema_slow = _ema_series(closes, p["ema_slow"])
                vol_avg  = volumes.rolling(20).mean()

                ema_fast_now  = float(ema_fast.iloc[-1])
                ema_fast_prev = float(ema_fast.iloc[-2])
                ema_mid_now   = float(ema_mid.iloc[-1])
                ema_mid_prev  = float(ema_mid.iloc[-2])
                ema_slow_now  = float(ema_slow.iloc[-1])
                close_now     = float(closes.iloc[-1])

                vol_now     = float(volumes.iloc[-1])
                vol_avg_now = float(vol_avg.iloc[-1])
                if math.isnan(vol_avg_now) or vol_avg_now == 0:
                    volume_ratio = 1.0
                else:
                    volume_ratio = vol_now / vol_avg_now

                score = _ema_cross_score(
                    ema_fast_now, ema_fast_prev,
                    ema_mid_now,  ema_mid_prev,
                    ema_slow_now, close_now,
                    volume_ratio, p["vol_min"],
                )
                if score is None:
                    continue

                ema_gap = (ema_fast_now - ema_slow_now) / ema_slow_now * 100
                regime  = "trending" if ema_gap > 2.0 else ("sideways" if ema_gap > -1.0 else "volatile")

                signals.append({
                    "symbol":      symbol,
                    "score":       round(score, 1),
                    "signal":      "BUY",
                    "entry_price": close_now,
                    "regime":      regime,
                    "sector":      get_sector(symbol, sector_map),
                    "stop_pct":    p["stop_pct"],
                    "target_pct":  p["target_pct"],
                    "context": {
                        "timeframe":    timeframe,
                        "ema_fast":     round(ema_fast_now, 2),
                        "ema_mid":      round(ema_mid_now, 2),
                        "ema_slow":     round(ema_slow_now, 2),
                        "ema_fast_prev": round(ema_fast_prev, 2),
                        "ema_mid_prev": round(ema_mid_prev, 2),
                        "volume_ratio": round(volume_ratio, 2),
                        "close":        close_now,
                        "sector":       get_sector(symbol, sector_map),
                    },
                })

            except Exception as exc:
                logger.debug("EMA scan error for %s: %s", symbol, exc)

        signals.sort(key=lambda s: s["score"], reverse=True)
        return signals[:10]
