"""
OHLCV Service

This service handles fetching and storing historical OHLCV data for stocks.

Data source priority:
  1. Free aggregator (samirwagle CSV → nepsealpha) — geo-unrestricted
  2. NepalStockScraper (nepalstock.com.np) — geo-restricted
  3. Quant-terminal SQLite DB (offline backfill)
"""

import asyncio
import concurrent.futures
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, date, timedelta
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.services.nepse_api_client import create_api_client
from app.services.data import quant_terminal_db
from app.services.data.free_sources import aggregator as free_aggregator
from app.models.stock import Stock
from app.models.stock_ohlcv import StockOHLCV
from app.validators.stock_validators import OHLCVDataSchema, OHLCVResponse

logger = logging.getLogger(__name__)


def _run_async(coro, timeout: int = 30) -> Any:
    """Run async coroutine from sync code without conflicting with FastAPI event loop."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result(timeout=timeout)


class OHLCVService:
    """
    Service for fetching and storing OHLCV data
    
    This service:
    1. Fetches historical OHLCV data from NEPSE API
    2. Validates the data
    3. Stores/updates in database
    4. Handles date ranges
    """
    
    def __init__(self, db: Session):
        """
        Initialize OHLCV service
        
        Args:
            db: Database session
        """
        self.db = db
        self.api_client = create_api_client("nepse")
        logger.info("OHLCVService initialized")
    
    # ------------------------------------------------------------------ #
    # Free-aggregator helpers                                            #
    # ------------------------------------------------------------------ #

    def _fetch_via_free_aggregator(
        self,
        symbol: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch OHLCV history from samirwagle CSV (geo-unrestricted).

        aggregator.symbol_prices() returns newest-first rows from:
          samirwagle GitHub CSV (daily, full history) → nepsealpha fallback.

        Each row already has: date, open, high, low, close/ltp, qty/volume,
        turnover, percent_change.
        """
        try:
            logger.info(f"Fetching OHLCV for {symbol} via free aggregator…")
            rows = _run_async(free_aggregator.symbol_prices(symbol), timeout=25)

            if not rows:
                logger.debug(f"Free aggregator returned no OHLCV for {symbol}")
                return []

            # Convert to the format _validate_ohlcv_data expects
            start_str = start_date.date().isoformat() if start_date else None
            end_str   = end_date.date().isoformat()   if end_date   else None

            filtered: List[Dict[str, Any]] = []
            for row in rows:
                d = str(row.get("date") or "")
                if not d:
                    continue
                if start_str and d < start_str:
                    continue
                if end_str   and d > end_str:
                    continue
                ltp = row.get("ltp") or row.get("close") or 0
                filtered.append({
                    "date":     d,
                    "open":     row.get("open") or ltp,
                    "high":     row.get("high") or ltp,
                    "low":      row.get("low")  or ltp,
                    "close":    ltp,
                    "volume":   row.get("qty")  or row.get("volume") or 0,
                    "turnover": row.get("turnover") or 0,
                    "total_trades": row.get("trades") or row.get("total_trades"),
                })

            logger.info(
                f"Free aggregator → {len(filtered)} OHLCV rows for {symbol}"
            )
            return filtered

        except Exception as exc:
            logger.warning(f"Free aggregator OHLCV fetch failed for {symbol}: {exc}")
            return []

    # ------------------------------------------------------------------ #

    def fetch_and_store_ohlcv(
        self,
        symbol: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        days: int = 30
    ) -> OHLCVResponse:
        """
        Fetch OHLCV data and store in database.

        Data source cascade:
          1. Free aggregator — samirwagle CSV (geo-unrestricted, full history)
          2. NepalStockScraper — nepalstock.com (may return 401)
          3. Quant-terminal SQLite DB — offline backfill

        Args:
            symbol: Stock symbol
            start_date: Start date for historical data
            end_date: End date for historical data
            days: Number of days to fetch if dates not provided

        Returns:
            OHLCVResponse with operation results
        """
        try:
            logger.info(f"Fetching OHLCV data for {symbol}...")

            # Set default date range if not provided
            if not end_date:
                end_date = datetime.now()
            if not start_date:
                start_date = end_date - timedelta(days=days)

            logger.info(f"Fetching OHLCV from {start_date.date()} to {end_date.date()}")

            # ── 1. Free aggregator (samirwagle CSV) ───────────────────── #
            ohlcv_data = self._fetch_via_free_aggregator(
                symbol, start_date, end_date
            )

            # ── 2. NepalStockScraper fallback ─────────────────────────── #
            if not ohlcv_data:
                try:
                    ohlcv_data = self.api_client.fetch_stock_ohlcv(
                        symbol=symbol,
                        start_date=start_date,
                        end_date=end_date,
                    )
                except Exception as exc:
                    logger.warning(f"NepalStockScraper OHLCV failed for {symbol}: {exc}")
                    ohlcv_data = []

            # ── 3. Quant-terminal SQLite DB ────────────────────────────── #
            if not ohlcv_data and quant_terminal_db.is_available():
                logger.info(
                    f"All live sources empty for {symbol}; "
                    "falling back to quant-terminal SQLite DB"
                )
                ohlcv_data = quant_terminal_db.fetch_ohlcv(
                    symbol=symbol,
                    start_date=start_date,
                    end_date=end_date,
                )

            # Ensure stock exists in DB — create a stub entry if it doesn't
            stock = self.db.query(Stock).filter_by(symbol=symbol.upper()).first()
            if not stock:
                if ohlcv_data:
                    # Auto-create a minimal stub so we can store OHLCV
                    logger.info(f"Auto-creating stub stock entry for {symbol}")
                    stock = Stock(symbol=symbol.upper(), name=symbol.upper())
                    self.db.add(stock)
                    self.db.flush()
                else:
                    logger.error(f"Stock not found and no data available: {symbol}")
                    return OHLCVResponse(
                        status="error",
                        message=f"Stock not found: {symbol}",
                        symbol=symbol,
                        records_added=0,
                        records_updated=0,
                        errors=[f"Stock not found: {symbol}"],
                    )

            if not ohlcv_data:
                logger.warning(f"No OHLCV data received for {symbol}")
                return OHLCVResponse(
                    status="warning",
                    message=f"No OHLCV data received for {symbol}",
                    symbol=symbol,
                    records_added=0,
                    records_updated=0,
                    errors=["No data received"]
                )
            
            logger.info(f"Received {len(ohlcv_data)} OHLCV records for {symbol}")
            
            # Process OHLCV data
            records_added = 0
            records_updated = 0
            errors = []
            
            for ohlcv_record in ohlcv_data:
                try:
                    # Validate OHLCV data
                    validated_data = self._validate_ohlcv_data(symbol, ohlcv_record)
                    
                    if validated_data:
                        # Store or update OHLCV
                        is_new = self._store_ohlcv(stock.id, validated_data)
                        if is_new:
                            records_added += 1
                        else:
                            records_updated += 1
                            
                except Exception as e:
                    error_msg = f"Error processing OHLCV record: {str(e)}"
                    logger.error(error_msg)
                    errors.append(error_msg)
            
            # Commit changes
            self.db.commit()
            
            logger.info(f"OHLCV data processed for {symbol}. Added: {records_added}, Updated: {records_updated}")
            
            return OHLCVResponse(
                status="success" if not errors else "partial_success",
                message=f"OHLCV data fetched for {symbol}",
                symbol=symbol,
                records_added=records_added,
                records_updated=records_updated,
                date_range={
                    "start": start_date.date().isoformat(),
                    "end": end_date.date().isoformat()
                },
                errors=errors
            )
            
        except Exception as e:
            logger.error(f"Error in fetch_and_store_ohlcv: {e}")
            self.db.rollback()
            return OHLCVResponse(
                status="error",
                message=f"Failed to fetch OHLCV data: {str(e)}",
                symbol=symbol,
                records_added=0,
                records_updated=0,
                errors=[str(e)]
            )
        finally:
            self.api_client.close()
    
    def _validate_ohlcv_data(self, symbol: str, data: Dict[str, Any]) -> Optional[OHLCVDataSchema]:
        """
        Validate OHLCV data using Pydantic schema
        
        Args:
            symbol: Stock symbol
            data: Raw OHLCV data from API
            
        Returns:
            Validated OHLCVDataSchema or None if validation fails
        """
        try:
            # Parse date
            date_str = data.get("date", data.get("businessDate"))
            if isinstance(date_str, str):
                trade_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            elif isinstance(date_str, date):
                trade_date = date_str
            else:
                logger.error(f"Invalid date format: {date_str}")
                return None
            
            # Map API data to schema
            ohlcv_data = {
                "symbol": symbol,
                "date": trade_date,
                "open": data.get("open", data.get("openPrice", 0)),
                "high": data.get("high", data.get("highPrice", 0)),
                "low": data.get("low", data.get("lowPrice", 0)),
                "close": data.get("close", data.get("closePrice", 0)),
                "volume": data.get("volume", data.get("totalTradeQuantity", 0)),
                "turnover": data.get("turnover", data.get("totalTurnover")),
                "total_trades": data.get("total_trades", data.get("totalTrades")),
                "adjusted_open": data.get("adjusted_open"),
                "adjusted_high": data.get("adjusted_high"),
                "adjusted_low": data.get("adjusted_low"),
                "adjusted_close": data.get("adjusted_close")
            }
            
            # Validate using Pydantic
            validated = OHLCVDataSchema(**ohlcv_data)
            return validated
            
        except Exception as e:
            logger.error(f"Validation error for OHLCV data: {e}")
            return None
    
    def _store_ohlcv(self, stock_id: int, ohlcv_data: OHLCVDataSchema) -> bool:
        """
        Store or update OHLCV in database
        
        Args:
            stock_id: Stock ID
            ohlcv_data: Validated OHLCV data
            
        Returns:
            True if new record created, False if updated
        """
        try:
            # Check if OHLCV record exists
            ohlcv = (
                self.db.query(StockOHLCV)
                .filter_by(stock_id=stock_id, date=ohlcv_data.trade_date)
                .first()
            )
            
            if ohlcv:
                # Update existing record
                ohlcv.open = ohlcv_data.open_price
                ohlcv.high = ohlcv_data.high_price
                ohlcv.low = ohlcv_data.low_price
                ohlcv.close = ohlcv_data.close_price
                ohlcv.volume = ohlcv_data.volume
                ohlcv.turnover = ohlcv_data.turnover
                ohlcv.total_trades = ohlcv_data.total_trades
                ohlcv.adjusted_close = ohlcv_data.adjusted_close
                
                # Calculate candle metrics
                ohlcv.body_size = abs(ohlcv_data.close_price - ohlcv_data.open_price)
                ohlcv.upper_shadow = ohlcv_data.high_price - max(ohlcv_data.open_price, ohlcv_data.close_price)
                ohlcv.lower_shadow = min(ohlcv_data.open_price, ohlcv_data.close_price) - ohlcv_data.low_price
                ohlcv.is_bullish = ohlcv_data.close_price > ohlcv_data.open_price
                ohlcv.is_bearish = ohlcv_data.close_price < ohlcv_data.open_price
                ohlcv.is_doji = abs(ohlcv_data.close_price - ohlcv_data.open_price) < (ohlcv_data.high_price - ohlcv_data.low_price) * 0.1
                
                logger.debug(f"Updated OHLCV for {ohlcv_data.symbol} on {ohlcv_data.trade_date}")
                return False
            else:
                # Create new record
                ohlcv = StockOHLCV(
                    stock_id=stock_id,
                    date=ohlcv_data.trade_date,
                    open=ohlcv_data.open_price,
                    high=ohlcv_data.high_price,
                    low=ohlcv_data.low_price,
                    close=ohlcv_data.close_price,
                    volume=ohlcv_data.volume,
                    turnover=ohlcv_data.turnover,
                    total_trades=ohlcv_data.total_trades,
                    adjusted_close=ohlcv_data.adjusted_close
                )
                
                # Calculate candle metrics
                ohlcv.body_size = abs(ohlcv_data.close_price - ohlcv_data.open_price)
                ohlcv.upper_shadow = ohlcv_data.high_price - max(ohlcv_data.open_price, ohlcv_data.close_price)
                ohlcv.lower_shadow = min(ohlcv_data.open_price, ohlcv_data.close_price) - ohlcv_data.low_price
                ohlcv.is_bullish = ohlcv_data.close_price > ohlcv_data.open_price
                ohlcv.is_bearish = ohlcv_data.close_price < ohlcv_data.open_price
                ohlcv.is_doji = abs(ohlcv_data.close_price - ohlcv_data.open_price) < (ohlcv_data.high_price - ohlcv_data.low_price) * 0.1
                
                self.db.add(ohlcv)
                
                logger.debug(f"Created new OHLCV for {ohlcv_data.symbol} on {ohlcv_data.trade_date}")
                return True
                
        except SQLAlchemyError as e:
            logger.error(f"Database error storing OHLCV: {e}")
            raise
    
    def get_ohlcv_by_symbol(
        self,
        symbol: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: int = 100
    ) -> List[StockOHLCV]:
        """
        Get OHLCV data for a stock
        
        Args:
            symbol: Stock symbol
            start_date: Start date filter
            end_date: End date filter
            limit: Maximum number of records
            
        Returns:
            List of StockOHLCV objects
        """
        try:
            # Get stock
            stock = self.db.query(Stock).filter_by(symbol=symbol.upper()).first()
            if not stock:
                logger.warning(f"Stock not found: {symbol}")
                return []
            
            # Build query
            query = self.db.query(StockOHLCV).filter_by(stock_id=stock.id)
            
            if start_date:
                query = query.filter(StockOHLCV.date >= start_date)
            if end_date:
                query = query.filter(StockOHLCV.date <= end_date)
            
            # Order by date descending and limit
            ohlcv_data = query.order_by(StockOHLCV.date.desc()).limit(limit).all()
            
            logger.info(f"Retrieved {len(ohlcv_data)} OHLCV records for {symbol}")
            return ohlcv_data
            
        except SQLAlchemyError as e:
            logger.error(f"Error retrieving OHLCV for {symbol}: {e}")
            return []
    
    def get_latest_ohlcv(self, symbol: str) -> Optional[StockOHLCV]:
        """
        Get latest OHLCV record for a stock
        
        Args:
            symbol: Stock symbol
            
        Returns:
            Latest StockOHLCV object or None
        """
        try:
            stock = self.db.query(Stock).filter_by(symbol=symbol.upper()).first()
            if not stock:
                return None
            
            ohlcv = (
                self.db.query(StockOHLCV)
                .filter_by(stock_id=stock.id)
                .order_by(StockOHLCV.date.desc())
                .first()
            )
            
            return ohlcv
            
        except SQLAlchemyError as e:
            logger.error(f"Error retrieving latest OHLCV for {symbol}: {e}")
            return None
    
    def bulk_fetch_ohlcv(
        self,
        symbols: List[str],
        days: int = 30
    ) -> Dict[str, Any]:
        """
        Fetch OHLCV data for multiple stocks
        
        Args:
            symbols: List of stock symbols
            days: Number of days to fetch
            
        Returns:
            Dictionary with operation results
        """
        results = {
            "status": "success",
            "total_symbols": len(symbols),
            "successful": 0,
            "failed": 0,
            "errors": []
        }
        
        for symbol in symbols:
            try:
                response = self.fetch_and_store_ohlcv(symbol=symbol, days=days)
                if response.status == "success":
                    results["successful"] += 1
                else:
                    results["failed"] += 1
                    results["errors"].append(f"{symbol}: {response.message}")
            except Exception as e:
                results["failed"] += 1
                results["errors"].append(f"{symbol}: {str(e)}")
        
        logger.info(f"Bulk OHLCV fetch completed. Success: {results['successful']}, Failed: {results['failed']}")
        return results
