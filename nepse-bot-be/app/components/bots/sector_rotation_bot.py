"""
Sector Rotation Bot
===================
Follows institutional money flow across NEPSE sectors.
Parameters scale with timeframe:

  Daily   — sector gain ≥0.2%, RSI 45-72, SMA20, vol≥1.2×, stop=3.0%, target=7%
  Weekly  — sector gain ≥0.5%, RSI 45-70, SMA50, vol≥1.1×, stop=4.5%, target=11%
  Monthly — sector gain ≥1.0%, RSI 45-65, SMA100,vol≥1.0×, stop=7.0%, target=18%

Entry conditions (all must be met):
  - Stock's sector is one of today's top 3 gaining sectors (≥ min_sector_gain)
  - RSI(14) between rsi_min and rsi_max (healthy momentum)
  - Price above N-day SMA (trend confirmation)
  - Volume ≥ vol_min × 20-day average (institutional participation)

Universe: fetched dynamically from NEPSE sector API.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.components.bots.base_bot import BaseBot
from app.components.bots.nepse_universe import get_sector_map, run_async

logger = logging.getLogger("bot.sector_rotation")

# ── Timeframe parameter sets ───────────────────────────────────────────────────
_TF_PARAMS: Dict[str, Dict[str, Any]] = {
    "daily": {
        "sector_min_gain": 0.2,   # minimum sector % gain to qualify
        "rsi_min":         45,
        "rsi_max":         72,
        "sma_period":      20,
        "vol_min":         1.2,
        "min_rows":        25,
        "stop_pct":        3.0,
        "target_pct":      7.0,
    },
    "weekly": {
        "sector_min_gain": 0.5,
        "rsi_min":         45,
        "rsi_max":         70,
        "sma_period":      50,
        "vol_min":         1.1,
        "min_rows":        55,
        "stop_pct":        4.5,
        "target_pct":      11.0,
    },
    "monthly": {
        "sector_min_gain": 1.0,
        "rsi_min":         45,
        "rsi_max":         65,
        "sma_period":      100,
        "vol_min":         1.0,
        "min_rows":        110,
        "stop_pct":        7.0,
        "target_pct":      18.0,
    },
}


def _rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.diff()
    gain  = delta.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _sector_rotation_score(
    sector_chg_pct: float,
    rsi_now: float,
    close: float,
    sma_now: float,
    volume_ratio: float,
    sector_min_gain: float,
    rsi_min: float,
    rsi_max: float,
    vol_min: float,
) -> Optional[float]:
    """0-100 score. Returns None if hard gates not met."""
    if sector_chg_pct < sector_min_gain:
        return None
    if close < sma_now * 0.995:
        return None
    if not (rsi_min <= rsi_now <= rsi_max):
        return None
    if volume_ratio < vol_min:
        return None

    score = 50.0
    score += min(25.0, sector_chg_pct * 12.0)
    score += 15.0 * min(1.0, max(0.0, (rsi_now - rsi_min) / 25.0))
    trend_bonus = (close - sma_now) / sma_now * 500.0
    score += min(10.0, max(0.0, trend_bonus))
    score += min(5.0, (volume_ratio - vol_min) * 4.0)
    return min(100.0, score)


class SectorRotationBot(BaseBot):
    BOT_ID   = "sector_rotation_bot"
    BOT_NAME = "Sector Rotation Bot"
    STRATEGY = "sector_rotation"

    DEFAULT_STOP_PCT   = 3.0
    DEFAULT_TARGET_PCT = 7.0
    MAX_HOLD_DAYS      = 12

    def generate_signals(self, db: Session, timeframe: str = "daily") -> List[Dict[str, Any]]:
        p = _TF_PARAMS.get(timeframe, _TF_PARAMS["daily"])
        signals: List[Dict[str, Any]] = []

        # ── 1. Derive live sector performance from per-symbol percent_change ──
        # sector_indices.json from yonepse has no price change data, so we
        # calculate sector avg % change from live market snapshot instead.
        winning: List[tuple] = []
        symbols_by_sector: Dict[str, List[str]] = {}
        try:
            from app.services.data.free_sources import aggregator
            live_rows = run_async(aggregator.live_market())
            sector_map = get_sector_map()

            # Group live percent_changes by sector
            sector_changes: Dict[str, List[float]] = {}
            for row in live_rows:
                sym = (row.get("symbol") or "").upper()
                pct_raw = row.get("percent_change")
                if pct_raw is None:
                    continue
                try:
                    pct = float(pct_raw)
                except (TypeError, ValueError):
                    continue
                sector = sector_map.get(sym, "")
                if not sector:
                    continue
                sector_changes.setdefault(sector, []).append(pct)
                symbols_by_sector.setdefault(sector, []).append(sym)

            # Average percent_change per sector → top 3 gaining
            for sector_name, changes in sector_changes.items():
                avg_pct = sum(changes) / len(changes)
                if avg_pct >= p["sector_min_gain"]:
                    winning.append((sector_name, avg_pct, sector_name))

        except Exception as exc:
            logger.warning("Could not derive sector performance from live market: %s", exc)

        winning.sort(key=lambda x: x[1], reverse=True)
        winning = winning[:3]

        if not winning:
            logger.info(
                "SectorRotationBot[%s]: no sectors averaged ≥%.1f%% gain today — skipping",
                timeframe, p["sector_min_gain"],
            )
            return []

        logger.info(
            "SectorRotationBot[%s] | winning sectors: %s | SMA%d vol≥%.1f×",
            timeframe,
            [(s, round(g, 2)) for s, g, _ in winning],
            p["sma_period"], p["vol_min"],
        )

        # ── 2. Load historical provider ───────────────────────────────────────
        try:
            from app.services.data.historical_provider import get_historical_provider
        except ImportError as e:
            logger.error("Imports failed: %s", e)
            return []

        provider = get_historical_provider()
        if not provider.is_available():
            logger.warning("SectorRotationBot: HistoricalDataProvider not available")
            return []

        # ── 3. For each winning sector, use already-mapped symbols → score them ──
        for sector_name, sector_chg, sector_key in winning:
            symbols_in_sector = symbols_by_sector.get(sector_name, [])

            if not symbols_in_sector:
                logger.debug("No stocks mapped for sector '%s'", sector_name)
                continue

            logger.info(
                "Sector '%s' (+%.2f%%): scanning %d stocks",
                sector_name, sector_chg, len(symbols_in_sector),
            )

            panel = provider.load_universe(symbols_in_sector, min_rows=p["min_rows"])

            for symbol, df in panel.items():
                try:
                    if len(df) < p["min_rows"] - 3:
                        continue

                    closes  = df["close"]
                    volumes = df["volume"] if "volume" in df.columns else pd.Series([1.0] * len(df))

                    rsi_s   = _rsi(closes)
                    sma_s   = closes.rolling(p["sma_period"]).mean()
                    vol_avg = volumes.rolling(20).mean()

                    close_now   = float(closes.iloc[-1])
                    rsi_now     = float(rsi_s.iloc[-1])
                    sma_now     = float(sma_s.iloc[-1])
                    vol_now     = float(volumes.iloc[-1])
                    vol_avg_now = float(vol_avg.iloc[-1]) if not math.isnan(float(vol_avg.iloc[-1])) else 1.0
                    volume_ratio = vol_now / vol_avg_now if vol_avg_now > 0 else 1.0

                    score = _sector_rotation_score(
                        sector_chg, rsi_now, close_now, sma_now, volume_ratio,
                        p["sector_min_gain"], p["rsi_min"], p["rsi_max"], p["vol_min"],
                    )
                    if score is None:
                        continue

                    regime = "trending" if sector_chg >= 1.0 else "sideways"
                    signals.append({
                        "symbol":      symbol,
                        "score":       round(score, 1),
                        "signal":      "BUY",
                        "entry_price": close_now,
                        "sector":      sector_name,
                        "regime":      regime,
                        "stop_pct":    p["stop_pct"],
                        "target_pct":  p["target_pct"],
                        "context": {
                            "timeframe":        timeframe,
                            "sector_chg_pct":   round(sector_chg, 2),
                            "rsi_14":           round(rsi_now, 1),
                            f"sma{p['sma_period']}": round(sma_now, 2),
                            "volume_ratio":     round(volume_ratio, 2),
                            "close":            close_now,
                        },
                    })
                except Exception as exc:
                    logger.debug("SectorRotation scan error for %s: %s", symbol, exc)

        signals.sort(key=lambda s: s["score"], reverse=True)
        return signals[:10]
