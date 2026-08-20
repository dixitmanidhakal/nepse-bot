"""
SMC Bot
=======
Uses Smart Money Concepts signals (Order Blocks, BOS, FVG, Liquidity Sweeps)
to identify high-confidence paper trade entries.

Parameters scale with timeframe:
  Daily   — 30 bars of context, stop=3.5%, target=8%,  hold≤12d
  Weekly  — 60 bars of context, stop=5.0%, target=12%, hold≤25d
  Monthly — 120 bars of context,stop=7.0%, target=20%, hold≤60d

Longer bar context for weekly/monthly lets the SMC engine identify more
significant institutional Order Blocks that span multi-week ranges.

Universe: ALL NEPSE scripts loaded dynamically — no hardcoded list.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.components.bots.base_bot import BaseBot
from app.components.bots.nepse_universe import get_nepse_universe, get_sector, get_sector_map

logger = logging.getLogger("bot.smc")

# ── Timeframe parameter sets ───────────────────────────────────────────────────
_TF_PARAMS: Dict[str, Dict[str, Any]] = {
    "daily": {
        "min_bars":   30,
        "stop_pct":   3.5,
        "target_pct": 8.0,
    },
    "weekly": {
        "min_bars":   60,
        "stop_pct":   5.0,
        "target_pct": 12.0,
    },
    "monthly": {
        "min_bars":   120,
        "stop_pct":   7.0,
        "target_pct": 20.0,
    },
}


class SMCBot(BaseBot):
    BOT_ID   = "smc_bot"
    BOT_NAME = "SMC Bot"
    STRATEGY = "smc"

    DEFAULT_STOP_PCT   = 3.5
    DEFAULT_TARGET_PCT = 8.0
    MAX_HOLD_DAYS      = 12

    def generate_signals(self, db: Session, timeframe: str = "daily") -> List[Dict[str, Any]]:
        p = _TF_PARAMS.get(timeframe, _TF_PARAMS["daily"])
        signals: List[Dict[str, Any]] = []

        try:
            from app.components.smc_engine import analyse
            from app.services.data.historical_provider import get_historical_provider
        except ImportError as e:
            logger.error("SMC imports failed: %s", e)
            return []

        provider = get_historical_provider()
        if not provider.is_available():
            logger.warning("SMC Bot: HistoricalDataProvider not available")
            return []

        universe = get_nepse_universe(provider)
        sector_map = get_sector_map()
        logger.info(
            "SMC Bot[%s] scanning %d symbols | bars=%d stop=%.1f%% target=%.1f%%",
            timeframe, len(universe), p["min_bars"], p["stop_pct"], p["target_pct"],
        )

        for symbol in universe:
            try:
                df = provider.load_ohlcv(symbol, min_rows=p["min_bars"])
                if df.empty or len(df) < p["min_bars"]:
                    continue

                bars = df[["open", "high", "low", "close", "volume", "date"]].to_dict("records")
                result = analyse(symbol, bars)
                if result is None:
                    continue
                if result.signal != "BUY":
                    continue

                regime = _smc_regime(result)
                entry_price = result.last_close

                active_obs = [o for o in result.order_blocks if o.get("kind") == "bullish" and o.get("active")]
                if active_obs:
                    ob_low   = active_obs[-1]["ob_low"]
                    stop_pct = max(p["stop_pct"] * 0.5, (entry_price - ob_low) / entry_price * 100 + 0.5)
                    # for longer timeframes, use the wider of OB-based or param-based stop
                    stop_pct = max(stop_pct, p["stop_pct"])
                else:
                    stop_pct = p["stop_pct"]

                target_pct = max(stop_pct * 2.0, p["target_pct"])

                signals.append({
                    "symbol":      symbol,
                    "score":       result.score,
                    "signal":      result.signal,
                    "entry_price": entry_price,
                    "stop_pct":    stop_pct,
                    "target_pct":  target_pct,
                    "regime":      regime,
                    "sector":      get_sector(symbol, sector_map),
                    "context": {
                        "timeframe":        timeframe,
                        "zone":             result.zone,
                        "zone_pct":         result.zone_pct,
                        "trend":            result.trend,
                        "confidence":       result.confidence,
                        "rationale":        result.rationale[:3],
                        "bos_count":        len(result.bos_events),
                        "active_ob":        len(active_obs),
                        "open_fvg":         len([f for f in result.fvg_zones if not f.get("filled")]),
                        "liquidity_sweeps": len(result.liquidity_sweeps),
                    },
                })

            except Exception as exc:
                logger.debug("SMC scan error for %s: %s", symbol, exc)

        signals.sort(key=lambda s: s["score"], reverse=True)
        return signals[:10]


def _smc_regime(result: Any) -> str:
    """Estimate market regime from SMC result."""
    bos_events  = result.bos_events or []
    choch_count = sum(1 for b in bos_events if b.get("is_choch"))
    if len(bos_events) >= 4 and choch_count >= 1:
        return "trending"
    if len(result.fvg_zones or []) > 8:
        return "volatile"
    return "sideways"
