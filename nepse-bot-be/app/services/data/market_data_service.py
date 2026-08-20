"""
Market Data Service

This service handles fetching and storing market indices and sector data.
It bridges the API client and database models.

Data source priority (highest → lowest):
  1. Free aggregator  : yonepse / nepsealpha / merolagani (no geo-block)
  2. NepalStockScraper: nepalstock.com.np (geo-restricted; 401 outside Nepal
                        and requires JS session cookies even inside Nepal)
"""

import asyncio
import concurrent.futures
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.services.nepse_api_client import create_api_client
from app.services.data.free_sources import aggregator as free_aggregator
from app.models.sector import Sector
from app.validators.market_validators import SectorDataSchema, MarketDataResponse

logger = logging.getLogger(__name__)


def _run_async(coro, timeout: int = 30) -> Any:
    """
    Execute an async coroutine from synchronous code.
    Creates a brand-new event loop in a worker thread so it never conflicts
    with the FastAPI / uvicorn event loop that is already running.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result(timeout=timeout)


class MarketDataService:
    """
    Service for fetching and storing market data
    
    This service:
    1. Fetches market indices from NEPSE API
    2. Validates the data
    3. Stores/updates in database
    4. Returns operation results
    """
    
    def __init__(self, db: Session):
        """
        Initialize market data service
        
        Args:
            db: Database session
        """
        self.db = db
        self.api_client = create_api_client("nepse")
        logger.info("MarketDataService initialized")
    
    # ------------------------------------------------------------------ #
    # Free-aggregator helpers (no geo-block)                             #
    # ------------------------------------------------------------------ #

    def _fetch_via_free_aggregator(self) -> Optional[Dict[str, Any]]:
        """
        Fetch market indices using the geo-unrestricted free sources
        (yonepse GitHub Actions JSON, nepsealpha, merolagani, sharesansar).

        Returns a dict compatible with NepalStockScraper.fetch_market_indices():
            {
                "nepse_index": float,
                "timestamp"  : str (ISO),
                "sub_indices": { code: {"name": str, "index": float,
                                        "current_index": float,
                                        "change": float,
                                        "change_percent": float} }
            }
        Returns None on failure so the caller can fall back to the scraper.
        """
        try:
            logger.info("Fetching market indices via free aggregator…")
            indices_data = _run_async(free_aggregator.indices(), timeout=20)

            # ── NEPSE main index ──────────────────────────────────────── #
            nepse_val = 0.0
            for row in indices_data or []:
                name = str(row.get("index", "")).upper()
                # "NEPSE Index" but NOT "Sensitive" or "Float"
                if "NEPSE" in name and "SENSITIVE" not in name and "FLOAT" not in name:
                    nepse_val = float(
                        row.get("currentValue") or row.get("close") or 0
                    )
                    break

            logger.info(f"Free aggregator → NEPSE Index: {nepse_val}")

            # ── Sector sub-indices ────────────────────────────────────── #
            # yonepse sector_indices.json is metadata-only (no live values).
            # We call nepsealpha directly for live sector index values; if that
            # also returns no values, we still succeed with NEPSE index only.
            sub_indices: Dict[str, Any] = {}

            def _parse_sector_rows(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
                result: Dict[str, Any] = {}
                for row in rows or []:
                    code = (
                        row.get("indexCode")
                        or row.get("code")
                        or row.get("index")
                        or ""
                    )
                    name = (
                        row.get("indexName")
                        or row.get("name")
                        or row.get("description")
                        or code
                    )
                    val = float(
                        row.get("currentValue")
                        or row.get("close")
                        or row.get("value")
                        or 0
                    )
                    chg = float(row.get("change") or 0)
                    chg_pct = float(
                        row.get("perChange") or row.get("change_percent") or 0
                    )
                    # Only include rows with a real (positive) index value —
                    # SectorDataSchema requires current_index > 0.
                    if code and val > 0:
                        result[str(code)] = {
                            "name":           str(name),
                            "index":          val,
                            "current_index":  val,
                            "change":         chg,
                            "change_percent": chg_pct,
                        }
                return result

            # 1st try: nepsealpha (has live values for all sectors)
            try:
                from app.services.data.free_sources import nepsealpha as _nepsealpha
                na_rows = _run_async(_nepsealpha.get_indices(), timeout=15)
                sub_indices = _parse_sector_rows(na_rows)
                if sub_indices:
                    logger.info(
                        f"Free aggregator → {len(sub_indices)} sector indices "
                        f"from nepsealpha"
                    )
            except Exception as na_exc:
                logger.debug(f"nepsealpha sector fetch failed: {na_exc}")

            # 2nd try: aggregator cascade (yonepse metadata only — values=0, skipped)
            if not sub_indices:
                try:
                    sector_data = _run_async(
                        free_aggregator.sector_indices(), timeout=15
                    )
                    sub_indices = _parse_sector_rows(sector_data)
                except Exception as sec_exc:
                    logger.debug(f"sector_indices cascade failed: {sec_exc}")

            logger.info(
                f"Free aggregator → {len(sub_indices)} sector indices with live values"
            )

            return {
                "nepse_index": nepse_val,
                "timestamp":   datetime.now().isoformat(),
                "sub_indices": sub_indices,
                "source":      "free_aggregator",
            }

        except Exception as exc:
            logger.warning(f"Free aggregator fetch failed: {exc}")
            return None

    # ------------------------------------------------------------------ #
    # Primary public method                                               #
    # ------------------------------------------------------------------ #

    def fetch_and_store_market_indices(self) -> MarketDataResponse:
        """
        Fetch market indices and store in database.

        Data source cascade:
          1. Free aggregator (yonepse / nepsealpha — geo-unrestricted)
          2. NepalStockScraper (nepalstock.com.np — may return 401)

        Returns:
            MarketDataResponse with operation results
        """
        # ── 1. Try the geo-unrestricted free aggregator first ─────────── #
        market_data = self._fetch_via_free_aggregator()

        # ── 2. Fall back to NepalStockScraper if aggregator failed ─────── #
        if market_data is None:
            try:
                logger.info(
                    "Free aggregator unavailable — falling back to "
                    "nepalstock.com scraper…"
                )
                market_data = self.api_client.fetch_market_indices()
            except Exception as exc:
                logger.error(f"NepalStockScraper also failed: {exc}")
                market_data = {"error": str(exc)}
            finally:
                self.api_client.close()

        # ── Handle error from scraper fallback ────────────────────────── #
        if market_data is None or "error" in market_data:
            err_msg = (market_data or {}).get("error", "All data sources failed")
            logger.error(f"Error fetching market indices: {err_msg}")
            return MarketDataResponse(
                status="error",
                message=f"Failed to fetch market indices: {err_msg}",
                nepse_index=None,
                sectors_updated=0,
                errors=[err_msg],
            )

        try:
            # Extract NEPSE index
            nepse_index = market_data.get("nepse_index", 0)
            source = market_data.get("source", "nepalstock_scraper")
            logger.info(f"NEPSE Index: {nepse_index} (source: {source})")

            # Process sub-indices (sectors)
            sub_indices = market_data.get("sub_indices", {})
            sectors_updated = 0
            errors: List[str] = []

            if sub_indices:
                sectors_updated, errors = self._process_sectors(sub_indices)

            # Commit changes
            self.db.commit()

            logger.info(
                f"Market data fetched successfully via {source}. "
                f"Sectors updated: {sectors_updated}"
            )

            return MarketDataResponse(
                status="success" if not errors else "partial_success",
                message=(
                    f"Market data fetched via {source}. "
                    f"{sectors_updated} sectors updated."
                ),
                nepse_index=nepse_index,
                sectors_updated=sectors_updated,
                errors=errors,
            )

        except Exception as exc:
            logger.error(f"Error in fetch_and_store_market_indices: {exc}")
            self.db.rollback()
            return MarketDataResponse(
                status="error",
                message=f"Failed to store market data: {str(exc)}",
                nepse_index=None,
                sectors_updated=0,
                errors=[str(exc)],
            )
    
    def _process_sectors(self, sub_indices: Dict[str, Any]) -> tuple[int, List[str]]:
        """
        Process and store sector data
        
        Args:
            sub_indices: Dictionary of sector data from API
            
        Returns:
            Tuple of (sectors_updated, errors)
        """
        sectors_updated = 0
        errors = []
        
        for sector_symbol, sector_data in sub_indices.items():
            try:
                # Validate sector data
                validated_data = self._validate_sector_data(sector_symbol, sector_data)
                
                if validated_data:
                    # Store or update sector
                    self._store_sector(validated_data)
                    sectors_updated += 1
                    
            except Exception as e:
                error_msg = f"Error processing sector {sector_symbol}: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)
        
        return sectors_updated, errors
    
    def _validate_sector_data(self, symbol: str, data: Dict[str, Any]) -> Optional[SectorDataSchema]:
        """
        Validate sector data using Pydantic schema
        
        Args:
            symbol: Sector symbol
            data: Raw sector data from API
            
        Returns:
            Validated SectorDataSchema or None if validation fails
        """
        try:
            # Map API data to schema
            sector_data = {
                "name": data.get("name", symbol),
                "symbol": symbol,
                "current_index": data.get("index", data.get("current_index", 0)),
                "change": data.get("change"),
                "change_percent": data.get("change_percent", data.get("changePercent")),
                "momentum_1d": data.get("momentum_1d"),
                "momentum_5d": data.get("momentum_5d"),
                "momentum_10d": data.get("momentum_10d"),
                "momentum_20d": data.get("momentum_20d"),
                "momentum_30d": data.get("momentum_30d"),
                "total_volume": data.get("total_volume", data.get("volume")),
                "total_turnover": data.get("total_turnover", data.get("turnover")),
                "avg_volume_10d": data.get("avg_volume_10d"),
                "total_stocks": data.get("total_stocks"),
                "advancing_stocks": data.get("advancing_stocks"),
                "declining_stocks": data.get("declining_stocks"),
                "unchanged_stocks": data.get("unchanged_stocks"),
                "relative_strength_nepse": data.get("relative_strength_nepse"),
                "sector_rank": data.get("sector_rank")
            }
            
            # Validate using Pydantic
            validated = SectorDataSchema(**sector_data)
            return validated
            
        except Exception as e:
            logger.error(f"Validation error for sector {symbol}: {e}")
            return None
    
    def _store_sector(self, sector_data: SectorDataSchema) -> None:
        """
        Store or update sector in database
        
        Args:
            sector_data: Validated sector data
        """
        try:
            # Check if sector exists
            sector = self.db.query(Sector).filter_by(symbol=sector_data.symbol).first()
            
            if sector:
                # Update existing sector
                sector.name = sector_data.name
                sector.current_index = sector_data.current_index
                sector.change = sector_data.change
                sector.change_percent = sector_data.change_percent
                sector.momentum_1d = sector_data.momentum_1d
                sector.momentum_5d = sector_data.momentum_5d
                sector.momentum_10d = sector_data.momentum_10d
                sector.momentum_20d = sector_data.momentum_20d
                sector.momentum_30d = sector_data.momentum_30d
                sector.total_volume = sector_data.total_volume
                sector.total_turnover = sector_data.total_turnover
                sector.avg_volume_10d = sector_data.avg_volume_10d
                sector.total_stocks = sector_data.total_stocks
                sector.advancing_stocks = sector_data.advancing_stocks
                sector.declining_stocks = sector_data.declining_stocks
                sector.unchanged_stocks = sector_data.unchanged_stocks
                sector.relative_strength_nepse = sector_data.relative_strength_nepse
                sector.sector_rank = sector_data.sector_rank
                sector.last_updated = datetime.now()
                
                logger.debug(f"Updated sector: {sector_data.symbol}")
            else:
                # Create new sector
                sector = Sector(
                    name=sector_data.name,
                    symbol=sector_data.symbol,
                    current_index=sector_data.current_index,
                    change=sector_data.change,
                    change_percent=sector_data.change_percent,
                    momentum_1d=sector_data.momentum_1d,
                    momentum_5d=sector_data.momentum_5d,
                    momentum_10d=sector_data.momentum_10d,
                    momentum_20d=sector_data.momentum_20d,
                    momentum_30d=sector_data.momentum_30d,
                    total_volume=sector_data.total_volume,
                    total_turnover=sector_data.total_turnover,
                    avg_volume_10d=sector_data.avg_volume_10d,
                    total_stocks=sector_data.total_stocks,
                    advancing_stocks=sector_data.advancing_stocks,
                    declining_stocks=sector_data.declining_stocks,
                    unchanged_stocks=sector_data.unchanged_stocks,
                    relative_strength_nepse=sector_data.relative_strength_nepse,
                    sector_rank=sector_data.sector_rank
                )
                self.db.add(sector)
                
                logger.debug(f"Created new sector: {sector_data.symbol}")
                
        except SQLAlchemyError as e:
            logger.error(f"Database error storing sector {sector_data.symbol}: {e}")
            raise
    
    def get_all_sectors(self) -> List[Sector]:
        """
        Get all sectors from database
        
        Returns:
            List of Sector objects
        """
        try:
            sectors = self.db.query(Sector).order_by(Sector.rank).all()
            logger.info(f"Retrieved {len(sectors)} sectors from database")
            return sectors
        except SQLAlchemyError as e:
            logger.error(f"Error retrieving sectors: {e}")
            return []
    
    def get_sector_by_symbol(self, symbol: str) -> Optional[Sector]:
        """
        Get sector by symbol
        
        Args:
            symbol: Sector symbol
            
        Returns:
            Sector object or None
        """
        try:
            sector = self.db.query(Sector).filter_by(symbol=symbol.upper()).first()
            if sector:
                logger.debug(f"Retrieved sector: {symbol}")
            else:
                logger.warning(f"Sector not found: {symbol}")
            return sector
        except SQLAlchemyError as e:
            logger.error(f"Error retrieving sector {symbol}: {e}")
            return None
    
    def get_top_sectors(self, limit: int = 5) -> List[Sector]:
        """
        Get top performing sectors
        
        Args:
            limit: Number of sectors to return
            
        Returns:
            List of top Sector objects
        """
        try:
            sectors = (
                self.db.query(Sector)
                .filter(Sector.change_percent.isnot(None))
                .order_by(Sector.change_percent.desc())
                .limit(limit)
                .all()
            )
            logger.info(f"Retrieved top {len(sectors)} sectors")
            return sectors
        except SQLAlchemyError as e:
            logger.error(f"Error retrieving top sectors: {e}")
            return []
