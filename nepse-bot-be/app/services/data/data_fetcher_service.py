"""
Data Fetcher Service (Orchestrator)

This service orchestrates all data fetching operations.
It coordinates between different data services to fetch all required data.
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, date
from sqlalchemy.orm import Session

from app.services.data.market_data_service import MarketDataService
from app.services.data.stock_data_service import StockDataService
from app.services.data.ohlcv_service import OHLCVService
from app.services.data.market_depth_service import MarketDepthService
from app.services.data.floorsheet_service import FloorsheetService

logger = logging.getLogger(__name__)


class DataFetcherService:
    """
    Orchestrator service for all data fetching operations
    
    This service:
    1. Coordinates all data fetching services
    2. Provides a single interface for fetching all data
    3. Handles dependencies between services
    4. Provides status and progress tracking
    """
    
    def __init__(self, db: Session):
        """
        Initialize data fetcher service
        
        Args:
            db: Database session
        """
        self.db = db
        self.market_service = MarketDataService(db)
        self.stock_service = StockDataService(db)
        self.ohlcv_service = OHLCVService(db)
        self.depth_service = MarketDepthService(db)
        self.floorsheet_service = FloorsheetService(db)
        logger.info("DataFetcherService initialized")
    
    def fetch_all_data(
        self,
        include_ohlcv: bool = True,
        include_depth: bool = True,
        include_floorsheet: bool = True,
        ohlcv_days: int = 30
    ) -> Dict[str, Any]:
        """
        Fetch all data from NEPSE API
        
        This is the main orchestration method that fetches:
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
            Dictionary with operation results
        """
        start_time = datetime.now()
        results = {
            "status": "success",
            "start_time": start_time.isoformat(),
            "operations": {},
            "errors": []
        }
        
        try:
            logger.info("Starting full data fetch operation...")
            
            # Step 1: Fetch market indices and sectors
            logger.info("Step 1/5: Fetching market indices and sectors...")
            market_response = self.market_service.fetch_and_store_market_indices()
            results["operations"]["market_data"] = {
                "status": market_response.status,
                "message": market_response.message,
                "nepse_index": market_response.nepse_index,
                "sectors_updated": market_response.sectors_updated
            }
            if market_response.errors:
                results["errors"].extend(market_response.errors)
            
            # Step 2: Fetch stock list
            logger.info("Step 2/5: Fetching stock list...")
            stock_response = self.stock_service.fetch_and_store_stock_list()
            results["operations"]["stock_data"] = {
                "status": stock_response.status,
                "message": stock_response.message,
                "stocks_added": stock_response.stocks_added,
                "stocks_updated": stock_response.stocks_updated
            }
            if stock_response.errors:
                results["errors"].extend(stock_response.errors)
            
            # Get list of all stock symbols for bulk operations
            stocks = self.stock_service.get_all_stocks(limit=None)
            stock_symbols = [stock.symbol for stock in stocks]
            logger.info(f"Found {len(stock_symbols)} stocks for bulk operations")
            
            # Step 3: Fetch OHLCV data (optional)
            if include_ohlcv and stock_symbols:
                logger.info(f"Step 3/5: Fetching OHLCV data for {len(stock_symbols)} stocks...")
                ohlcv_results = self.ohlcv_service.bulk_fetch_ohlcv(
                    symbols=stock_symbols[:50],  # Limit to first 50 stocks for now
                    days=ohlcv_days
                )
                results["operations"]["ohlcv_data"] = ohlcv_results
            else:
                logger.info("Step 3/5: Skipping OHLCV data fetch")
                results["operations"]["ohlcv_data"] = {"status": "skipped"}
            
            # Step 4: Fetch market depth (optional)
            if include_depth and stock_symbols:
                logger.info(f"Step 4/5: Fetching market depth for {len(stock_symbols)} stocks...")
                depth_results = self.depth_service.bulk_fetch_market_depth(
                    symbols=stock_symbols[:50]  # Limit to first 50 stocks for now
                )
                results["operations"]["market_depth"] = depth_results
            else:
                logger.info("Step 4/5: Skipping market depth fetch")
                results["operations"]["market_depth"] = {"status": "skipped"}
            
            # Step 5: Fetch floorsheet (optional)
            if include_floorsheet:
                logger.info("Step 5/5: Fetching floorsheet data...")
                floorsheet_response = self.floorsheet_service.fetch_and_store_floorsheet()
                results["operations"]["floorsheet"] = {
                    "status": floorsheet_response.status,
                    "message": floorsheet_response.message,
                    "trades_added": floorsheet_response.trades_added,
                    "trades_updated": floorsheet_response.trades_updated
                }
                if floorsheet_response.errors:
                    results["errors"].extend(floorsheet_response.errors)
            else:
                logger.info("Step 5/5: Skipping floorsheet fetch")
                results["operations"]["floorsheet"] = {"status": "skipped"}
            
            # Calculate duration
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            results["end_time"] = end_time.isoformat()
            results["duration_seconds"] = duration
            
            # Determine overall status
            if results["errors"]:
                results["status"] = "partial_success"
            
            logger.info(f"Full data fetch completed in {duration:.2f} seconds")
            
            return results
            
        except Exception as e:
            logger.error(f"Error in fetch_all_data: {e}")
            results["status"] = "error"
            results["errors"].append(str(e))
            return results
    
    def fetch_market_data_only(self) -> Dict[str, Any]:
        """
        Fetch only market indices and sectors
        
        Returns:
            Dictionary with operation results
        """
        logger.info("Fetching market data only...")
        response = self.market_service.fetch_and_store_market_indices()
        return {
            "status": response.status,
            "message": response.message,
            "nepse_index": response.nepse_index,
            "sectors_updated": response.sectors_updated,
            "errors": response.errors
        }
    
    def fetch_stock_data_only(self) -> Dict[str, Any]:
        """
        Fetch only stock list
        
        Returns:
            Dictionary with operation results
        """
        logger.info("Fetching stock data only...")
        response = self.stock_service.fetch_and_store_stock_list()
        return {
            "status": response.status,
            "message": response.message,
            "stocks_added": response.stocks_added,
            "stocks_updated": response.stocks_updated,
            "errors": response.errors
        }
    
    def fetch_ohlcv_for_symbol(
        self,
        symbol: str,
        days: int = 30
    ) -> Dict[str, Any]:
        """
        Fetch OHLCV data for a specific stock
        
        Args:
            symbol: Stock symbol
            days: Number of days to fetch
            
        Returns:
            Dictionary with operation results
        """
        logger.info(f"Fetching OHLCV for {symbol}...")
        response = self.ohlcv_service.fetch_and_store_ohlcv(symbol=symbol, days=days)
        return {
            "status": response.status,
            "message": response.message,
            "symbol": response.symbol,
            "records_added": response.records_added,
            "records_updated": response.records_updated,
            "errors": response.errors
        }
    
    def fetch_market_depth_for_symbol(self, symbol: str) -> Dict[str, Any]:
        """
        Fetch market depth for a specific stock
        
        Args:
            symbol: Stock symbol
            
        Returns:
            Dictionary with operation results
        """
        logger.info(f"Fetching market depth for {symbol}...")
        response = self.depth_service.fetch_and_store_market_depth(symbol=symbol)
        return {
            "status": response.status,
            "message": response.message,
            "symbol": response.symbol,
            "errors": response.errors
        }
    
    def fetch_floorsheet_for_symbol(
        self,
        symbol: str,
        trade_date: Optional[date] = None
    ) -> Dict[str, Any]:
        """
        Fetch floorsheet for a specific stock
        
        Args:
            symbol: Stock symbol
            trade_date: Trade date (optional)
            
        Returns:
            Dictionary with operation results
        """
        logger.info(f"Fetching floorsheet for {symbol}...")
        response = self.floorsheet_service.fetch_and_store_floorsheet(
            symbol=symbol,
            trade_date=trade_date
        )
        return {
            "status": response.status,
            "message": response.message,
            "symbol": response.symbol,
            "trades_added": response.trades_added,
            "trades_updated": response.trades_updated,
            "errors": response.errors
        }
    
    def get_data_status(self) -> Dict[str, Any]:
        """
        Get status of all data in database
        
        Returns:
            Dictionary with data statistics
        """
        try:
            # Get counts
            sectors = self.market_service.get_all_sectors()
            stocks = self.stock_service.get_all_stocks()
            
            # Get latest OHLCV date for a sample stock
            latest_ohlcv_date = None
            if stocks:
                latest_ohlcv = self.ohlcv_service.get_latest_ohlcv(stocks[0].symbol)
                if latest_ohlcv:
                    latest_ohlcv_date = latest_ohlcv.date.isoformat()
            
            return {
                "status": "success",
                "data_status": {
                    "sectors_count": len(sectors),
                    "stocks_count": len(stocks),
                    "latest_ohlcv_date": latest_ohlcv_date,
                    "database_connected": True
                },
                "timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error getting data status: {e}")
            return {
                "status": "error",
                "message": str(e),
                "timestamp": datetime.now().isoformat()
            }
