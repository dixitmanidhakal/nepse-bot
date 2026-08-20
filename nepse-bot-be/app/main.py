"""
FastAPI Application Entry Point

This is the main application file that initializes FastAPI,
sets up middleware, includes routers, and defines core endpoints.

Architecture Benefits:
- Clean separation of concerns
- Easy to add new routes and middleware
- Centralized error handling
- Health check endpoints for monitoring
"""

from fastapi import FastAPI, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy.exc import OperationalError as SAOperationalError
from sqlalchemy.orm import Session
import logging
from contextlib import asynccontextmanager

from app.config import settings
from app.database import get_db, test_connection, get_db_info, init_db
from app.services.nepse_api_client import create_api_client

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),  # Console output
        # File handler will be added when logs directory is created
    ]
)

# SQLAlchemy logs every SQL query at INFO level using %(param)s substitution syntax
# which clashes with Python's logging % formatter → suppress to WARNING.
# Our app logs all meaningful events via its own loggers; raw SQL is not needed.
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.dialects").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager
    
    Handles startup and shutdown events for the application.
    This is the modern way to handle startup/shutdown in FastAPI.
    
    Startup:
        - Test database connection
        - Initialize database tables
        - Log application info
    
    Shutdown:
        - Close database connections
        - Cleanup resources
    """
    # Startup
    logger.info("=" * 60)
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
    logger.info(f"Environment: {settings.environment}")
    logger.info("=" * 60)
    
    # Test database connection
    logger.info("Testing database connection...")
    if test_connection():
        logger.info("✅ Database connection successful")
        
        # Initialize database tables (create_all is idempotent — safe in all envs)
        logger.info("Initializing database tables...")
        try:
            init_db()
            logger.info("✅ Database tables initialized")
        except Exception as e:
            logger.error(f"❌ Failed to initialize database: {e}")
    else:
        logger.error("❌ Database connection failed")
        logger.error("Please check your database configuration in .env file")
    
    # NOTE: nepalstock.com direct API is geo-restricted to Nepal (returns 401 from outside).
    # The application uses the free data cascade (merolagani → sharesansar → yonepse …)
    # which works globally, so we skip the NEPSE direct-API probe at startup.
    import asyncio

    # Start high-frequency market-depth poller (runs only during NEPSE hours).
    try:
        from app.services.data.depth_poller import get_depth_poller, POLL_ENABLED
        if POLL_ENABLED:
            logger.info("Starting depth poller...")
            await get_depth_poller().start()
            logger.info("✅ Depth poller started (gated by NEPSE market hours)")
        else:
            logger.info("Depth poller disabled (DEPTH_POLLER_ENABLED=false)")
    except Exception as e:
        logger.error(f"❌ Failed to start depth poller: {e}")

    # Start paper-trading bot scheduler (every 15 min during NEPSE hours).
    try:
        from app.services.bot_scheduler import start_bot_scheduler
        await start_bot_scheduler()
        logger.info("✅ Bot scheduler started")
    except Exception as e:
        logger.error(f"❌ Failed to start bot scheduler: {e}")

    logger.info("=" * 60)
    logger.info(f"Application started successfully!")
    logger.info(f"API Documentation: http://{settings.host}:{settings.port}/docs")
    logger.info("=" * 60)

    yield

    # Shutdown
    logger.info("Shutting down application...")
    try:
        from app.services.data.depth_poller import get_depth_poller
        await get_depth_poller().stop()
        logger.info("✅ Depth poller stopped")
    except Exception as e:
        logger.error(f"Depth poller shutdown error: {e}")

    try:
        from app.services.bot_scheduler import stop_bot_scheduler
        await stop_bot_scheduler()
        logger.info("✅ Bot scheduler stopped")
    except Exception as e:
        logger.error(f"Bot scheduler shutdown error: {e}")

    logger.info("Application shutdown complete")


# Create FastAPI application
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="""
    A configuration-driven NEPSE trading bot that generates buy signals 
    based on a comprehensive 4-component trading strategy.
    
    ## Features
    
    * **Sector Identification**: Analyze sectors and identify bullish trends
    * **Liquidity Hunt**: Detect demand zones and entry points
    * **Market Depth Analysis**: Monitor order book and detect institutional activity
    * **Floorsheet Analysis**: Track broker activity and detect manipulation
    * **Risk Management**: Calculate position sizing, stop-loss, and take-profit
    
    ## Strategy Components
    
    1. **Sector Identifier**: Daily/weekly chart comparison, stock screening
    2. **Liquidity Hunter**: Demand zones, volume patterns, candlestick patterns
    3. **Market Depth Analyzer**: Order book analysis, bid wall detection
    4. **Floorsheet Analyzer**: Broker tracking, manipulation detection
    5. **Risk Manager**: Position sizing, stop-loss, take-profit calculation
    """,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Root endpoint
@app.get("/", tags=["Root"])
async def root():
    """
    Root endpoint
    
    Returns basic information about the API.
    """
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "status": "running",
        "docs": "/docs",
        "health": "/health"
    }


# Health check endpoint
@app.get("/health", tags=["Health"])
async def health_check():
    """
    Health check endpoint
    
    Returns the health status of the application and its dependencies.
    This is useful for monitoring and load balancers.
    """
    # Check database connection
    db_healthy = test_connection()

    # Check free data API (merolagani / yonepse cascade) — fast, never geo-blocked
    import asyncio
    from app.services.data.free_sources import aggregator as _agg

    api_healthy = False
    try:
        rows = await asyncio.wait_for(_agg.live_market(), timeout=10.0)
        api_healthy = len(rows) > 0
    except asyncio.TimeoutError:
        logger.warning("Free API health check timed out")
    except Exception as e:
        logger.error("Free API health check failed: %s", e)
    
    # Overall health status
    healthy = db_healthy and api_healthy
    
    return {
        "status": "healthy" if healthy else "unhealthy",
        "version": settings.app_version,
        "environment": settings.environment,
        "checks": {
            "database": "healthy" if db_healthy else "unhealthy",
            "api": "healthy" if api_healthy else "unhealthy"
        }
    }


# Database info endpoint
@app.get("/db-info", tags=["Database"])
async def database_info():
    """
    Database information endpoint
    
    Returns information about the database connection.
    Useful for debugging and monitoring.
    """
    try:
        info = get_db_info()
        return {
            "status": "success",
            "data": info
        }
    except Exception as e:
        logger.error(f"Error getting database info: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": str(e)
            }
        )


# Test database connection endpoint
@app.get("/test-db", tags=["Database"])
async def test_db_connection():
    """
    Test database connection endpoint
    
    Tests the database connection and returns the result.
    """
    try:
        success = test_connection()
        if success:
            return {
                "status": "success",
                "message": "Database connection successful",
                "database": settings.db_name,
                "host": settings.db_host,
                "port": settings.db_port
            }
        else:
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": "Database connection failed"
                }
            )
    except Exception as e:
        logger.error(f"Database connection test failed: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": str(e)
            }
        )


# Test API connection endpoint
@app.get("/test-api", tags=["API"])
async def test_api_connection():
    """
    Test NEPSE API connection endpoint
    
    Tests the NEPSE API connection and returns the result.
    """
    try:
        api_client = create_api_client("nepse")
        
        # Test health check
        healthy = api_client.health_check()
        
        # Get API info
        info = {
            "base_url": api_client.get_base_url(),
            "timeout": api_client.get_timeout(),
            "retry_attempts": api_client.get_retry_attempts()
        }
        
        api_client.close()
        
        if healthy:
            return {
                "status": "success",
                "message": "API connection successful",
                "api_info": info
            }
        else:
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": "API health check failed",
                    "api_info": info
                }
            )
    except Exception as e:
        logger.error(f"API connection test failed: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": str(e)
            }
        )


# Configuration endpoint
@app.get("/config", tags=["Configuration"])
async def get_configuration():
    """
    Get application configuration
    
    Returns non-sensitive configuration information.
    """
    return {
        "app_name": settings.app_name,
        "app_version": settings.app_version,
        "environment": settings.environment,
        "debug": settings.debug,
        "api_base_url": settings.nepse_api_base_url,
        "scheduler_enabled": settings.enable_scheduler,
        "intervals": {
            "market_data_fetch": settings.market_data_fetch_interval,
            "sector_analysis": settings.sector_analysis_interval,
            "stock_screening": settings.stock_screening_interval,
            "pattern_detection": settings.pattern_detection_interval,
            "market_depth": settings.market_depth_interval,
            "floorsheet_analysis": settings.floorsheet_analysis_interval,
            "signal_generation": settings.signal_generation_interval
        }
    }


# Unified HTTPException handler — respects the `detail` message set by routes
# (e.g. "No market depth data found for NABIL") and only substitutes a generic
# "Endpoint not found" message when FastAPI itself raises 404 for an unmatched
# route (which has detail == "Not Found").
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request, exc: StarletteHTTPException):
    detail = exc.detail
    is_default_not_found = exc.status_code == 404 and detail == "Not Found"

    if is_default_not_found:
        payload = {
            "status": "error",
            "message": "Endpoint not found",
            "path": str(request.url),
        }
    elif isinstance(detail, dict):
        payload = {"status": "error", **detail}
    else:
        payload = {
            "status": "error",
            "message": str(detail) if detail is not None else "Request failed",
        }

    return JSONResponse(status_code=exc.status_code, content=payload)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "status": "error",
            "message": "Validation failed",
            "errors": exc.errors(),
        },
    )


# Error handler for 500
@app.exception_handler(500)
async def internal_error_handler(request, exc):
    """Handle 500 errors"""
    logger.error(f"Internal server error: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "message": "Internal server error"
        }
    )


# SQLAlchemy DB connection error → return 503 with friendly message
@app.exception_handler(SAOperationalError)
async def db_operational_error_handler(request, exc: SAOperationalError):
    logger.warning("Database unavailable: %s", exc)
    return JSONResponse(
        status_code=503,
        content={
            "status": "error",
            "code": "db_unavailable",
            "message": (
                "Database is not reachable. Paper-trading bots require PostgreSQL. "
                "Run `docker-compose up -d postgres` or start your local Postgres instance."
            ),
        },
    )


# Test NEPSE Live endpoint
@app.get("/test-nepse-live", tags=["API"])
async def test_nepse_live():
    """
    Test NEPSE Live Data endpoint

    Directly calls the unofficial NEPSE API at nepseapi.surajrimal.dev
    and returns raw live data. Useful for verifying API connectivity.

    Returns:
        - health: API health status
        - today_prices: Sample of today's stock prices (first 5 records)
        - indices: Current NEPSE index data
        - summary: Market summary
        - errors: Any errors encountered per call
    """
    import requests as req_lib
    from datetime import datetime

    BASE = "https://nepseapi.surajrimal.dev"
    TIMEOUT = 20

    results = {
        "timestamp": datetime.now().isoformat(),
        "api_base": BASE,
        "health": None,
        "today_prices": None,
        "indices": None,
        "summary": None,
        "errors": {}
    }

    session = req_lib.Session()
    session.headers.update({"Accept": "application/json"})

    def safe_get(endpoint, label):
        try:
            r = session.get(f"{BASE}{endpoint}", timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except req_lib.exceptions.Timeout:
            results["errors"][label] = f"Timeout after {TIMEOUT}s"
            logger.warning(f"/test-nepse-live: {label} timed out")
            return None
        except Exception as e:
            results["errors"][label] = str(e)
            logger.warning(f"/test-nepse-live: {label} error: {e}")
            return None

    # 1. Health check
    health_data = safe_get("/health", "health")
    if health_data:
        results["health"] = health_data
        logger.info(f"/test-nepse-live: health={health_data}")

    # 2. Market summary
    summary_data = safe_get("/Summary", "summary")
    if summary_data:
        results["summary"] = summary_data
        logger.info(f"/test-nepse-live: summary fetched")

    # 3. Today's prices (LiveMarket)
    prices_data = safe_get("/LiveMarket", "today_prices")
    if prices_data is not None:
        if isinstance(prices_data, list):
            results["today_prices"] = {
                "total_records": len(prices_data),
                "sample": prices_data[:5]
            }
        else:
            results["today_prices"] = prices_data
        logger.info(f"/test-nepse-live: LiveMarket returned data")

    # 4. NEPSE indices
    indices_data = safe_get("/NepseIndex", "indices")
    if indices_data is not None:
        if isinstance(indices_data, list):
            results["indices"] = {
                "total_records": len(indices_data),
                "sample": indices_data[:5]
            }
        else:
            results["indices"] = indices_data
        logger.info(f"/test-nepse-live: NepseIndex returned data")

    session.close()

    has_data = any(v is not None for k, v in results.items()
                   if k not in ("timestamp", "api_base", "errors"))
    has_errors = bool(results["errors"])

    return {
        "status": "success" if has_data and not has_errors else (
            "partial" if has_data else "no_data"
        ),
        "data": results
    }


# Data-persistence verification endpoint — confirms:
#   1. Bundled SQLite historical DB is readable on every cold start (size, rows, latest date).
#   2. Yonepse GitHub-Actions data is fresh (last_updated + commit timestamp).
#   3. SamirWagle scraper is fresh (latest commit timestamp).
@app.get("/__verify-data", tags=["Diagnostics"])
async def verify_data():
    import httpx
    import asyncio
    import sqlite3
    import os
    from pathlib import Path
    from datetime import datetime, timezone

    result: dict = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "sqlite": {},
        "yonepse": {},
        "samirwagle": {},
    }

    # 1. SQLite persistence check
    try:
        from app.services.data.quant_terminal_db import get_db_path
        db_path = get_db_path()
        if db_path is None:
            result["sqlite"] = {"ok": False, "error": "DB path not resolved"}
        else:
            size_mb = round(db_path.stat().st_size / (1024 * 1024), 2)
            uri = f"file:{db_path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=5.0)
            try:
                row_count = conn.execute("SELECT COUNT(*) FROM stock_prices").fetchone()[0]
                sym_count = conn.execute("SELECT COUNT(DISTINCT symbol) FROM stock_prices").fetchone()[0]
                min_d, max_d = conn.execute("SELECT MIN(date), MAX(date) FROM stock_prices").fetchone()
            finally:
                conn.close()
            result["sqlite"] = {
                "ok": True,
                "path": str(db_path),
                "size_mb": size_mb,
                "row_count": row_count,
                "symbol_count": sym_count,
                "date_range": [str(min_d), str(max_d)],
            }
    except Exception as e:
        result["sqlite"] = {"ok": False, "error": str(e)[:200]}

    # 2. Yonepse + SamirWagle freshness (GitHub commits API + direct JSON)
    headers = {"User-Agent": "nepse-bot/1.0"}

    async def _probe_yonepse(client: httpx.AsyncClient):
        # Latest commit time on main
        try:
            r = await client.get(
                "https://api.github.com/repos/Shubhamnpk/yonepse/commits?per_page=1",
                headers=headers,
            )
            if r.status_code == 200 and r.json():
                commit = r.json()[0]["commit"]
                result["yonepse"]["last_commit_utc"] = commit["committer"]["date"]
                result["yonepse"]["last_commit_message"] = commit["message"][:120]
        except Exception as e:
            result["yonepse"]["commit_error"] = str(e)[:120]

        # Actual data freshness + row counts
        try:
            r = await client.get(
                "https://raw.githubusercontent.com/Shubhamnpk/yonepse/main/data/nepse_data.json",
                headers=headers,
            )
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict) and "data" in data:
                    rows = data["data"]
                    result["yonepse"]["nepse_data_last_updated"] = data.get("last_updated")
                    result["yonepse"]["nepse_data_symbol_count"] = len(rows) if isinstance(rows, list) else None
                elif isinstance(data, list):
                    result["yonepse"]["nepse_data_symbol_count"] = len(data)
                    result["yonepse"]["nepse_data_last_updated"] = None
                result["yonepse"]["nepse_data_ok"] = True
            else:
                result["yonepse"]["nepse_data_ok"] = False
                result["yonepse"]["nepse_data_status"] = r.status_code
        except Exception as e:
            result["yonepse"]["nepse_data_error"] = str(e)[:120]

        try:
            r = await client.get(
                "https://raw.githubusercontent.com/Shubhamnpk/yonepse/main/data/market_status.json",
                headers=headers,
            )
            if r.status_code == 200:
                result["yonepse"]["market_status"] = r.json()
        except Exception as e:
            result["yonepse"]["market_status_error"] = str(e)[:120]

    async def _probe_samirwagle(client: httpx.AsyncClient):
        try:
            r = await client.get(
                "https://api.github.com/repos/SamirWagle/Nepse-All-Scraper/commits?per_page=1",
                headers=headers,
            )
            if r.status_code == 200 and r.json():
                commit = r.json()[0]["commit"]
                result["samirwagle"]["last_commit_utc"] = commit["committer"]["date"]
                result["samirwagle"]["last_commit_message"] = commit["message"][:120]
        except Exception as e:
            result["samirwagle"]["commit_error"] = str(e)[:120]

        try:
            r = await client.get(
                "https://raw.githubusercontent.com/SamirWagle/Nepse-All-Scraper/main/data/company_list.json",
                headers=headers,
            )
            if r.status_code == 200:
                body = r.json()
                result["samirwagle"]["company_list_count"] = (
                    len(body) if isinstance(body, list) else None
                )
                result["samirwagle"]["company_list_ok"] = True
        except Exception as e:
            result["samirwagle"]["company_list_error"] = str(e)[:120]

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        await asyncio.gather(_probe_yonepse(client), _probe_samirwagle(client))

    # Derive staleness flags
    def _minutes_since(iso_str: str) -> float | None:
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            return round((datetime.now(timezone.utc) - dt).total_seconds() / 60.0, 1)
        except Exception:
            return None

    if "last_commit_utc" in result["yonepse"]:
        result["yonepse"]["commit_age_minutes"] = _minutes_since(
            result["yonepse"]["last_commit_utc"]
        )
    if "nepse_data_last_updated" in result["yonepse"] and result["yonepse"]["nepse_data_last_updated"]:
        result["yonepse"]["data_age_minutes"] = _minutes_since(
            result["yonepse"]["nepse_data_last_updated"]
        )
    if "last_commit_utc" in result["samirwagle"]:
        result["samirwagle"]["commit_age_minutes"] = _minutes_since(
            result["samirwagle"]["last_commit_utc"]
        )

    # Overall health summary
    result["summary"] = {
        "sqlite_persistent": result["sqlite"].get("ok", False),
        "yonepse_reachable": result["yonepse"].get("nepse_data_ok", False),
        "samirwagle_reachable": result["samirwagle"].get("company_list_ok", False),
        "yonepse_fresh": (
            (result["yonepse"].get("commit_age_minutes") or 99999) < 120
        ),
    }
    return result


# Diagnostic endpoint to probe which NEPSE data origins are reachable from
# this runtime (useful to diagnose Vercel geo-block issues).
@app.get("/__diagnose-upstream", tags=["Diagnostics"])
async def diagnose_upstream():
    import httpx
    import asyncio
    import time
    targets = [
        # Direct origins (most are geo-blocked)
        ("nepalstock_api_summary","https://www.nepalstock.com.np/api/nots/market-open"),
        ("merolagani_company",   "https://merolagani.com/CompanyDetail.aspx?symbol=NABIL"),
        ("sharesansar_today",    "https://www.sharesansar.com/today-share-price"),
        ("nepalipaisa_home",     "https://nepalipaisa.com/"),
        # GitHub-Actions scraped datasets (free, reachable from Vercel)
        ("gh_yonepse_nepse",     "https://raw.githubusercontent.com/Shubhamnpk/yonepse/main/data/nepse_data.json"),
        ("gh_yonepse_indices",   "https://raw.githubusercontent.com/Shubhamnpk/yonepse/main/data/indices.json"),
        ("gh_yonepse_top",       "https://raw.githubusercontent.com/Shubhamnpk/yonepse/main/data/top_stocks.json"),
        ("gh_yonepse_market",    "https://raw.githubusercontent.com/Shubhamnpk/yonepse/main/data/market_summary.json"),
        ("gh_yonepse_all_sec",   "https://raw.githubusercontent.com/Shubhamnpk/yonepse/main/data/all_securities.json"),
        ("gh_yonepse_status",    "https://raw.githubusercontent.com/Shubhamnpk/yonepse/main/data/market_status.json"),
        ("gh_samirwagle_list",   "https://raw.githubusercontent.com/SamirWagle/Nepse-All-Scraper/main/data/company_list.json"),
        ("gh_samirwagle_adbl",   "https://raw.githubusercontent.com/SamirWagle/Nepse-All-Scraper/main/data/company-wise/ADBL/prices.csv"),
        # Community-hosted NEPSE proxies (testing for depth availability)
        ("prabesh_info",         "https://nepalstock-kj9g.onrender.com/info"),
        ("prabesh_market_depth", "https://nepalstock-kj9g.onrender.com/api/nots/nepse-data/marketdepth/131"),
        ("prabesh_summary",      "https://nepalstock-kj9g.onrender.com/api/nots/market/summary"),
        ("prabesh_today_price",  "https://nepalstock-kj9g.onrender.com/api/nots/today-price"),
        # CORS-proxy passthroughs (geography often preserved)
        ("corsproxy_nepalstock", "https://corsproxy.io/?https://www.nepalstock.com.np/api/nots/market-open"),
        ("codetabs_nepalstock",  "https://api.codetabs.com/v1/proxy?quest=https://www.nepalstock.com.np/api/nots/market-open"),
        # Alternative scraped data sources with depth-like info
        ("gh_yonepse_supply_demand", "https://raw.githubusercontent.com/Shubhamnpk/yonepse/main/data/supply_demand.json"),
    ]
    results = {}
    headers = {"User-Agent": "Mozilla/5.0 (compatible; nepse-bot/1.0)"}
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=headers) as client:
        async def probe(label, url):
            t0 = time.perf_counter()
            try:
                r = await client.get(url)
                ms = round((time.perf_counter() - t0) * 1000, 1)
                results[label] = {
                    "url": url,
                    "status": r.status_code,
                    "ok": 200 <= r.status_code < 300,
                    "ms": ms,
                    "bytes": len(r.content),
                    "content_type": r.headers.get("content-type", ""),
                }
            except Exception as e:
                ms = round((time.perf_counter() - t0) * 1000, 1)
                results[label] = {"url": url, "status": None, "ok": False, "ms": ms, "error": str(e)[:200]}
        await asyncio.gather(*[probe(l, u) for l, u in targets])
    return {"results": results}


# Include API routers
from app.api.v1 import router as api_v1_router
app.include_router(api_v1_router, prefix="/api/v1")


if __name__ == "__main__":
    import uvicorn
    
    # Run the application
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level=settings.log_level.lower()
    )
