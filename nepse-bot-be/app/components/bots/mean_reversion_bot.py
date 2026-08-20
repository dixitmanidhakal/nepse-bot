"""
Mean Reversion Bot (NEPSE-optimised)
======================================
Strategy: Identifies stocks that have been oversold relative to their recent
range (near lower Bollinger Band) with a volume spike signalling capitulation.
Parameters scale with timeframe:

  Daily   — RSI<38, BB(20, 2σ), vol>1.4×, stop=3.5%, target=8%,  hold≤10d
  Weekly  — RSI<35, BB(30, 2σ), vol>1.3×, stop=5.0%, target=12%, hold≤25d
  Monthly — RSI<30, BB(50, 2σ), vol>1.2×, stop=7.0%, target=20%, hold≤60d

Entry conditions (all must pass):
  1. RSI below threshold (oversold zone).
  2. RSI rising vs previous bar (momentum turning).
  3. Price within 3% of lower Bollinger Band.
  4. Volume > threshold × 20-day average (capitulation spike).
  5. Price ≥ 8% above 52-week low (not a fallen knife).
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

logger = logging.getLogger("bot.mean_reversion")

_MIN_ABOVE_52W_LOW_PCT = 8.0   # avoid fallen knives

# ── Timeframe parameter sets ───────────────────────────────────────────────────
_TF_PARAMS: Dict[str, Dict[str, Any]] = {
    "daily": {
        "rsi_oversold": 38.0,
        "bb_period":    20,
        "bb_std":       2.0,
        "vol_min":      1.4,
        "min_rows":     60,
        "stop_pct":     3.5,
        "target_pct":   8.0,
    },
    "weekly": {
        "rsi_oversold": 35.0,
        "bb_period":    30,
        "bb_std":       2.0,
        "vol_min":      1.3,
        "min_rows":     80,
        "stop_pct":     5.0,
        "target_pct":   12.0,
    },
    "monthly": {
        "rsi_oversold": 30.0,
        "bb_period":    50,
        "bb_std":       2.0,
        "vol_min":      1.2,
        "min_rows":     120,
        "stop_pct":     7.0,
        "target_pct":   20.0,
    },
}


def _rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.diff()
    gain  = delta.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _bollinger(closes: pd.Series, period: int = 20, std: float = 2.0):
    mid  = closes.rolling(period).mean()
    band = closes.rolling(period).std(ddof=0)
    return mid, mid + std * band, mid - std * band


def _mr_score(
    rsi_now: float,
    rsi_prev: float,
    close_now: float,
    bb_lower: float,
    volume_ratio: float,
    low_52w: float,
    rsi_oversold: float,
    vol_min: float,
) -> Optional[float]:
    """Compute 0-100 mean-reversion score. Returns None if conditions not met."""
    if rsi_now >= rsi_oversold:
        return None
    if rsi_now <= rsi_prev:
        return None
    if bb_lower <= 0 or close_now > bb_lower * 1.03:
        return None
    if volume_ratio < vol_min:
        return None
    if low_52w > 0 and close_now < low_52w * (1 + _MIN_ABOVE_52W_LOW_PCT / 100):
        return None

    score = 50.0

    # RSI depth bonus (up to 15 pts): deeper oversold = more potential
    rsi_bonus = min(15.0, ((rsi_oversold - rsi_now) / rsi_oversold) * 15.0 * 2.5)
    score += rsi_bonus

    # RSI rising bonus (up to 10 pts)
    score += min(10.0, (rsi_now - rsi_prev) * 3.0)

    # Volume spike bonus (up to 15 pts)
    score += min(15.0, (volume_ratio - vol_min) / 1.6 * 15.0)

    # BB proximity bonus (up to 10 pts)
    bb_dist_pct = (close_now - bb_lower) / bb_lower * 100
    score += max(0.0, 10.0 - bb_dist_pct * 3.33)

    return min(100.0, score)


class MeanReversionBot(BaseBot):
    BOT_ID   = "mean_reversion_bot"
    BOT_NAME = "Mean Reversion Bot"
    STRATEGY = "mean_reversion"

    DEFAULT_STOP_PCT   = 3.5
    DEFAULT_TARGET_PCT = 8.0
    MAX_HOLD_DAYS      = 7

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
            logger.warning("Mean Reversion Bot: HistoricalDataProvider not available")
            return []

        universe = get_nepse_universe(provider)
        sector_map = get_sector_map()
        logger.info(
            "Mean Reversion Bot[%s] scanning %d symbols | RSI<%s BB(%d) vol≥%.1f×",
            timeframe, len(universe), p["rsi_oversold"], p["bb_period"], p["vol_min"],
        )

        panel = provider.load_universe(universe, min_rows=p["min_rows"])
        logger.info(
            "Mean Reversion Bot[%s]: loaded %d symbols from historical provider",
            timeframe, len(panel),
        )

        # Diagnostic counters — help distinguish "no oversold stocks" vs data issues
        n_skipped_rows   = 0   # not enough OHLCV rows
        n_rsi_too_high   = 0   # RSI >= oversold threshold
        n_rsi_not_rising = 0   # RSI falling (no momentum turn)
        n_bb_far         = 0   # price too far above lower BB
        n_vol_low        = 0   # volume not spiking
        n_fallen_knife   = 0   # price < 52w low * 1.08 (fallen knife)

        for symbol, df in panel.items():
            try:
                if len(df) < p["min_rows"] - 5:
                    n_skipped_rows += 1
                    continue

                closes  = df["close"]
                lows    = df["low"] if "low" in df.columns else closes
                volumes = df["volume"] if "volume" in df.columns else pd.Series([1.0] * len(df))

                rsi_series = _rsi(closes)
                _, _, bb_lo = _bollinger(closes, p["bb_period"], p["bb_std"])
                vol_avg = volumes.rolling(20).mean()

                rsi_now   = float(rsi_series.iloc[-1])
                rsi_prev  = float(rsi_series.iloc[-2])
                close_now = float(closes.iloc[-1])
                bb_lo_now = float(bb_lo.iloc[-1])

                lookback = min(252, len(lows))
                low_52w  = float(lows.iloc[-lookback:].min())

                vol_now  = float(volumes.iloc[-1])
                avg_vol  = float(vol_avg.iloc[-1])
                if math.isnan(avg_vol) or avg_vol == 0:
                    volume_ratio = 1.0
                else:
                    volume_ratio = vol_now / avg_vol

                # Diagnose which filter is blocking each stock
                if rsi_now >= p["rsi_oversold"]:
                    n_rsi_too_high += 1
                    continue
                if rsi_now >= rsi_prev:
                    n_rsi_not_rising += 1
                    continue
                if bb_lo_now <= 0 or close_now > bb_lo_now * 1.03:
                    n_bb_far += 1
                    continue
                if volume_ratio < p["vol_min"]:
                    n_vol_low += 1
                    continue
                if low_52w > 0 and close_now < low_52w * (1 + _MIN_ABOVE_52W_LOW_PCT / 100):
                    n_fallen_knife += 1
                    continue

                score = _mr_score(
                    rsi_now, rsi_prev,
                    close_now, bb_lo_now,
                    volume_ratio, low_52w,
                    p["rsi_oversold"], p["vol_min"],
                )
                if score is None:
                    continue

                regime = "volatile" if rsi_now < 25 else "sideways"

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
                        "rsi_14":       round(rsi_now, 1),
                        "rsi_prev":     round(rsi_prev, 1),
                        "bb_period":    p["bb_period"],
                        "bb_lower":     round(bb_lo_now, 2),
                        "volume_ratio": round(volume_ratio, 2),
                        "low_52w":      round(low_52w, 2),
                        "close":        close_now,
                        "sector":       get_sector(symbol, sector_map),
                    },
                })

            except Exception as exc:
                logger.debug("Mean Reversion scan error for %s: %s", symbol, exc)

        signals.sort(key=lambda s: s["score"], reverse=True)

        if signals:
            logger.info(
                "Mean Reversion Bot[%s]: found %d signals | top=%s score=%.0f",
                timeframe, len(signals), signals[0]["symbol"], signals[0]["score"],
            )
        else:
            logger.info(
                "Mean Reversion Bot[%s]: NO signals found — market not oversold. "
                "Filters: rsi_too_high=%d rsi_not_rising=%d bb_far=%d vol_low=%d "
                "fallen_knife=%d skipped_rows=%d (RSI threshold=%.0f)",
                timeframe,
                n_rsi_too_high, n_rsi_not_rising, n_bb_far, n_vol_low,
                n_fallen_knife, n_skipped_rows, p["rsi_oversold"],
            )

        return signals[:10]
