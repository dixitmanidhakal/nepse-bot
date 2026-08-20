"""
Volume Breakout Bot
===================
Detects stocks with an unusual volume spike combined with a price breakout
above the N-day high. Parameters scale with timeframe:

  Daily   — vol≥2.5×, 20d-high,  RSI 45-78, stop=3.5%, target=8%,  hold≤10d
  Weekly  — vol≥2.0×, 50d-high,  RSI 50-75, stop=5.0%, target=12%, hold≤25d
  Monthly — vol≥1.8×, 100d-high, RSI 50-70, stop=7.0%, target=20%, hold≤60d

Entry conditions (all must be met):
  - Volume ≥ threshold × 20-day average volume
  - Close within 2% of or above the N-day high (breakout zone)
  - RSI(14) in healthy range (not overbought)
  - Price change today ≥ 0% (no red-day breakouts)
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.components.bots.base_bot import BaseBot
from app.components.bots.nepse_universe import get_nepse_universe, get_sector, get_sector_map, run_async

logger = logging.getLogger("bot.volume_breakout")

# ── Timeframe parameter sets ───────────────────────────────────────────────────
_TF_PARAMS: Dict[str, Dict[str, Any]] = {
    "daily": {
        "vol_min":      2.5,
        "lookback_high": 20,
        "rsi_min":      45,
        "rsi_max":      78,
        "min_rows":     25,
        "stop_pct":     3.5,
        "target_pct":   8.0,
    },
    "weekly": {
        "vol_min":      2.0,
        "lookback_high": 50,
        "rsi_min":      50,
        "rsi_max":      75,
        "min_rows":     55,
        "stop_pct":     5.0,
        "target_pct":   12.0,
    },
    "monthly": {
        "vol_min":      1.8,
        "lookback_high": 100,
        "rsi_min":      50,
        "rsi_max":      70,
        "min_rows":     110,
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


def _volume_breakout_score(
    volume_ratio: float,
    close_now: float,
    high_nd: float,
    rsi_now: float,
    chg_pct: float,
    vol_min: float,
    rsi_min: float,
    rsi_max: float,
) -> Optional[float]:
    """0-100 score. Returns None if hard gates not met."""
    if volume_ratio < vol_min:
        return None
    if close_now < high_nd * 0.98:
        return None
    if not (rsi_min <= rsi_now <= rsi_max):
        return None
    if chg_pct < 0:
        return None

    score = 50.0

    # Volume spike (up to 30 pts)
    vol_score = min(30.0, (volume_ratio - vol_min) * 8.0 + 10.0)
    score += vol_score

    # Breakout proximity (up to 20 pts): from 2% below high → 0 pts; at/above → 20 pts
    proximity = (close_now - high_nd * 0.98) / (high_nd * 0.02)
    score += min(20.0, max(0.0, proximity * 20.0))

    # RSI sweet-spot (up to 10 pts)
    score += 10.0 * min(1.0, max(0.0, (rsi_now - rsi_min) / 25.0))

    # Positive day change bonus (up to 5 pts)
    score += min(5.0, chg_pct * 1.5)

    return min(100.0, score)


class VolumeBreakoutBot(BaseBot):
    BOT_ID   = "volume_breakout_bot"
    BOT_NAME = "Volume Breakout Bot"
    STRATEGY = "volume_breakout"

    DEFAULT_STOP_PCT   = 3.5
    DEFAULT_TARGET_PCT = 8.0
    MAX_HOLD_DAYS      = 7

    def generate_signals(self, db: Session, timeframe: str = "daily") -> List[Dict[str, Any]]:
        p = _TF_PARAMS.get(timeframe, _TF_PARAMS["daily"])
        signals: List[Dict[str, Any]] = []

        # ── 1. Fetch live market for today's % change pre-filter ─────────────
        live_info: Dict[str, Dict[str, float]] = {}
        try:
            from app.services.data.free_sources import aggregator
            live_rows = run_async(aggregator.live_market())
            for row in live_rows:
                sym = str(row.get("symbol") or row.get("Symbol") or "").upper()
                if not sym:
                    continue
                ltp = row.get("ltp") or row.get("lastTradedPrice") or row.get("close") or 0
                chg = row.get("percentChange") or row.get("percent_change") or row.get("change") or 0
                try:
                    live_info[sym] = {"ltp": float(ltp), "chg_pct": float(chg)}
                except (TypeError, ValueError):
                    pass
        except Exception as exc:
            logger.warning("Could not fetch live market data: %s", exc)

        # ── 2. Historical provider ────────────────────────────────────────────
        try:
            from app.services.data.historical_provider import get_historical_provider
        except ImportError as e:
            logger.error("Imports failed: %s", e)
            return []

        provider = get_historical_provider()
        if not provider.is_available():
            logger.warning("VolumeBreakoutBot: HistoricalDataProvider not available")
            return []

        sector_map = get_sector_map()
        db_universe = get_nepse_universe(provider)
        universe = list(set(db_universe + list(live_info.keys())))

        logger.info(
            "Volume Breakout Bot[%s] scanning %d symbols | vol≥%.1f× %dd-high RSI %d-%d",
            timeframe, len(universe),
            p["vol_min"], p["lookback_high"], p["rsi_min"], p["rsi_max"],
        )

        for symbol in universe:
            info    = live_info.get(symbol, {})
            chg_pct = info.get("chg_pct", 0.0)
            ltp     = info.get("ltp", 0.0)

            # Pre-filter: only up-days
            if chg_pct < 0:
                continue

            try:
                df = provider.load_ohlcv(symbol, min_rows=p["min_rows"])
                if df.empty or len(df) < p["min_rows"] - 3:
                    continue

                closes  = df["close"]
                volumes = df["volume"] if "volume" in df.columns else pd.Series([1.0] * len(df))
                highs   = df["high"] if "high" in df.columns else closes

                rsi_s       = _rsi(closes)
                vol_avg     = volumes.rolling(20).mean()
                high_nd_s   = highs.rolling(p["lookback_high"]).max()

                close_now   = ltp if ltp > 0 else float(closes.iloc[-1])
                rsi_now     = float(rsi_s.iloc[-1])
                high_nd     = float(high_nd_s.iloc[-1])
                vol_last    = float(volumes.iloc[-1])
                vol_avg_now = float(vol_avg.iloc[-1]) if not math.isnan(float(vol_avg.iloc[-1])) else 1.0
                volume_ratio = vol_last / vol_avg_now if vol_avg_now > 0 else 1.0

                score = _volume_breakout_score(
                    volume_ratio, close_now, high_nd, rsi_now, chg_pct,
                    p["vol_min"], p["rsi_min"], p["rsi_max"],
                )
                if score is None:
                    continue

                regime = "trending" if rsi_now > 55 else "sideways"

                signals.append({
                    "symbol":      symbol,
                    "score":       round(score, 1),
                    "signal":      "BUY",
                    "entry_price": close_now,
                    "sector":      get_sector(symbol, sector_map),
                    "regime":      regime,
                    "stop_pct":    p["stop_pct"],
                    "target_pct":  p["target_pct"],
                    "context": {
                        "timeframe":      timeframe,
                        "volume_ratio":   round(volume_ratio, 2),
                        f"high_{p['lookback_high']}d": round(high_nd, 2),
                        "rsi_14":         round(rsi_now, 1),
                        "chg_pct_today":  round(chg_pct, 2),
                        "close":          close_now,
                    },
                })
            except Exception as exc:
                logger.debug("VolumeBreakout scan error for %s: %s", symbol, exc)

        signals.sort(key=lambda s: s["score"], reverse=True)
        return signals[:10]
