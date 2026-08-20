"""
Enhanced Recommendation Routes
==============================

FastAPI endpoints for the **enhanced** recommendation engine
(``app.components.enhanced_recommendation_engine``).

This is a *separate* surface from ``/api/v1/recommendations`` — the classic
engine at that path is preserved exactly as-is. The enhanced engine adds:

- Order-block proximity score.
- Live market-depth score (bid/ask walls, imbalance, spread).
- Advanced quant score (regime / changepoint / conformal VaR).

Endpoints
---------
GET  /recommendations-enhanced/top
GET  /recommendations-enhanced/{symbol}
GET  /recommendations-enhanced/explain/{symbol}
POST /recommendations-enhanced/score
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.components import enhanced_recommendation_engine as eng
from app.services.data.depth_poller import get_depth_poller
from app.services.data.historical_provider import get_historical_provider

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/recommendations-enhanced",
    tags=["Recommendations — Enhanced"],
)


# --------------------------------------------------------------------------- #
# Schemas                                                                      #
# --------------------------------------------------------------------------- #

class DepthSnapshot(BaseModel):
    has_bid_wall: Optional[bool] = None
    has_ask_wall: Optional[bool] = None
    order_imbalance: Optional[float] = Field(
        default=None, ge=-1.0, le=1.0, description="(buy_qty - sell_qty) / total"
    )
    bid_ask_spread_percent: Optional[float] = Field(default=None, ge=0.0)
    total_buy_quantity: Optional[float] = Field(default=None, ge=0.0)
    total_sell_quantity: Optional[float] = Field(default=None, ge=0.0)


class QuantSignals(BaseModel):
    regime: Optional[str] = Field(
        default=None, description="'bull' | 'neutral' | 'bear'"
    )
    regime_prob_bull: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    bocpd_cp_recent: Optional[bool] = None
    conformal_var: Optional[float] = None
    hmm_state: Optional[int] = None


class ScoreEnhancedRequest(BaseModel):
    symbols: List[str] = Field(..., min_length=1)
    min_rows: int = Field(default=60, ge=20, le=1000)
    min_score: float = Field(default=0.0, ge=0.0, le=100.0)
    weights: Optional[Dict[str, float]] = None
    depth_by_symbol: Optional[Dict[str, DepthSnapshot]] = None
    quant_by_symbol: Optional[Dict[str, QuantSignals]] = None


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _require_provider():
    provider = get_historical_provider()
    if not provider.is_available():
        raise HTTPException(
            status_code=503,
            detail=(
                "Historical SQLite DB not available. "
                "Set QUANT_TERMINAL_DB_PATH in .env and ensure the file exists."
            ),
        )
    return provider


def _to_plain(
    mapping: Optional[Mapping[str, BaseModel]],
) -> Dict[str, Dict[str, Any]]:
    if not mapping:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in mapping.items():
        if hasattr(v, "model_dump"):
            out[k.upper()] = v.model_dump(exclude_none=True)
        elif isinstance(v, Mapping):
            out[k.upper()] = dict(v)
    return out


# --------------------------------------------------------------------------- #
# Endpoints                                                                    #
# --------------------------------------------------------------------------- #

@router.get("/top", summary="Top enhanced recommendations across the universe")
def top_enhanced(
    limit: int = Query(default=20, ge=1, le=200),
    min_score: float = Query(default=0.0, ge=0.0, le=100.0),
    min_rows: int = Query(default=120, ge=60, le=1000),
    action: Optional[str] = Query(default=None, description="BUY / WATCH / AVOID"),
    max_symbols: Optional[int] = Query(default=None, ge=1),
    use_live_depth: bool = Query(
        default=True,
        description="Pull live depth snapshots from the poller's ring buffer.",
    ),
):
    provider = _require_provider()
    panel = provider.load_universe(min_rows=min_rows, max_symbols=max_symbols)

    depth_map: Dict[str, Dict[str, Any]] = {}
    if use_live_depth and panel:
        try:
            depth_map = get_depth_poller().depth_for_engine(panel.keys())
        except Exception:
            depth_map = {}

    recs = eng.rank_universe(
        panel,
        depth_by_symbol=depth_map,
        top_n=0,
        min_score=min_score,
    )
    if action:
        recs = [r for r in recs if r.action == action.upper()]
    recs = recs[:limit]
    return {
        "status": "success",
        "count": len(recs),
        "universe_size": len(panel),
        "live_depth_symbols": len(depth_map),
        "data": [r.as_dict() for r in recs],
    }


def _live_depth_for(symbol: str) -> Optional[Dict[str, Any]]:
    try:
        snap = get_depth_poller().latest_for(symbol)
    except Exception:
        return None
    if snap and not snap.get("is_stale"):
        return snap
    return None


@router.get("/{symbol}", summary="Enhanced score for a single symbol")
def score_one(
    symbol: str,
    min_rows: int = Query(default=60, ge=20, le=1000),
    use_live_depth: bool = Query(default=True),
):
    provider = _require_provider()
    df = provider.load_ohlcv(symbol, min_rows=min_rows)
    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"Symbol '{symbol}' not found or has fewer than {min_rows} rows.",
        )
    depth = _live_depth_for(symbol) if use_live_depth else None
    rec = eng.score_symbol(symbol, df, depth=depth)
    if rec is None:
        raise HTTPException(status_code=422, detail=f"Cannot score '{symbol}'.")
    return {"status": "success", "data": rec.as_dict()}


@router.get("/explain/{symbol}", summary="Verbose enhanced breakdown")
def explain(
    symbol: str,
    min_rows: int = Query(default=60, ge=20, le=1000),
    use_live_depth: bool = Query(default=True),
):
    provider = _require_provider()
    df = provider.load_ohlcv(symbol, min_rows=min_rows)
    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"Symbol '{symbol}' not found or has fewer than {min_rows} rows.",
        )
    depth = _live_depth_for(symbol) if use_live_depth else None
    rec = eng.score_symbol(symbol, df, depth=depth)
    if rec is None:
        raise HTTPException(status_code=422, detail=f"Cannot score '{symbol}'.")
    payload = rec.as_dict()
    payload["weights"] = eng.ENHANCED_WEIGHTS
    payload["history_rows"] = len(df)
    payload["live_depth_used"] = depth is not None
    return {"status": "success", "data": payload}


@router.post("/score", summary="Enhanced score for a user-supplied list")
def score_many(req: ScoreEnhancedRequest):
    provider = _require_provider()
    symbols_upper = [s.upper() for s in req.symbols]
    panel = provider.load_universe(symbols=symbols_upper, min_rows=req.min_rows)
    if not panel:
        raise HTTPException(
            status_code=404,
            detail="None of the supplied symbols had sufficient history.",
        )
    recs = eng.rank_universe(
        panel,
        depth_by_symbol=_to_plain(req.depth_by_symbol),
        quant_by_symbol=_to_plain(req.quant_by_symbol),
        top_n=0,
        min_score=req.min_score,
        weights=req.weights,
    )
    return {
        "status": "success",
        "count": len(recs),
        "universe_size": len(panel),
        "data": [r.as_dict() for r in recs],
    }
