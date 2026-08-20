"""
Enhanced Script Recommendation Engine
=====================================

A second, **separate** recommendation engine that layers extra signals on top
of the classic 5-factor composite produced by
``app.components.recommendation_engine``:

- **Order-block score**  : detects the last strong down-then-up sequence and
                           measures how close price is to a retest zone.
- **Market-depth score** : bid/ask wall presence, order imbalance, and
                           bid-ask spread tightness from the NEPSE order book.
- **Quant score**        : combines advanced quant regime (HMM-style), BOCPD
                           changepoint recency, and conformal VaR severity.

Design goals
------------
1. **Coexist, don't replace**: the classic engine stays canonical. This module
   wraps it and produces an *enhanced* recommendation with extra sub-scores.
2. **Graceful degradation**: if depth or quant signals are missing, the engine
   still scores using whichever components are available; it renormalises
   weights on-the-fly so partial data never distorts the composite.
3. **Explainable**: every enhanced recommendation carries the original factor
   scores plus the new sub-scores and extended rationale.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Mapping, Optional

import numpy as np
import pandas as pd

from app.components import recommendation_engine as classic

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Weights for the enhanced composite.                                         #
# Enhanced sub-scores take 30 % of the composite; the classic composite keeps #
# the remaining 70 % so the baseline ranking stays dominant.                  #
# --------------------------------------------------------------------------- #

ENHANCED_WEIGHTS: Dict[str, float] = {
    "classic": 0.70,
    "order_block": 0.10,
    "depth": 0.10,
    "quant": 0.10,
}


# --------------------------------------------------------------------------- #
# Result dataclass                                                             #
# --------------------------------------------------------------------------- #

@dataclass
class EnhancedRecommendation:
    symbol: str
    action: str                     # BUY / WATCH / AVOID
    score: float                    # 0..100 composite incl. enhancements
    classic_score: float            # 0..100 from classic engine
    last_close: float
    factor_scores: Dict[str, float] = field(default_factory=dict)
    enhanced_scores: Dict[str, float] = field(default_factory=dict)
    effective_weights: Dict[str, float] = field(default_factory=dict)
    rationale: List[str] = field(default_factory=list)
    as_of_date: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        for k in ("score", "classic_score", "last_close"):
            v = d.get(k)
            if isinstance(v, float) and math.isfinite(v):
                d[k] = round(v, 4)
        d["factor_scores"] = {
            k: round(float(v), 4) for k, v in d["factor_scores"].items()
        }
        d["enhanced_scores"] = {
            k: round(float(v), 4) for k, v in d["enhanced_scores"].items()
        }
        d["effective_weights"] = {
            k: round(float(v), 4) for k, v in d["effective_weights"].items()
        }
        return d


# --------------------------------------------------------------------------- #
# Order-block sub-score                                                        #
# --------------------------------------------------------------------------- #

def _order_block_score(df: pd.DataFrame) -> tuple[float, Dict[str, Any]]:
    """
    Classic bullish order-block detection:
    find the last down-candle that was immediately followed by a
    strong up-candle closing above the down-candle's high.

    Output sub-score in 0..1:
    - 1.0 when price is retesting the OB zone (within 2 % above its high).
    - Scales down as price moves further above the OB.
    - 0.0 when no recent valid OB or price is below the OB (invalidated).
    """
    cols = {c.lower(): c for c in df.columns}
    required = {"open", "high", "low", "close"}
    if not required.issubset(cols.keys()) or len(df) < 30:
        return 0.0, {"reason": "insufficient OHLC columns or rows"}

    opens = pd.to_numeric(df[cols["open"]], errors="coerce").to_numpy()
    highs = pd.to_numeric(df[cols["high"]], errors="coerce").to_numpy()
    lows = pd.to_numeric(df[cols["low"]], errors="coerce").to_numpy()
    closes = pd.to_numeric(df[cols["close"]], errors="coerce").to_numpy()
    n = len(closes)

    last_ob: Optional[Dict[str, float]] = None
    # Scan the last 60 candles (cap) for an OB; prefer the most recent one.
    start = max(1, n - 60)
    for i in range(n - 2, start - 1, -1):
        # Down candle at i?
        if not (closes[i] < opens[i]):
            continue
        # Strong up candle at i+1 that closes above the down candle's high?
        up_body = closes[i + 1] - opens[i + 1]
        down_range = highs[i] - lows[i]
        if up_body <= 0 or down_range <= 0:
            continue
        if closes[i + 1] < highs[i]:
            continue
        # Must be a meaningful thrust (up body ≥ 1.2× down-candle range).
        if up_body < 1.2 * down_range:
            continue
        last_ob = {
            "index": i,
            "ob_low": float(lows[i]),
            "ob_high": float(highs[i]),
            "ob_mid": float((highs[i] + lows[i]) / 2.0),
        }
        break

    if last_ob is None:
        return 0.0, {"reason": "no recent bullish order block"}

    last_close = float(closes[-1])
    ob_high = last_ob["ob_high"]
    ob_low = last_ob["ob_low"]
    # Retest proximity: best when price is within ±2 % of OB high, falling off
    # to zero at ±10 % outside the zone. If price crashed below ob_low → 0.
    if last_close < ob_low * 0.98:
        score = 0.0
        reason = "price broke below order block (invalidated)"
    else:
        dist_pct = (last_close - ob_high) / ob_high * 100.0
        if -2.0 <= dist_pct <= 2.0:
            score = 1.0
            reason = "price retesting order-block high"
        elif 2.0 < dist_pct <= 10.0:
            score = max(0.0, 1.0 - (dist_pct - 2.0) / 8.0)
            reason = f"price {dist_pct:+.1f}% above OB high"
        elif -10.0 <= dist_pct < -2.0:
            score = max(0.0, 1.0 - (-2.0 - dist_pct) / 8.0)
            reason = f"price {dist_pct:+.1f}% below OB high (inside zone)"
        else:
            score = 0.1
            reason = f"price {dist_pct:+.1f}% from OB high (far)"

    return float(np.clip(score, 0.0, 1.0)), {
        "ob_high": ob_high,
        "ob_low": ob_low,
        "distance_from_ob_pct": round(
            (last_close - ob_high) / ob_high * 100.0, 2
        ),
        "reason": reason,
    }


# --------------------------------------------------------------------------- #
# Depth sub-score                                                              #
# --------------------------------------------------------------------------- #

def _depth_score(depth: Optional[Mapping[str, Any]]) -> tuple[float, Dict[str, Any]]:
    """
    Score live order-book depth. Expects a mapping with fields commonly
    returned by ``MarketDepth``:
      - has_bid_wall, has_ask_wall        (bool)
      - order_imbalance                   (float, -1..+1 ≈ buy-sell / buy+sell)
      - bid_ask_spread_percent            (float)
      - total_buy_quantity, total_sell_quantity  (floats, optional)

    Score in 0..1:
      +0.35 when a bid wall is present and no ask wall
      +0.25 when order imbalance is meaningfully positive
      +0.15 when bid-ask spread is tight (<0.3 %)
      +0.25 scaled by raw imbalance magnitude (up to ±0.25)

    Returns 0 when depth is missing so it can be safely dropped from the
    composite via weight renormalisation.
    """
    if not depth:
        return 0.0, {"reason": "no depth data"}

    score = 0.0
    reasons: List[str] = []

    has_bid = bool(depth.get("has_bid_wall"))
    has_ask = bool(depth.get("has_ask_wall"))
    if has_bid and not has_ask:
        score += 0.35
        reasons.append("bid wall, no ask wall")
    elif has_bid and has_ask:
        score += 0.15
        reasons.append("both walls present")
    elif has_ask and not has_bid:
        reasons.append("ask wall (bearish)")

    imbalance = depth.get("order_imbalance")
    if isinstance(imbalance, (int, float)) and math.isfinite(imbalance):
        if imbalance > 0.10:
            score += 0.25
            reasons.append(f"positive imbalance {imbalance:+.2f}")
        elif imbalance < -0.10:
            reasons.append(f"negative imbalance {imbalance:+.2f}")
        score += float(np.clip(imbalance, -0.25, 0.25))

    spread = depth.get("bid_ask_spread_percent")
    if isinstance(spread, (int, float)) and math.isfinite(spread):
        if 0 < spread < 0.3:
            score += 0.15
            reasons.append(f"tight spread {spread:.2f}%")

    final = float(np.clip(score, 0.0, 1.0))
    return final, {
        "has_bid_wall": has_bid,
        "has_ask_wall": has_ask,
        "order_imbalance": (
            round(float(imbalance), 4)
            if isinstance(imbalance, (int, float)) and math.isfinite(imbalance)
            else None
        ),
        "bid_ask_spread_percent": (
            round(float(spread), 4)
            if isinstance(spread, (int, float)) and math.isfinite(spread)
            else None
        ),
        "reasons": reasons,
    }


# --------------------------------------------------------------------------- #
# Quant sub-score                                                              #
# --------------------------------------------------------------------------- #

def _quant_score(quant: Optional[Mapping[str, Any]]) -> tuple[float, Dict[str, Any]]:
    """
    Combine advanced quant signals into a 0..1 score.

    Expected optional fields:
      - regime            : "bull" | "neutral" | "bear"
      - regime_prob_bull  : float in 0..1
      - bocpd_cp_recent   : bool (changepoint inside the last 5 bars)
      - conformal_var     : float (absolute daily-return threshold, lower is safer)
      - hmm_state         : int (optional)
    """
    if not quant:
        return 0.0, {"reason": "no quant signals"}

    score = 0.0
    reasons: List[str] = []

    regime = str(quant.get("regime") or "").lower()
    if regime == "bull":
        score += 0.35
        reasons.append("regime bull")
    elif regime == "neutral":
        score += 0.15
        reasons.append("regime neutral")
    elif regime == "bear":
        reasons.append("regime bear")

    prob_bull = quant.get("regime_prob_bull")
    if isinstance(prob_bull, (int, float)) and math.isfinite(prob_bull):
        score += 0.25 * float(np.clip(prob_bull, 0.0, 1.0))
        reasons.append(f"p(bull)={float(prob_bull):.2f}")

    cp_recent = quant.get("bocpd_cp_recent")
    if cp_recent is True:
        # Recent changepoint → lower confidence; deduct a little.
        score -= 0.10
        reasons.append("recent changepoint (caution)")

    var_abs = quant.get("conformal_var")
    if isinstance(var_abs, (int, float)) and math.isfinite(var_abs):
        # Smaller absolute VaR = safer. Reward when |VaR| < 3 %.
        vabs = abs(float(var_abs))
        if vabs < 0.03:
            score += 0.20
            reasons.append(f"VaR tight {vabs:.2%}")
        elif vabs < 0.06:
            score += 0.10
        else:
            reasons.append(f"VaR wide {vabs:.2%}")

    final = float(np.clip(score, 0.0, 1.0))
    return final, {
        "regime": regime or None,
        "regime_prob_bull": (
            round(float(prob_bull), 4)
            if isinstance(prob_bull, (int, float)) and math.isfinite(prob_bull)
            else None
        ),
        "bocpd_cp_recent": bool(cp_recent) if cp_recent is not None else None,
        "conformal_var": (
            round(float(var_abs), 6)
            if isinstance(var_abs, (int, float)) and math.isfinite(var_abs)
            else None
        ),
        "reasons": reasons,
    }


# --------------------------------------------------------------------------- #
# Core scoring                                                                 #
# --------------------------------------------------------------------------- #

def _renormalise(weights: Dict[str, float], dropped: List[str]) -> Dict[str, float]:
    keep = {k: v for k, v in weights.items() if k not in dropped}
    total = sum(keep.values())
    if total <= 0:
        # Nothing left; degenerate case, fall back to classic-only.
        return {"classic": 1.0}
    return {k: v / total for k, v in keep.items()}


def score_symbol(
    symbol: str,
    df: pd.DataFrame,
    depth: Optional[Mapping[str, Any]] = None,
    quant: Optional[Mapping[str, Any]] = None,
    weights: Optional[Mapping[str, float]] = None,
) -> Optional[EnhancedRecommendation]:
    """
    Produce an enhanced recommendation for a single symbol.

    Parameters
    ----------
    symbol : str
        Ticker.
    df : pandas.DataFrame
        OHLCV panel. Same contract as the classic engine.
    depth : optional mapping
        Live order-book snapshot. See ``_depth_score`` for expected fields.
    quant : optional mapping
        Pre-computed advanced-quant signals. See ``_quant_score`` for fields.
    weights : optional mapping
        Override for ``ENHANCED_WEIGHTS``. Unknown keys are ignored.
    """
    classic_rec = classic.score_symbol(symbol, df)
    if classic_rec is None:
        return None

    w = dict(ENHANCED_WEIGHTS)
    if weights:
        w.update({k: float(v) for k, v in weights.items() if k in w})

    ob_sub, ob_details = _order_block_score(df)
    depth_sub, depth_details = _depth_score(depth)
    quant_sub, quant_details = _quant_score(quant)

    # Drop zero-data components from the weight base so they can't dilute.
    dropped: List[str] = []
    if not depth:
        dropped.append("depth")
    if not quant:
        dropped.append("quant")
    eff_weights = _renormalise(w, dropped)

    classic_unit = classic_rec.score / 100.0  # already 0..1
    composite = (
        eff_weights.get("classic", 0.0) * classic_unit
        + eff_weights.get("order_block", 0.0) * ob_sub
        + eff_weights.get("depth", 0.0) * depth_sub
        + eff_weights.get("quant", 0.0) * quant_sub
    )
    score_100 = float(round(np.clip(composite * 100.0, 0.0, 100.0), 2))

    if score_100 >= 70:
        action = "BUY"
    elif score_100 >= 50:
        action = "WATCH"
    else:
        action = "AVOID"

    rationale = list(classic_rec.rationale)
    if ob_details.get("reason"):
        rationale.append(f"OB: {ob_details['reason']}")
    if depth_details.get("reasons"):
        rationale.append("Depth: " + "; ".join(depth_details["reasons"]))
    elif depth_details.get("reason"):
        rationale.append(f"Depth: {depth_details['reason']}")
    if quant_details.get("reasons"):
        rationale.append("Quant: " + "; ".join(quant_details["reasons"]))
    elif quant_details.get("reason"):
        rationale.append(f"Quant: {quant_details['reason']}")

    return EnhancedRecommendation(
        symbol=symbol.upper(),
        action=action,
        score=score_100,
        classic_score=classic_rec.score,
        last_close=classic_rec.last_close,
        factor_scores=dict(classic_rec.factor_scores),
        enhanced_scores={
            "order_block": round(ob_sub, 4),
            "depth": round(depth_sub, 4),
            "quant": round(quant_sub, 4),
        },
        effective_weights=eff_weights,
        rationale=rationale,
        as_of_date=classic_rec.as_of_date,
        details={
            "order_block": ob_details,
            "depth": depth_details,
            "quant": quant_details,
        },
    )


def rank_universe(
    ohlcv_by_symbol: Dict[str, pd.DataFrame],
    depth_by_symbol: Optional[Mapping[str, Mapping[str, Any]]] = None,
    quant_by_symbol: Optional[Mapping[str, Mapping[str, Any]]] = None,
    top_n: int = 20,
    min_score: float = 0.0,
    weights: Optional[Mapping[str, float]] = None,
) -> List[EnhancedRecommendation]:
    """Rank a universe using the enhanced engine."""
    depth_by_symbol = depth_by_symbol or {}
    quant_by_symbol = quant_by_symbol or {}

    scored: List[EnhancedRecommendation] = []
    for symbol, df in ohlcv_by_symbol.items():
        try:
            rec = score_symbol(
                symbol,
                df,
                depth=depth_by_symbol.get(symbol),
                quant=quant_by_symbol.get(symbol),
                weights=weights,
            )
        except Exception as exc:
            logger.debug("Enhanced scoring %s failed: %s", symbol, exc)
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
    "EnhancedRecommendation",
    "ENHANCED_WEIGHTS",
    "score_symbol",
    "rank_universe",
]
