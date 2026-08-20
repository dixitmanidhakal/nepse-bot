"""
Momentum Bot
============
Pure price-action momentum strategy: RSI + MACD + Bollinger Band breakout.
Parameters scale with timeframe:

  Daily   — MACD(12/26/9),  BB(20), RSI 50-72, vol≥1.2×, stop=3.0%, target=7%
  Weekly  — MACD(26/52/18), BB(40), RSI 52-70, vol≥1.1×, stop=4.5%, target=11%
  Monthly — MACD(52/104/36),BB(80), RSI 55-68, vol≥1.0×, stop=7.0%, target=18%

Entry conditions (all must be met for any timeframe):
  - RSI in range [rsi_min, rsi_max] (momentum, not overbought)
  - MACD histogram turned positive (signal cross)
  - Price closes above BB middle band
  - Volume > vol_min × 20-day average
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

logger = logging.getLogger("bot.momentum")

# ── Timeframe parameter sets ───────────────────────────────────────────────────
_TF_PARAMS: Dict[str, Dict[str, Any]] = {
    "daily": {
        "macd_fast":  12,
        "macd_slow":  26,
        "macd_sig":   9,
        "bb_period":  20,
        "rsi_min":    50,
        "rsi_max":    72,
        "vol_min":    1.2,
        "min_rows":   40,
        "stop_pct":   3.0,
        "target_pct": 7.0,
    },
    "weekly": {
        "macd_fast":  26,
        "macd_slow":  52,
        "macd_sig":   18,
        "bb_period":  40,
        "rsi_min":    52,
        "rsi_max":    70,
        "vol_min":    1.1,
        "min_rows":   60,
        "stop_pct":   4.5,
        "target_pct": 11.0,
    },
    "monthly": {
        "macd_fast":  52,
        "macd_slow":  104,
        "macd_sig":   36,
        "bb_period":  80,
        "rsi_min":    55,
        "rsi_max":    68,
        "vol_min":    1.0,
        "min_rows":   120,
        "stop_pct":   7.0,
        "target_pct": 18.0,
    },
}


def _rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.diff()
    gain  = delta.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd_hist(closes: pd.Series, fast: int, slow: int, sig: int) -> pd.Series:
    ema_f  = closes.ewm(span=fast, adjust=False).mean()
    ema_s  = closes.ewm(span=slow, adjust=False).mean()
    macd   = ema_f - ema_s
    signal = macd.ewm(span=sig, adjust=False).mean()
    return macd - signal


def _bollinger(closes: pd.Series, period: int = 20, std: float = 2.0):
    mid  = closes.rolling(period).mean()
    band = closes.rolling(period).std()
    return mid, mid + std * band, mid - std * band


def _momentum_score(
    rsi_now: float,
    rsi_prev: float,
    hist_now: float,
    hist_prev: float,
    close: float,
    bb_mid: float,
    volume_ratio: float,
    rsi_min: float,
    rsi_max: float,
    vol_min: float,
) -> Optional[float]:
    """Compute 0-100 score. Returns None if entry conditions not met."""
    if not (rsi_min <= rsi_now <= rsi_max):
        return None
    if hist_now <= 0:
        return None
    if close < bb_mid:
        return None
    if volume_ratio < vol_min:
        return None

    score = 50.0

    rsi_score = 20.0 * min(1.0, max(0.0, (rsi_now - rsi_min) / 15.0))
    score += rsi_score

    if rsi_prev < rsi_min:
        score += 10.0

    if hist_prev <= 0 < hist_now:
        score += 15.0
    elif hist_now > 0:
        score += 8.0

    score += min(15.0, (volume_ratio - vol_min) * 10.0)
    score += min(5.0, (close - bb_mid) / bb_mid * 100)

    return min(100.0, score)


class MomentumBot(BaseBot):
    BOT_ID   = "momentum_bot"
    BOT_NAME = "Momentum Bot"
    STRATEGY = "momentum"

    DEFAULT_STOP_PCT   = 3.0
    DEFAULT_TARGET_PCT = 7.0
    MAX_HOLD_DAYS      = 8

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
            logger.warning("Momentum Bot: HistoricalDataProvider not available")
            return []

        universe = get_nepse_universe(provider)
        sector_map = get_sector_map()
        logger.info(
            "Momentum Bot[%s] scanning %d symbols | MACD(%d/%d/%d) BB(%d) RSI %d-%d vol≥%.1f×",
            timeframe, len(universe),
            p["macd_fast"], p["macd_slow"], p["macd_sig"],
            p["bb_period"], p["rsi_min"], p["rsi_max"], p["vol_min"],
        )

        panel = provider.load_universe(universe, min_rows=p["min_rows"])

        for symbol, df in panel.items():
            try:
                if len(df) < p["min_rows"] - 5:
                    continue

                closes  = df["close"]
                volumes = df["volume"] if "volume" in df.columns else pd.Series([1.0] * len(df))

                rsi_series  = _rsi(closes)
                hist_series = _macd_hist(closes, p["macd_fast"], p["macd_slow"], p["macd_sig"])
                bb_mid, _, _ = _bollinger(closes, p["bb_period"])
                vol_avg = volumes.rolling(20).mean()

                rsi_now    = float(rsi_series.iloc[-1])
                rsi_prev   = float(rsi_series.iloc[-2])
                hist_now   = float(hist_series.iloc[-1])
                hist_prev  = float(hist_series.iloc[-2])
                close_now  = float(closes.iloc[-1])
                bb_mid_now = float(bb_mid.iloc[-1])
                vol_now    = float(volumes.iloc[-1])
                vol_avg_now = float(vol_avg.iloc[-1]) if not math.isnan(float(vol_avg.iloc[-1])) else 1.0
                volume_ratio = vol_now / vol_avg_now if vol_avg_now > 0 else 1.0

                score = _momentum_score(
                    rsi_now, rsi_prev, hist_now, hist_prev,
                    close_now, bb_mid_now, volume_ratio,
                    p["rsi_min"], p["rsi_max"], p["vol_min"],
                )
                if score is None:
                    continue

                regime = "trending" if rsi_now > 58 and hist_now > hist_prev else "sideways"

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
                        "timeframe":        timeframe,
                        "rsi_14":           round(rsi_now, 1),
                        "rsi_prev":         round(rsi_prev, 1),
                        "macd_hist":        round(hist_now, 4),
                        "macd_hist_prev":   round(hist_prev, 4),
                        "volume_ratio":     round(volume_ratio, 2),
                        "bb_mid":           round(bb_mid_now, 2),
                        "close":            close_now,
                    },
                })

            except Exception as exc:
                logger.debug("Momentum scan error for %s: %s", symbol, exc)

        signals.sort(key=lambda s: s["score"], reverse=True)
        return signals[:10]
