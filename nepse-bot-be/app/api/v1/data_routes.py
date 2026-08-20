"""
Data API Routes

This module defines API endpoints for data fetching operations.
"""

import asyncio
import concurrent.futures
import os
import logging
import time
from typing import Optional, List, Dict, Any
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.data import DataFetcherService

logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/data", tags=["Data Fetching"])


@router.post("/fetch-market")
async def fetch_market_data(db: Session = Depends(get_db)):
    """
    Fetch market indices and sector data from NEPSE API
    
    This endpoint:
    - Fetches NEPSE index
    - Fetches all sector indices
    - Updates database with latest data
    
    Returns:
        Dictionary with operation results
    """
    try:
        logger.info("API: Fetching market data...")
        service = DataFetcherService(db)
        result = service.fetch_market_data_only()
        return result
    except Exception as e:
        logger.error(f"Error in fetch_market_data endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/fetch-stocks")
async def fetch_stock_list(db: Session = Depends(get_db)):
    """
    Fetch stock list from NEPSE API
    
    This endpoint:
    - Fetches all listed stocks
    - Updates stock information
    - Links stocks to sectors
    
    Returns:
        Dictionary with operation results
    """
    try:
        logger.info("API: Fetching stock list...")
        service = DataFetcherService(db)
        result = service.fetch_stock_data_only()
        return result
    except Exception as e:
        logger.error(f"Error in fetch_stock_list endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/fetch-ohlcv/{symbol}")
async def fetch_ohlcv_data(
    symbol: str,
    days: int = Query(default=30, ge=1, le=365, description="Number of days to fetch"),
    db: Session = Depends(get_db)
):
    """
    Fetch OHLCV (Open, High, Low, Close, Volume) data for a stock
    
    Args:
        symbol: Stock symbol (e.g., NABIL)
        days: Number of days of historical data to fetch (1-365)
    
    Returns:
        Dictionary with operation results
    """
    try:
        logger.info(f"API: Fetching OHLCV for {symbol}...")
        service = DataFetcherService(db)
        result = service.fetch_ohlcv_for_symbol(symbol=symbol.upper(), days=days)
        return result
    except Exception as e:
        logger.error(f"Error in fetch_ohlcv_data endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/fetch-market-depth/{symbol}")
async def fetch_market_depth(
    symbol: str,
    db: Session = Depends(get_db)
):
    """
    Fetch market depth (order book) data for a stock
    
    Args:
        symbol: Stock symbol (e.g., NABIL)
    
    Returns:
        Dictionary with operation results including buy/sell orders
    """
    try:
        logger.info(f"API: Fetching market depth for {symbol}...")
        service = DataFetcherService(db)
        result = service.fetch_market_depth_for_symbol(symbol=symbol.upper())
        return result
    except Exception as e:
        logger.error(f"Error in fetch_market_depth endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/fetch-floorsheet")
async def fetch_floorsheet_data(
    symbol: Optional[str] = Query(None, description="Stock symbol (optional, fetch all if not provided)"),
    trade_date: Optional[date] = Query(None, description="Trade date (optional, fetch today if not provided)"),
    db: Session = Depends(get_db)
):
    """
    Fetch floorsheet (trade details) data
    
    Args:
        symbol: Stock symbol (optional, fetches all stocks if not provided)
        trade_date: Trade date (optional, fetches today's data if not provided)
    
    Returns:
        Dictionary with operation results including trade details
    """
    try:
        if symbol:
            logger.info(f"API: Fetching floorsheet for {symbol}...")
            symbol = symbol.upper()
        else:
            logger.info("API: Fetching floorsheet for all stocks...")
        
        service = DataFetcherService(db)
        result = service.fetch_floorsheet_for_symbol(symbol=symbol, trade_date=trade_date)
        return result
    except Exception as e:
        logger.error(f"Error in fetch_floorsheet_data endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/fetch-all")
