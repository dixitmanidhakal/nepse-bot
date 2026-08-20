"""
SMC (Smart Money Concepts) Routes
===================================

Exposes SMC analysis via /api/v1/free/smc/*.

These endpoints use the same free-tier data sources as the rest of the
`/api/v1/free/*` surface — yonepse live snapshot + SamirWagle OHLCV CSV.
No database or paid API required.

Endpoints
---------
GET /api/v1/free/smc/{symbol}
    Full SMC analysis for a single symbol (Order Blocks, FVGs, BOS/ChoCH,
    Liquidity Sweeps, Premium/Discount zone, composite signal).

GET /api/v1/free/smc/top
    Run SMC on the top-N live market symbols (by turnover) and return those
    with a BUY or SELL signal.  Useful for a "watchlist" view.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.services.data.free_sources import aggregator
from app.components.smc_engine import analyse as smc_analyse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/free/smc", tags=["smc"])


# ── single symbol ─────────────────────────────────────────────────────────────

@router.get("/{symbol}")
async def smc_symbol(
    symbol: str,
    limit: int = Query(120, ge=30, le=500, description="Max OHLCV bars to load"),
):
    """
    Full SMC analysis for one symbol.

    Returns detected structures (swing points, BOS/ChoCH, order blocks,
    FVGs, liquidity sweeps), the premium/discount zone, trend direction,
    and a composite BUY / SELL / WATCH signal.
    """
    sym = symbol.strip().upper()

    # Fetch OHLCV (newest-first from aggregator)
    rows = await aggregator.symbol_prices_enriched(sym)
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No OHLCV data found for {sym}. The symbol may not be listed.",
        )

    # Reverse to oldest-first and apply limit
    bars = list(reversed(rows))[-limit:]

    result = smc_analyse(sym, bars)
    if result is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Insufficient historical data for {sym} "
                f"({len(bars)} bars, minimum 30 required)."
            ),
        )

    return {
        "status": "success",
        "symbol": sym,
        "bars_used": len(bars),
        "data": result.as_dict(),
    }


# ── top SMC signals ───────────────────────────────────────────────────────────

@router.get("")
async def smc_top(
    limit: int = Query(
        20, ge=1, le=100,
        description="Max number of symbols to return"
    ),
    signal: Optional[str] = Query(
        None,
        description="Filter by signal: BUY | SELL | WATCH"
    ),
    min_score: float = Query(
        0.0, ge=0.0, le=100.0,
        description="Minimum SMC score to include"
    ),
    universe_size: int = Query(
        60, ge=10, le=200,
        description="How many top-turnover symbols to evaluate"
    ),
):
    """
    Scan the top `universe_size` NEPSE symbols (by today's turnover) through
    the SMC engine and return those matching the requested `signal`.

    This is compute-heavy because it fetches OHLCV for each symbol.
    Results are limited to `limit` after filtering.
    """
    # Step 1: Get live market to find top symbols by turnover (or volume as fallback)
    live = await aggregator.live_market()
    if not live:
        raise HTTPException(status_code=503, detail="Live market data unavailable")

    def _rank_key(r):
        """Rank by turnover; fall back to volume*ltp proxy when turnover absent."""
        try:
            t = float(r.get("turnover") or 0)
            if t > 0:
                return t
            # proxy: volume × ltp
            vol = float(r.get("volume") or r.get("qty") or 0)
            ltp = float(r.get("ltp") or 0)
            return vol * ltp
        except (TypeError, ValueError):
            return 0.0

    sorted_live = sorted(live, key=_rank_key, reverse=True)
    # Include all symbols with any price activity (ltp > 0)
    candidates = [
        r["symbol"] for r in sorted_live
        if r.get("symbol") and _rank_key(r) > 0
    ][:universe_size]

    if not candidates:
        raise HTTPException(status_code=503, detail="No symbols with market activity found")

    # Step 2: Fetch OHLCV and analyse each symbol concurrently
    async def _analyse_one(sym: str):
        try:
            rows = await aggregator.symbol_prices_enriched(sym)
            if not rows:
                return None
            bars = list(reversed(rows))[-120:]
            return smc_analyse(sym, bars)
        except Exception as exc:  # noqa: BLE001
            logger.debug("SMC top: %s failed — %s", sym, exc)
            return None

    # Run all analyses concurrently (bounded)
    CONCURRENCY = 8
    results = []
    for i in range(0, len(candidates), CONCURRENCY):
        batch = candidates[i : i + CONCURRENCY]
        batch_results = await asyncio.gather(*[_analyse_one(s) for s in batch])
        results.extend(r for r in batch_results if r is not None)

    # Step 3: Filter and sort
    if signal:
        sig_upper = signal.upper()
        results = [r for r in results if r.signal == sig_upper]

    if min_score > 0:
        results = [r for r in results if r.score >= min_score]

    results.sort(key=lambda r: r.score, reverse=True)
    top = results[:limit]

    return {
        "status": "success",
        "count": len(top),
        "universe_scanned": len(candidates),
        "signal_filter": signal.upper() if signal else "ALL",
        "data": [r.as_dict() for r in top],
    }
