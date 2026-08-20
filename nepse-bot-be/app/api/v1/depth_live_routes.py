"""
Live market-depth routes backed by the high-frequency depth poller.

These endpoints are decoupled from the legacy ``/depth`` routes so the
existing REST-with-fallback contract for single-shot fetches stays
untouched. The "live" namespace serves whatever is in the poller's ring
buffer (fresh when the market is open; stale flag set otherwise).

Endpoints
---------
GET    /depth/live/{symbol}               — latest snapshot + age + staleness
GET    /depth/live/{symbol}/history       — recent ring-buffer entries
GET    /depth/live/stats                  — poller stats & watchlist
POST   /depth/live/watchlist              — replace the watchlist
POST   /depth/live/watchlist/add          — add one symbol
POST   /depth/live/watchlist/remove       — remove one symbol
POST   /depth/live/start                  — start the poller (dev helper)
POST   /depth/live/stop                   — stop the poller (dev helper)
GET    /depth/live/market/session         — current NEPSE session state
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.data import market_hours
from app.services.data.depth_poller import get_depth_poller

router = APIRouter(prefix="/depth/live", tags=["Market Depth — Live"])


# --------------------------------------------------------------------------- #
# Schemas                                                                      #
# --------------------------------------------------------------------------- #

class WatchlistRequest(BaseModel):
    symbols: List[str] = Field(..., min_length=1, max_length=250)


class SymbolRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=20)


# --------------------------------------------------------------------------- #
# Read endpoints                                                               #
# --------------------------------------------------------------------------- #

@router.get("/market/session", summary="Current NEPSE session state")
def market_session():
    s = market_hours.session_status()
    return {
        "status": "success",
        "data": {
            "is_open": s.is_open,
            "is_poll_window": s.is_poll_window,
            "reason": s.reason,
            "now_npt": s.now_npt.isoformat(),
            "seconds_to_open": s.seconds_to_open,
            "seconds_to_close": s.seconds_to_close,
            "session_open": "11:00",
            "session_close": "15:00",
            "timezone": "Asia/Kathmandu (+05:45)",
        },
    }


@router.get("/stats", summary="Depth poller stats")
def poller_stats():
    return {"status": "success", "data": get_depth_poller().stats()}


@router.get("/{symbol}", summary="Latest depth snapshot for a symbol")
def latest_depth(symbol: str):
    snap = get_depth_poller().latest_for(symbol)
    if snap is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No live depth snapshot for '{symbol.upper()}'. "
                "Add it to the watchlist (POST /depth/live/watchlist/add) "
                "and wait one poll cycle."
            ),
        )
    return {"status": "success", "data": snap}


@router.get("/{symbol}/history", summary="Recent depth snapshots for a symbol")
def depth_history(symbol: str, limit: int = Query(default=60, ge=1, le=500)):
    poller = get_depth_poller()
    rows = poller.buffer.history(symbol, limit=limit)
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No history for '{symbol.upper()}'. Is it on the watchlist?",
        )
    return {
        "status": "success",
        "count": len(rows),
        "data": [
            {
                "received_at": r.received_wall,
                "age_seconds": round(
                    __import__("time").monotonic() - r.received_at, 3
                ),
                "payload": r.payload,
            }
            for r in rows
        ],
    }


# --------------------------------------------------------------------------- #
# Watchlist management                                                         #
# --------------------------------------------------------------------------- #

@router.post("/watchlist", summary="Replace the entire watchlist")
async def set_watchlist(req: WatchlistRequest):
    poller = get_depth_poller()
    cleaned = await poller.set_watchlist(req.symbols)
    return {"status": "success", "watchlist": cleaned, "count": len(cleaned)}


@router.post("/watchlist/add", summary="Add a symbol to the watchlist")
async def watchlist_add(req: SymbolRequest):
    poller = get_depth_poller()
    wl = await poller.add(req.symbol)
    return {"status": "success", "watchlist": wl, "count": len(wl)}


@router.post("/watchlist/remove", summary="Remove a symbol from the watchlist")
async def watchlist_remove(req: SymbolRequest):
    poller = get_depth_poller()
    wl = await poller.remove(req.symbol)
    return {"status": "success", "watchlist": wl, "count": len(wl)}


# --------------------------------------------------------------------------- #
# Lifecycle (dev helpers; poller also auto-starts on FastAPI startup)          #
# --------------------------------------------------------------------------- #

@router.post("/start", summary="Start the depth poller background loop")
async def start():
    poller = get_depth_poller()
    await poller.start()
    return {"status": "success", "running": poller.is_running()}


@router.post("/stop", summary="Stop the depth poller background loop")
async def stop():
    poller = get_depth_poller()
    await poller.stop()
    return {"status": "success", "running": poller.is_running()}
