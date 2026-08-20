"""
Free-Sources Routes
===================

Exposes the `free_sources.aggregator` via HTTP under /api/v1/free/*.

These endpoints are the recommended production surface on Vercel's free
tier, because they do not require direct access to nepalstock.com.np
(which is geo-blocked outside Nepal).

Data origin:
    yonepse (GitHub Actions, ~15 min cadence)
    SamirWagle Nepse-All-Scraper (GitHub Actions, daily)

All endpoints are cached in-memory per-instance via `TTLCache`.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.services.data.free_sources import aggregator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/free", tags=["free-sources"])


# ---------- status / health ----------

@router.get("/health")
async def health():
    """Freshness report across all underlying free sources."""
    return await aggregator.health()


# ---------- live market ----------

@router.get("/market/status")
async def market_status():
    return await aggregator.market_status()


@router.get("/market/summary")
async def market_summary():
    return await aggregator.market_summary()


@router.get("/market/live")
async def live_market():
    data = await aggregator.live_market()
    return {"count": len(data), "data": data}


@router.get("/market/live/{symbol}")
async def live_quote(symbol: str):
    row = await aggregator.live_quote(symbol)
    if not row:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")
    return row


@router.get("/market/top")
async def top_stocks():
    return await aggregator.top_stocks()


# ---------- indices ----------

@router.get("/indices")
async def indices():
    return await aggregator.indices()


@router.get("/indices/sectors")
async def sector_indices():
    return await aggregator.sector_indices()


@router.get("/indices/sectors/{sector}/stocks")
async def sector_stocks(sector: str):
    """
    Return live-snapshot rows for every symbol belonging to `sector`.

    Used by the SectorAnalysis page to show the constituent stocks of a
    selected sector with today's fresh prices/volume/turnover.
    """
    rows = await aggregator.sector_stocks(sector)
    return {
        "sector": sector,
        "count": len(rows),
        "data": rows,
    }


# ---------- depth ----------

@router.get("/depth/{symbol}")
async def partial_depth(symbol: str):
    """
    Best-effort market depth (aggregate bid/ask only).

    Full 20-level order book is not available on Vercel's free tier because
    `nepalstock.com.np` is geo-restricted. This endpoint returns the best
    aggregate supply/demand published by the free GitHub Actions scrapers.
    """
    return await aggregator.partial_depth(symbol)


# ---------- floorsheet ----------

@router.get("/floorsheet/latest")
async def floorsheet_latest(
    symbol: Optional[str] = Query(None, description="Filter to one symbol"),
    limit: int = Query(500, ge=1, le=100_000),
):
    date_str = await aggregator.latest_floorsheet_date()
    rows = await aggregator.floorsheet(target=date_str, symbol=symbol)
    return {
        "date": date_str,
        "symbol": symbol,
        "total": len(rows),
        "data": rows[:limit],
    }


@router.get("/floorsheet/{target_date}")
async def floorsheet_by_date(
    target_date: str,
    symbol: Optional[str] = Query(None),
    limit: int = Query(500, ge=1, le=100_000),
):
    try:
        rows = await aggregator.floorsheet(target=target_date, symbol=symbol)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))
    return {
        "date": target_date,
        "symbol": symbol,
        "total": len(rows),
        "data": rows[:limit],
    }


# ---------- per-symbol series ----------

@router.get("/symbols/{symbol}/prices")
async def symbol_prices(
    symbol: str,
    limit: int = Query(500, ge=1, le=10_000),
    enriched: bool = Query(
        True,
        description=(
            "When true (default), prepend today's live bar from the live "
            "quote snapshot if it is fresher than the historical CSV. "
            "Fixes stale charts when the upstream scraper lags."
        ),
    ),
):
    rows = (
        await aggregator.symbol_prices_enriched(symbol)
        if enriched
        else await aggregator.symbol_prices(symbol)
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"No price data found for {symbol.upper()}")
    return {
        "symbol": symbol.upper(),
        "total": len(rows),
        "data": rows[:limit],
    }


@router.get("/symbols/{symbol}/dividends")
async def symbol_dividends(symbol: str):
    return await aggregator.symbol_dividends(symbol)


@router.get("/symbols/{symbol}/rights")
async def symbol_rights(symbol: str):
    return await aggregator.symbol_rights(symbol)


# ---------- masters ----------

@router.get("/securities")
async def securities():
    data = await aggregator.all_securities()
    return {"count": len(data), "data": data}


@router.get("/brokers")
async def brokers():
    data = await aggregator.brokers()
    return {"count": len(data), "data": data}


@router.get("/ipo/upcoming")
async def upcoming_ipo():
    return await aggregator.upcoming_ipo()


@router.get("/disclosures")
async def disclosures():
    return await aggregator.disclosures()


@router.get("/notices")
async def notices():
    return await aggregator.notices()


@router.get("/dps")
async def dps():
    return await aggregator.dps()


# ---------- live-scored recommendations ----------

@router.get("/recommendations/top")
async def recommendations_top(
    limit: int = Query(30, ge=1, le=500),
    action: Optional[str] = Query(
        None, pattern="^(BUY|WATCH|AVOID)$", description="Filter by action"
    ),
    min_score: float = Query(0.0, ge=0.0, le=100.0),
):
    """
    Fresh recommendations computed from today's live snapshot.

    Unlike `/api/v1/recommendations/top` (which scores against the bundled
    SQLite history that can be days/weeks old on a free-tier deploy), this
    endpoint recomputes scores every request from the live yonepse snapshot,
    so it always reflects today's market action.
    """
    return await aggregator.recommendations_live(
        limit=limit, action=action, min_score=min_score
    )


@router.get("/recommendations/universe")
async def recommendations_universe():
    rows = await aggregator.live_market()
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()
    return {
        "status": "success",
        "data": {
            "total_symbols": len(rows),
            "total_rows": len(rows),
            "earliest_date": today,
            "latest_date": today,
            "db_path": "free-provider:live-snapshot",
        },
    }
