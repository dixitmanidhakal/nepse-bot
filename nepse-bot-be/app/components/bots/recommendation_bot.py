"""
Recommendation Bot
==================
Uses the existing RecommendationEngine (trend + momentum + volume + volatility + drawdown)
to find high-scoring BUY candidates. Parameters scale with timeframe:

  Daily   — min_rows=60,  min_change_n_d=5d,  stop=4.0%, target=10%,  hold≤15d
  Weekly  — min_rows=100, min_change_n_d=20d,  stop=5.5%, target=14%,  hold≤25d
  Monthly — min_rows=180, min_change_n_d=60d,  stop=8.0%, target=22%,  hold≤60d

The lookback window grows so the engine has enough context to capture:
  - Weekly:  multi-week swing trends (20-day change momentum)
  - Monthly: positional trades driven by long-term accumulation patterns

Universe: ALL NEPSE scripts loaded dynamically — no hardcoded list.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.components.bots.base_bot import BaseBot
from app.components.bots.nepse_universe import get_nepse_universe, get_sector, get_sector_map

logger = logging.getLogger("bot.recommendation")

# ── Timeframe parameter sets ───────────────────────────────────────────────────
_TF_PARAMS: Dict[str, Dict[str, Any]] = {
    "daily": {
        "min_rows":   60,
        "stop_pct":   4.0,
        "target_pct": 10.0,
        # require at least flat or positive 5d change
        "min_change_pct": 0.0,
        "change_key":     "change_5d_pct",
    },
    "weekly": {
        "min_rows":   100,
        "stop_pct":   5.5,
        "target_pct": 14.0,
        # require positive 20d trend for weekly
        "min_change_pct": 1.0,
        "change_key":     "change_20d_pct",
    },
    "monthly": {
        "min_rows":   180,
        "stop_pct":   8.0,
        "target_pct": 22.0,
        # require meaningful 60d accumulation for monthly
        "min_change_pct": 3.0,
        "change_key":     "change_20d_pct",   # fallback; reco engine doesn't have 60d built-in
    },
}


class RecommendationBot(BaseBot):
    BOT_ID   = "reco_bot"
    BOT_NAME = "Recommendation Bot"
    STRATEGY = "recommendation"

    DEFAULT_STOP_PCT   = 4.0
    DEFAULT_TARGET_PCT = 10.0
    MAX_HOLD_DAYS      = 15

    def generate_signals(self, db: Session, timeframe: str = "daily") -> List[Dict[str, Any]]:
        p = _TF_PARAMS.get(timeframe, _TF_PARAMS["daily"])
        signals: List[Dict[str, Any]] = []

        try:
            from app.components.recommendation_engine import score_symbol
            from app.services.data.historical_provider import get_historical_provider
        except ImportError as e:
            logger.error("Recommendation imports failed: %s", e)
            return []

        provider = get_historical_provider()
        if not provider.is_available():
            logger.warning("Reco Bot: HistoricalDataProvider not available")
            return []

        universe = get_nepse_universe(provider)
        sector_map = get_sector_map()
        logger.info(
            "Reco Bot[%s] scanning %d symbols | min_rows=%d stop=%.1f%% target=%.1f%%",
            timeframe, len(universe), p["min_rows"], p["stop_pct"], p["target_pct"],
        )

        panel = provider.load_universe(universe, min_rows=p["min_rows"])

        for symbol, df in panel.items():
            try:
                reco = score_symbol(symbol, df)
                if reco is None:
                    continue
                if reco.action != "BUY":
                    continue

                # Timeframe-specific trend filter: require N-day momentum
                change_val = getattr(reco, p["change_key"], None) or 0.0
                if change_val < p["min_change_pct"]:
                    continue

                score = float(reco.score)

                regime = "sideways"
                if reco.rsi_14 and reco.macd_hist:
                    if reco.rsi_14 > 50 and reco.macd_hist > 0:
                        regime = "trending"
                    elif (reco.volatility_annualised or 0) > 0.40:
                        regime = "volatile"

                signals.append({
                    "symbol":      symbol,
                    "score":       score,
                    "signal":      "BUY",
                    "entry_price": float(reco.last_close),
                    "regime":      regime,
                    "sector":      get_sector(symbol, sector_map),
                    "stop_pct":    p["stop_pct"],
                    "target_pct":  p["target_pct"],
                    "context": {
                        "timeframe":             timeframe,
                        "rsi_14":                reco.rsi_14,
                        "macd_hist":             reco.macd_hist,
                        "volume_ratio":          reco.volume_ratio,
                        "volatility":            reco.volatility_annualised,
                        "drawdown_from_high":    reco.drawdown_from_high_pct,
                        "change_5d":             reco.change_5d_pct,
                        "change_20d":            reco.change_20d_pct,
                        "factor_scores":         reco.factor_scores,
                        "rationale":             reco.rationale[:3],
                    },
                })

            except Exception as exc:
                logger.debug("Reco scan error for %s: %s", symbol, exc)

        signals.sort(key=lambda s: s["score"], reverse=True)
        return signals[:10]
