"""
Recommendation Routes
=====================

FastAPI endpoints that expose the deterministic script recommendation
engine built on top of the historical OHLCV provider.

Endpoints
---------
GET  /recommendations/top         - Top N scored stock picks.
GET  /recommendations/{symbol}    - Score for a single symbol.
GET  /recommendations/universe    - Summary of the symbol universe in the DB.
POST /recommendations/score       - Score a user-supplied list of symbols.
GET  /recommendations/explain/{symbol} - Verbose factor breakdown.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.components import recommendation_engine as rec_engine
from app.services.data.historical_provider import get_historical_provider


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/recommendations", tags=["Recommendations"])


# --------------------------------------------------------------------------- #
# Schemas                                                                      #
# --------------------------------------------------------------------------- #

class ScoreSymbolsRequest(BaseModel):
    symbols: List[str] = Field(..., min_length=1, description="Symbols to score")
    min_rows: int = Field(default=60, ge=20, le=1000)
    min_score: float = Field(default=0.0, ge=0.0, le=100.0)
    weights: Optional[Dict[str, float]] = None


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


# --------------------------------------------------------------------------- #
# Endpoints                                                                    #
# --------------------------------------------------------------------------- #

@router.get("/top", summary="Top scored recommendations across the universe")
def top_recommendations(
    limit: int = Query(default=20, ge=1, le=200),
    min_score: float = Query(default=0.0, ge=0.0, le=100.0),
    min_rows: int = Query(default=120, ge=60, le=1000),
    action: Optional[str] = Query(
        default=None, description="Filter by action: BUY / WATCH / AVOID"
    ),
    max_symbols: Optional[int] = Query(
        default=None,
        ge=1,
        description="Cap the universe size (useful for smoke-testing).",
    ),
):
    provider = _require_provider()
    panel = provider.load_universe(min_rows=min_rows, max_symbols=max_symbols)
    recs = rec_engine.rank_universe(
        panel, top_n=0, min_score=min_score
    )
    if action:
        action_u = action.upper()
        recs = [r for r in recs if r.action == action_u]
    recs = recs[:limit]
    return {
        "status": "success",
        "count": len(recs),
        "universe_size": len(panel),
        "data": [r.as_dict() for r in recs],
    }


@router.get("/universe", summary="Summary of the symbol universe")
def universe_summary():
    provider = _require_provider()
    meta = provider.symbol_metadata()
    total_symbols = len(meta)
    total_rows = sum(m.row_count for m in meta.values())
    last_dates = [m.last_date for m in meta.values() if m.last_date]
    first_dates = [m.first_date for m in meta.values() if m.first_date]
    return {
        "status": "success",
        "data": {
            "total_symbols": total_symbols,
            "total_rows": total_rows,
            "earliest_date": min(first_dates) if first_dates else None,
            "latest_date": max(last_dates) if last_dates else None,
            "db_path": str(provider.db_path()),
        },
    }


@router.get("/{symbol}", summary="Score a single symbol")
def score_symbol(
    symbol: str,
    min_rows: int = Query(default=60, ge=20, le=1000),
):
    provider = _require_provider()
    df = provider.load_ohlcv(symbol, min_rows=min_rows)
    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"Symbol '{symbol}' not found or has fewer than {min_rows} rows.",
        )
    rec = rec_engine.score_symbol(symbol, df)
    if rec is None:
        raise HTTPException(
            status_code=422,
            detail=f"Not enough history to score '{symbol}'.",
        )
    return {"status": "success", "data": rec.as_dict()}


@router.get("/explain/{symbol}", summary="Verbose factor breakdown for one symbol")
def explain_symbol(symbol: str, min_rows: int = Query(default=60, ge=20, le=1000)):
    provider = _require_provider()
    df = provider.load_ohlcv(symbol, min_rows=min_rows)
    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"Symbol '{symbol}' not found or has fewer than {min_rows} rows.",
        )
    rec = rec_engine.score_symbol(symbol, df)
    if rec is None:
        raise HTTPException(status_code=422, detail="Cannot score this symbol.")
    data = rec.as_dict()
    # Add the weights used so the caller can audit the composite math.
    data["weights"] = rec_engine.WEIGHTS
    data["history_rows"] = len(df)
    return {"status": "success", "data": data}


@router.post("/score", summary="Score a user-supplied list of symbols")
def score_many(req: ScoreSymbolsRequest):
    provider = _require_provider()
    panel = provider.load_universe(
        symbols=req.symbols, min_rows=req.min_rows
    )
    if not panel:
        raise HTTPException(
            status_code=404,
            detail="None of the supplied symbols had sufficient history.",
        )
    recs = rec_engine.rank_universe(
        panel,
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