async def fetch_all_data(
    include_ohlcv: bool = Query(default=True, description="Include OHLCV data"),
    include_depth: bool = Query(default=True, description="Include market depth data"),
    include_floorsheet: bool = Query(default=True, description="Include floorsheet data"),
    ohlcv_days: int = Query(default=30, ge=1, le=365, description="Number of days of OHLCV data"),
    db: Session = Depends(get_db)
):
    """
    Fetch all data from NEPSE API (orchestrated operation)
    
    This endpoint performs a complete data fetch:
    1. Market indices and sectors
    2. Stock list
    3. OHLCV data (optional)
    4. Market depth (optional)
    5. Floorsheet (optional)
    
    Args:
        include_ohlcv: Whether to fetch OHLCV data
        include_depth: Whether to fetch market depth
        include_floorsheet: Whether to fetch floorsheet
        ohlcv_days: Number of days of OHLCV data to fetch
    
    Returns:
        Dictionary with comprehensive operation results
        
    Note: This operation may take several minutes to complete
    """
    try:
        logger.info("API: Starting full data fetch operation...")
        service = DataFetcherService(db)
        result = service.fetch_all_data(
            include_ohlcv=include_ohlcv,
            include_depth=include_depth,
            include_floorsheet=include_floorsheet,
            ohlcv_days=ohlcv_days
        )
        return result
    except Exception as e:
        logger.error(f"Error in fetch_all_data endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_data_status(db: Session = Depends(get_db)):
    """
    Get status of data in database

    Returns statistics about:
    - Number of sectors
    - Number of stocks
    - Latest OHLCV date
    - Database connection status

    Returns:
        Dictionary with data statistics
    """
    try:
        logger.info("API: Getting data status...")
        service = DataFetcherService(db)
        result = service.get_data_status()
        return result
    except Exception as e:
        logger.error(f"Error in get_data_status endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/proxy/test")
async def test_proxies(
    max_proxies: int = Query(default=10, ge=1, le=50, description="Max proxies to test"),
    target_url: str = Query(
        default="https://www.nepalstock.com.np/api/nots/nepse-data/market-open",
        description="URL to test proxies against",
    ),
):
    """
    Test proxy connectivity and configuration.

    Shows:
    - Current proxy configuration (PROXY_LIST, NEPAL_PROXY_LIST)
    - Whether PROXY_LIST_URL is fetching proxies
    - Tests up to max_proxies against the target URL
    - Free-source connectivity (merolagani, sharesansar, yonepse)

    Returns a comprehensive proxy + connectivity report.
    """
    import requests
    import urllib3
    urllib3.disable_warnings()

    report: Dict[str, Any] = {}

    # ── Config ────────────────────────────────────────────────────────────
    proxy_list_raw   = os.environ.get("PROXY_LIST", "").strip()
    nepal_list_raw   = os.environ.get("NEPAL_PROXY_LIST", "").strip()
    proxy_list_url   = os.environ.get("PROXY_LIST_URL", "").strip()
    refresh_interval = os.environ.get("PROXY_LIST_REFRESH_INTERVAL", "1800")

    report["config"] = {
        "PROXY_LIST":                  proxy_list_raw or "(not set — direct)",
        "NEPAL_PROXY_LIST":            nepal_list_raw or "(not set — falls back to PROXY_LIST)",
        "PROXY_LIST_URL":              proxy_list_url or "(not set)",
        "PROXY_LIST_REFRESH_INTERVAL": refresh_interval + "s",
    }

    # ── Fetch proxy pool from rotator ─────────────────────────────────────
    try:
        from app.services.data.free_sources.proxy_rotator import get_rotator, get_nepal_rotator
        rotator = get_rotator()
        nepal_rotator = get_nepal_rotator()
        report["proxy_pool"] = {
            "general_pool_size":  len(rotator._proxies),
            "nepal_pool_size":    len(nepal_rotator._proxies),
            "general_sample":     rotator._proxies[:5],
            "nepal_sample":       nepal_rotator._proxies[:5],
        }
        all_proxies: List[str] = rotator._proxies[:max_proxies]
    except Exception as exc:
        report["proxy_pool"] = {"error": str(exc)}
        all_proxies = []

    # ── Direct connectivity (no proxy) ────────────────────────────────────
    direct_results: Dict[str, Any] = {}
    test_urls = {
        "nepalstock_api": "https://www.nepalstock.com.np/api/nots/nepse-data/market-open",
        "merolagani":     "https://merolagani.com/LatestMarket.aspx",
        "sharesansar":    "https://www.sharesansar.com/today-share-price",
        "yonepse_github": "https://raw.githubusercontent.com/Shubhamnpk/yonepse/main/data/indices.json",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "application/json, text/html, */*",
    }
    for name, url in test_urls.items():
        try:
            t0 = time.time()
            r = requests.get(url, headers=headers, timeout=8, verify=False)
            elapsed = round((time.time() - t0) * 1000)
            direct_results[name] = {
                "status": r.status_code,
                "ok": r.status_code == 200,
                "ms": elapsed,
                "content_type": r.headers.get("content-type", "")[:40],
            }
        except Exception as exc:
            direct_results[name] = {"status": None, "ok": False, "error": str(exc)[:80]}
    report["direct_connectivity"] = direct_results

    # ── Proxy test ────────────────────────────────────────────────────────
    if all_proxies:
        proxy_results: List[Dict[str, Any]] = []
        for proxy_url in all_proxies:
            entry: Dict[str, Any] = {"proxy": proxy_url}
            try:
                proxies = {"http": proxy_url, "https": proxy_url}
                t0 = time.time()
                r = requests.get(
                    target_url,
                    proxies=proxies,
                    headers=headers,
                    timeout=6,
                    verify=False,
                )
                elapsed = round((time.time() - t0) * 1000)
                entry["status"]       = r.status_code
                entry["ok"]           = r.status_code == 200
                entry["ms"]           = elapsed
                entry["content_type"] = r.headers.get("content-type", "")[:40]
                if r.status_code == 200:
                    try:
                        body = r.json()
                        entry["is_json"] = True
                        entry["sample"]  = str(body)[:100]
                    except Exception:
                        entry["is_json"] = False
            except requests.exceptions.ConnectTimeout:
                entry["error"] = "ConnectTimeout"
            except requests.exceptions.ProxyError as exc:
                entry["error"] = f"ProxyError: {str(exc)[:60]}"
            except Exception as exc:
                entry["error"] = str(exc)[:60]
            proxy_results.append(entry)
        report["proxy_test"] = {
            "target_url":    target_url,
            "tested":        len(proxy_results),
            "working":       sum(1 for r in proxy_results if r.get("ok")),
            "results":       proxy_results,
        }
    else:
        report["proxy_test"] = {
            "skipped": True,
            "reason":  "No proxies loaded (configure PROXY_LIST or PROXY_LIST_URL in .env)",
        }

    # ── Free-source quick-check ───────────────────────────────────────────
    def _run(coro):
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result(timeout=20)

    from app.services.data.free_sources import aggregator as _agg
    free_status: Dict[str, Any] = {}
    sources = {
        "live_market":   lambda: _agg.live_market(),
        "indices":       lambda: _agg.indices(),
        "sector_indices": lambda: _agg.sector_indices(),
    }
    for name, fn in sources.items():
        try:
            t0 = time.time()
            data = _run(fn())
            elapsed = round((time.time() - t0) * 1000)
            free_status[name] = {
                "ok":      True,
                "count":   len(data) if data else 0,
                "ms":      elapsed,
            }
        except Exception as exc:
            free_status[name] = {"ok": False, "error": str(exc)[:80]}
    report["free_sources"] = free_status

    # ── Summary ───────────────────────────────────────────────────────────
    working_proxies = report.get("proxy_test", {}).get("working", 0)
    direct_ok       = sum(1 for v in direct_results.values() if v.get("ok"))
    report["summary"] = {
        "direct_sources_reachable": f"{direct_ok}/{len(direct_results)}",
        "proxies_tested":           len(all_proxies),
        "proxies_working":          working_proxies,
        "recommendation": (
            "nepalstock.com needs Nepal IP + JS session cookies. "
            "General HTTP proxies will NOT fix the 401. "
            "Use free sources (merolagani / yonepse) instead — they work without proxies."
            if not working_proxies else
            f"{working_proxies} proxy(ies) reached the target URL successfully."
        ),
    }

    return report
