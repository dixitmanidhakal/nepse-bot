"""
Market Depth Service

This service handles fetching and storing market depth (order book) data.
"""

import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.services.data.depth_sources import fetch_market_depth as _fetch_real_depth
from app.services.http import (
    CircuitBreakerOpen,
    RateLimitExceeded,
    UpstreamUnavailable,
)
from app.models.stock import Stock
from app.models.market_depth import MarketDepth
from app.validators.depth_validators import MarketDepthSchema, MarketDepthResponse

# Exceptions that the caller (route layer) maps to HTTP 503 with a specific
# hint. We propagate them instead of wrapping in a status="error" response
# so the route can show users an actionable reason.
_PROPAGATED_UPSTREAM_ERRORS = (
    UpstreamUnavailable,
    CircuitBreakerOpen,
    RateLimitExceeded,
)

logger = logging.getLogger(__name__)


class MarketDepthService:
    """
    Service for fetching and storing market depth data
    
    This service:
    1. Fetches market depth from NEPSE API
    2. Validates the data
    3. Stores/updates in database
    4. Calculates depth metrics
    """
    
    def __init__(self, db: Session):
        """
        Initialize market depth service

        Args:
            db: Database session
        """
        self.db = db
        logger.info("MarketDepthService initialized")
    
    def fetch_and_store_market_depth(self, symbol: str) -> MarketDepthResponse:
        """
        Fetch market depth and store in database
        
        Args:
            symbol: Stock symbol
            
        Returns:
            MarketDepthResponse with operation results
        """
        try:
            logger.info(f"Fetching market depth for {symbol}...")
            
            # Verify stock exists
            stock = self.db.query(Stock).filter_by(symbol=symbol.upper()).first()
            if not stock:
                logger.error(f"Stock not found: {symbol}")
                return MarketDepthResponse(
                    status="error",
                    message=f"Stock not found: {symbol}",
                    symbol=symbol,
                    errors=[f"Stock not found: {symbol}"]
                )
            
            # Fetch data via resilient multi-source chain. Anti-ban errors
            # (upstream unavailable / circuit open / rate-limited) are
            # propagated so the route layer can return a 503 with a
            # user-actionable hint; other errors fall through to the
            # generic `except` below.
            depth_data = _fetch_real_depth(symbol)
            
            # Validate market depth data
            validated_data = self._validate_market_depth(symbol, depth_data)
            
            if not validated_data:
                return MarketDepthResponse(
                    status="error",
                    message="Failed to validate market depth data",
                    symbol=symbol,
                    errors=["Validation failed"]
                )
            
            # Store market depth
            self._store_market_depth(stock.id, validated_data)
            
            # Commit changes
            self.db.commit()
            
            logger.info(f"Market depth stored for {symbol}")
            
            return MarketDepthResponse(
                status="success",
                message=f"Market depth fetched for {symbol}",
                symbol=symbol,
                depth_data=validated_data
            )
            
        except _PROPAGATED_UPSTREAM_ERRORS:
            # Route layer will translate this into a 503 with a specific hint.
            self.db.rollback()
            raise
        except Exception as e:
            logger.error(f"Error in fetch_and_store_market_depth: {e}")
            self.db.rollback()
            return MarketDepthResponse(
                status="error",
                message=f"Failed to fetch market depth: {str(e)}",
                symbol=symbol,
                errors=[str(e)]
            )
    
    def _validate_market_depth(self, symbol: str, data: Dict[str, Any]) -> Optional[MarketDepthSchema]:
        """
        Validate market depth data using Pydantic schema
        
        Args:
            symbol: Stock symbol
            data: Raw market depth data from API
            
        Returns:
            Validated MarketDepthSchema or None if validation fails
        """
        try:
            # Extract buy and sell orders
            buy_orders = data.get("buy_orders", [])
            sell_orders = data.get("sell_orders", [])
            
            # Map API data to schema
            depth_data = {
                "symbol": symbol,
                "timestamp": datetime.now()
            }
            
            # Process buy orders (top 5)
            for i, order in enumerate(buy_orders[:5], 1):
                depth_data[f"buy_price_{i}"] = order.get("price", 0)
                depth_data[f"buy_quantity_{i}"] = order.get("quantity", 0)
                depth_data[f"buy_orders_{i}"] = order.get("orders", 1)
            
            # Process sell orders (top 5)
            for i, order in enumerate(sell_orders[:5], 1):
                depth_data[f"sell_price_{i}"] = order.get("price", 0)
                depth_data[f"sell_quantity_{i}"] = order.get("quantity", 0)
                depth_data[f"sell_orders_{i}"] = order.get("orders", 1)
            
            # Calculate metrics
            total_buy_qty = sum(order.get("quantity", 0) for order in buy_orders[:5])
            total_sell_qty = sum(order.get("quantity", 0) for order in sell_orders[:5])
            
            depth_data["total_buy_quantity"] = total_buy_qty
            depth_data["total_sell_quantity"] = total_sell_qty
            
            # Order imbalance
            if total_buy_qty + total_sell_qty > 0:
                depth_data["order_imbalance"] = (total_buy_qty - total_sell_qty) / (total_buy_qty + total_sell_qty)
            else:
                depth_data["order_imbalance"] = 0
            
            # Bid-ask spread
            if buy_orders and sell_orders:
                best_bid = buy_orders[0].get("price", 0)
                best_ask = sell_orders[0].get("price", 0)
                if best_bid > 0 and best_ask > 0:
                    depth_data["bid_ask_spread"] = best_ask - best_bid
                    depth_data["bid_ask_spread_percent"] = ((best_ask - best_bid) / best_bid) * 100
            
            # Detect walls (large orders)
            depth_data["has_bid_wall"] = self._detect_bid_wall(buy_orders)
            depth_data["has_ask_wall"] = self._detect_ask_wall(sell_orders)
            
            # Validate using Pydantic
            validated = MarketDepthSchema(**depth_data)
            return validated
            
        except Exception as e:
            logger.error(f"Validation error for market depth: {e}")
            return None
    
    def _detect_bid_wall(self, buy_orders: List[Dict]) -> bool:
        """
        Detect if there's a significant bid wall
        
        Args:
            buy_orders: List of buy orders
            
        Returns:
            True if bid wall detected
        """
        if not buy_orders or len(buy_orders) < 2:
            return False
        
        # Check if any order is significantly larger than average
        quantities = [order.get("quantity", 0) for order in buy_orders[:5]]
        avg_qty = sum(quantities) / len(quantities)
        
        for qty in quantities:
            if qty > avg_qty * 3:  # 3x average
                return True
        
        return False
    
    def _detect_ask_wall(self, sell_orders: List[Dict]) -> bool:
        """
        Detect if there's a significant ask wall
        
        Args:
            sell_orders: List of sell orders
            
        Returns:
            True if ask wall detected
        """
        if not sell_orders or len(sell_orders) < 2:
            return False
        
        # Check if any order is significantly larger than average
        quantities = [order.get("quantity", 0) for order in sell_orders[:5]]
        avg_qty = sum(quantities) / len(quantities)
        
        for qty in quantities:
            if qty > avg_qty * 3:  # 3x average
                return True
        
        return False
    
    def _store_market_depth(self, stock_id: int, depth_data: MarketDepthSchema) -> None:
        """
        Store market depth in database
        
        Args:
            stock_id: Stock ID
            depth_data: Validated market depth data
        """
        try:
            # Create new market depth record
            depth = MarketDepth(
                stock_id=stock_id,
                timestamp=depth_data.timestamp,
                buy_price_1=depth_data.buy_price_1,
                buy_quantity_1=depth_data.buy_quantity_1,
                buy_orders_1=depth_data.buy_orders_1,
                buy_price_2=depth_data.buy_price_2,
                buy_quantity_2=depth_data.buy_quantity_2,
                buy_orders_2=depth_data.buy_orders_2,
                buy_price_3=depth_data.buy_price_3,
                buy_quantity_3=depth_data.buy_quantity_3,
                buy_orders_3=depth_data.buy_orders_3,
                buy_price_4=depth_data.buy_price_4,
                buy_quantity_4=depth_data.buy_quantity_4,
                buy_orders_4=depth_data.buy_orders_4,
                buy_price_5=depth_data.buy_price_5,
                buy_quantity_5=depth_data.buy_quantity_5,
                buy_orders_5=depth_data.buy_orders_5,
                sell_price_1=depth_data.sell_price_1,
                sell_quantity_1=depth_data.sell_quantity_1,
                sell_orders_1=depth_data.sell_orders_1,
                sell_price_2=depth_data.sell_price_2,
                sell_quantity_2=depth_data.sell_quantity_2,
                sell_orders_2=depth_data.sell_orders_2,
                sell_price_3=depth_data.sell_price_3,
                sell_quantity_3=depth_data.sell_quantity_3,
                sell_orders_3=depth_data.sell_orders_3,
                sell_price_4=depth_data.sell_price_4,
                sell_quantity_4=depth_data.sell_quantity_4,
                sell_orders_4=depth_data.sell_orders_4,
                sell_price_5=depth_data.sell_price_5,
                sell_quantity_5=depth_data.sell_quantity_5,
                sell_orders_5=depth_data.sell_orders_5,
                total_buy_quantity=depth_data.total_buy_quantity,
                total_sell_quantity=depth_data.total_sell_quantity,
                order_imbalance=depth_data.order_imbalance,
                bid_ask_spread=depth_data.bid_ask_spread,
                bid_ask_spread_percent=depth_data.bid_ask_spread_percent,
                # DB column is Integer (0/1) for index friendliness; coerce
                # the pydantic bool flag to int before writing.
                has_bid_wall=int(bool(depth_data.has_bid_wall)),
                has_ask_wall=int(bool(depth_data.has_ask_wall)),
            )
            
            self.db.add(depth)
            logger.debug(f"Stored market depth for stock_id: {stock_id}")
            
        except SQLAlchemyError as e:
            logger.error(f"Database error storing market depth: {e}")
            raise
    
    def get_latest_market_depth(self, symbol: str) -> Optional[MarketDepth]:
        """
        Get latest market depth for a stock
        
        Args:
            symbol: Stock symbol
            
        Returns:
            Latest MarketDepth object or None
        """
        try:
            stock = self.db.query(Stock).filter_by(symbol=symbol.upper()).first()
            if not stock:
                return None
            
            depth = (
                self.db.query(MarketDepth)
                .filter_by(stock_id=stock.id)
                .order_by(MarketDepth.timestamp.desc())
                .first()
            )
            
            return depth
            
        except SQLAlchemyError as e:
            logger.error(f"Error retrieving market depth for {symbol}: {e}")
            return None
    
    def get_market_depth_history(
        self,
        symbol: str,
        limit: int = 100
    ) -> List[MarketDepth]:
        """
        Get market depth history for a stock
        
        Args:
            symbol: Stock symbol
            limit: Maximum number of records
            
        Returns:
            List of MarketDepth objects
        """
        try:
            stock = self.db.query(Stock).filter_by(symbol=symbol.upper()).first()
            if not stock:
                return []
            
            depth_history = (
                self.db.query(MarketDepth)
                .filter_by(stock_id=stock.id)
                .order_by(MarketDepth.timestamp.desc())
                .limit(limit)
                .all()
            )
            
            logger.info(f"Retrieved {len(depth_history)} market depth records for {symbol}")
            return depth_history
            
        except SQLAlchemyError as e:
            logger.error(f"Error retrieving market depth history for {symbol}: {e}")
            return []
    
    def bulk_fetch_market_depth(self, symbols: List[str]) -> Dict[str, Any]:
        """
        Fetch market depth for multiple stocks
        
        Args:
            symbols: List of stock symbols
            
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
                response = self.fetch_and_store_market_depth(symbol=symbol)
                if response.status == "success":
                    results["successful"] += 1
                else:
                    results["failed"] += 1
                    results["errors"].append(f"{symbol}: {response.message}")
            except Exception as e:
                results["failed"] += 1
                results["errors"].append(f"{symbol}: {str(e)}")
        
        logger.info(f"Bulk market depth fetch completed. Success: {results['successful']}, Failed: {results['failed']}")
        return results
