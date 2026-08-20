"""
Floorsheet Service

This service handles fetching and storing floorsheet (trade details) data.

Data source priority:
  1. Free aggregator (samirwagle CSV → sharesansar HTML) — geo-unrestricted
  2. NepalStockScraper (nepalstock.com.np) — geo-restricted, needs JS session
"""

import asyncio
import concurrent.futures
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, date
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from collections import defaultdict

from app.services.nepse_api_client import create_api_client
from app.services.data.free_sources import aggregator as free_aggregator
from app.models.stock import Stock
from app.models.floorsheet import Floorsheet
from app.validators.floorsheet_validators import (
    FloorsheetTradeSchema,
    FloorsheetResponse,
    BrokerActivitySchema
)

logger = logging.getLogger(__name__)


def _run_async(coro, timeout: int = 45) -> Any:
    """Run async coroutine from sync code without conflicting with FastAPI event loop."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result(timeout=timeout)


class FloorsheetService:
    """
    Service for fetching and storing floorsheet data
    
    This service:
    1. Fetches floorsheet from NEPSE API
    2. Validates the data
    3. Stores/updates in database
    4. Analyzes broker activity
    """
    
    def __init__(self, db: Session):
        """
        Initialize floorsheet service
        
        Args:
            db: Database session
        """
        self.db = db
        self.api_client = create_api_client("nepse")
        logger.info("FloorsheetService initialized")
    
    # ------------------------------------------------------------------ #
    # Free-aggregator helpers                                            #
    # ------------------------------------------------------------------ #

    def _fetch_via_free_aggregator(
        self,
        symbol: Optional[str] = None,
        trade_date: Optional[date] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch floorsheet from samirwagle CSV (primary) → sharesansar HTML (fallback).

        Both are geo-unrestricted.  The aggregator.floorsheet() returns rows with:
          symbol, contract_id, buyer_broker, seller_broker, quantity, price, amount,
          trade_time.

        We normalise `price` → `rate` so the existing _validate_floorsheet_trade
        mapping works unchanged.
        """
        try:
            logger.info(
                f"Fetching floorsheet via free aggregator "
                f"(symbol={symbol}, date={trade_date})…"
            )
            rows = _run_async(
                free_aggregator.floorsheet(target=trade_date, symbol=symbol),
                timeout=40,
            )

            if not rows:
                logger.debug("Free aggregator returned no floorsheet data")
                return []

            # Normalise: rename 'price' → 'rate' to match _validate_floorsheet_trade
            normalised: List[Dict[str, Any]] = []
            for row in rows:
                normalised.append({
                    "symbol":       row.get("symbol", symbol or ""),
                    "contract_id":  row.get("contract_id") or row.get("contractId") or "",
                    "buyer_broker": row.get("buyer_broker") or row.get("buyerBrokerNo") or 0,
                    "seller_broker": row.get("seller_broker") or row.get("sellerBrokerNo") or 0,
                    "quantity":     row.get("quantity") or row.get("contractQuantity") or 0,
                    "rate":         row.get("price") or row.get("rate") or row.get("contractRate") or 0,
                    "amount":       row.get("amount") or row.get("contractAmount") or 0,
                    "trade_time":   row.get("trade_time") or row.get("tradeTime") or None,
                })

            logger.info(f"Free aggregator → {len(normalised)} floorsheet rows")
            return normalised

        except Exception as exc:
            logger.warning(f"Free aggregator floorsheet fetch failed: {exc}")
            return []

    # ------------------------------------------------------------------ #

    def fetch_and_store_floorsheet(
        self,
        symbol: Optional[str] = None,
        trade_date: Optional[date] = None
    ) -> FloorsheetResponse:
        """
        Fetch floorsheet and store in database.

        Data source cascade:
          1. Free aggregator — samirwagle CSV → sharesansar HTML
          2. NepalStockScraper — nepalstock.com (may return 401)

        Args:
            symbol: Stock symbol (optional, fetch all if None)
            trade_date: Trade date (optional, fetch today if None)

        Returns:
            FloorsheetResponse with operation results
        """
        try:
            if symbol:
                logger.info(f"Fetching floorsheet for {symbol}…")
            else:
                logger.info("Fetching floorsheet for all stocks…")

            # Resolve stock_id (only for DB linking, not a hard requirement)
            stock_id = None
            if symbol:
                stock = self.db.query(Stock).filter_by(symbol=symbol.upper()).first()
                if stock:
                    stock_id = stock.id
                # Don't bail if stock not in DB — floorsheet can still be saved

            # ── 1. Free aggregator ────────────────────────────────────── #
            floorsheet_data = self._fetch_via_free_aggregator(symbol, trade_date)

            # ── 2. NepalStockScraper fallback ─────────────────────────── #
            if not floorsheet_data:
                logger.info(
                    "Free aggregator empty — falling back to nepalstock.com scraper…"
                )
                try:
                    trade_datetime = None
                    if trade_date:
                        trade_datetime = datetime.combine(trade_date, datetime.min.time())
                    floorsheet_data = self.api_client.fetch_floorsheet(
                        symbol=symbol,
                        date=trade_datetime,
                    )
                except Exception as exc:
                    logger.warning(f"NepalStockScraper floorsheet failed: {exc}")
                    floorsheet_data = []
                finally:
                    self.api_client.close()

            if not floorsheet_data:
                logger.warning("No floorsheet data received from any source")
                return FloorsheetResponse(
                    status="warning",
                    message="No floorsheet data received",
                    symbol=symbol,
                    trades_added=0,
                    trades_updated=0,
                    errors=["No data received"],
                )
            
            logger.info(f"Received {len(floorsheet_data)} floorsheet records")
            
            # Process floorsheet data
            trades_added = 0
            trades_updated = 0
            total_volume = 0
            total_amount = 0.0
            errors = []
            
            for trade_data in floorsheet_data:
                try:
                    # Validate trade data
                    validated_data = self._validate_floorsheet_trade(trade_data)
                    
                    if validated_data:
                        # Get stock_id for this trade
                        trade_stock = self.db.query(Stock).filter_by(
                            symbol=validated_data.symbol
                        ).first()
                        
                        if trade_stock:
                            # Store trade
                            is_new = self._store_floorsheet_trade(trade_stock.id, validated_data)
                            if is_new:
                                trades_added += 1
                            else:
                                trades_updated += 1
                            
                            total_volume += validated_data.quantity
                            total_amount += validated_data.amount
                        else:
                            logger.warning(f"Stock not found for trade: {validated_data.symbol}")
                            
                except Exception as e:
                    error_msg = f"Error processing trade: {str(e)}"
                    logger.error(error_msg)
                    errors.append(error_msg)
            
            # Commit changes
            self.db.commit()
            
            logger.info(f"Floorsheet processed. Added: {trades_added}, Updated: {trades_updated}")
            
            return FloorsheetResponse(
                status="success" if not errors else "partial_success",
                message=f"Floorsheet data fetched",
                symbol=symbol,
                trades_added=trades_added,
                trades_updated=trades_updated,
                total_volume=total_volume,
                total_amount=total_amount,
                date=trade_date,
                errors=errors
            )
            
        except Exception as e:
            logger.error(f"Error in fetch_and_store_floorsheet: {e}")
            self.db.rollback()
            return FloorsheetResponse(
                status="error",
                message=f"Failed to fetch floorsheet: {str(e)}",
                symbol=symbol,
                trades_added=0,
                trades_updated=0,
                errors=[str(e)]
            )
        finally:
            self.api_client.close()
    
    def _validate_floorsheet_trade(self, data: Dict[str, Any]) -> Optional[FloorsheetTradeSchema]:
        """
        Validate floorsheet trade data using Pydantic schema
        
        Args:
            data: Raw trade data from API
            
        Returns:
            Validated FloorsheetTradeSchema or None if validation fails
        """
        try:
            # Parse dates
            trade_time = data.get("trade_time", data.get("tradeTime"))
            if isinstance(trade_time, str):
                try:
                    trade_time = datetime.fromisoformat(trade_time)
                except:
                    trade_time = None
            
            trade_date = data.get("trade_date", data.get("tradeDate"))
            if isinstance(trade_date, str):
                try:
                    trade_date = datetime.strptime(trade_date, "%Y-%m-%d").date()
                except:
                    trade_date = None
            
            # Map API data to schema
            trade_data = {
                "symbol": data.get("symbol", ""),
                "contract_id": data.get("contract_id", data.get("contractId", "")),
                "buyer_broker_no": data.get("buyer_broker", data.get("buyerBrokerNo", 0)),
                "buyer_broker_name": data.get("buyer_broker_name", data.get("buyerBrokerName")),
                "seller_broker_no": data.get("seller_broker", data.get("sellerBrokerNo", 0)),
                "seller_broker_name": data.get("seller_broker_name", data.get("sellerBrokerName")),
                "quantity": data.get("quantity", data.get("contractQuantity", 0)),
                "rate": data.get("rate", data.get("contractRate", 0)),
                "amount": data.get("amount", data.get("contractAmount", 0)),
                "trade_time": trade_time,
                "trade_date": trade_date,
                "is_institutional": data.get("is_institutional", False),
                "is_cross_trade": data.get("is_cross_trade", False)
            }
            
            # Validate using Pydantic
            validated = FloorsheetTradeSchema(**trade_data)
            return validated
            
        except Exception as e:
            logger.error(f"Validation error for floorsheet trade: {e}")
            return None
    
    def _store_floorsheet_trade(self, stock_id: int, trade_data: FloorsheetTradeSchema) -> bool:
        """
        Store floorsheet trade in database
        
        Args:
            stock_id: Stock ID
            trade_data: Validated trade data
            
        Returns:
            True if new trade created, False if updated
        """
        try:
            # Check if trade exists (by contract_id)
            trade = self.db.query(Floorsheet).filter_by(
                contract_id=trade_data.contract_id
            ).first()
            
            if trade:
                # Update existing trade (rare case)
                trade.buyer_broker_no = trade_data.buyer_broker_no
                trade.buyer_broker_name = trade_data.buyer_broker_name
                trade.seller_broker_no = trade_data.seller_broker_no
                trade.seller_broker_name = trade_data.seller_broker_name
                trade.quantity = trade_data.quantity
                trade.rate = trade_data.rate
                trade.amount = trade_data.amount
                trade.trade_time = trade_data.trade_time
                trade.trade_date = trade_data.trade_date
                trade.is_institutional = trade_data.is_institutional
                trade.is_cross_trade = trade_data.is_cross_trade
                
                logger.debug(f"Updated trade: {trade_data.contract_id}")
                return False
            else:
                # Create new trade
                trade = Floorsheet(
                    stock_id=stock_id,
                    contract_id=trade_data.contract_id,
                    buyer_broker_no=trade_data.buyer_broker_no,
                    buyer_broker_name=trade_data.buyer_broker_name,
                    seller_broker_no=trade_data.seller_broker_no,
                    seller_broker_name=trade_data.seller_broker_name,
                    quantity=trade_data.quantity,
                    rate=trade_data.rate,
                    amount=trade_data.amount,
                    trade_time=trade_data.trade_time,
                    trade_date=trade_data.trade_date,
                    is_institutional=trade_data.is_institutional,
                    is_cross_trade=trade_data.is_cross_trade
                )
                
                self.db.add(trade)
                logger.debug(f"Created new trade: {trade_data.contract_id}")
                return True
                
        except SQLAlchemyError as e:
            logger.error(f"Database error storing floorsheet trade: {e}")
            raise
    
    def get_floorsheet_by_symbol(
        self,
        symbol: str,
        trade_date: Optional[date] = None,
        limit: int = 100
    ) -> List[Floorsheet]:
        """
        Get floorsheet data for a stock
        
        Args:
            symbol: Stock symbol
            trade_date: Trade date filter
            limit: Maximum number of records
            
        Returns:
            List of Floorsheet objects
        """
        try:
            stock = self.db.query(Stock).filter_by(symbol=symbol.upper()).first()
            if not stock:
                return []
            
            query = self.db.query(Floorsheet).filter_by(stock_id=stock.id)
            
            if trade_date:
                query = query.filter(Floorsheet.trade_date == trade_date)
            
            trades = query.order_by(Floorsheet.trade_time.desc()).limit(limit).all()
            
            logger.info(f"Retrieved {len(trades)} floorsheet records for {symbol}")
            return trades
            
        except SQLAlchemyError as e:
            logger.error(f"Error retrieving floorsheet for {symbol}: {e}")
            return []
    
    def analyze_broker_activity(
        self,
        symbol: str,
        trade_date: date
    ) -> Dict[str, Any]:
        """
        Analyze broker activity for a stock on a specific date
        
        Args:
            symbol: Stock symbol
            trade_date: Trade date
            
        Returns:
            Dictionary with broker activity analysis
        """
        try:
            # Get floorsheet data
            trades = self.get_floorsheet_by_symbol(symbol, trade_date, limit=10000)
            
            if not trades:
                return {
                    "status": "error",
                    "message": "No trades found",
                    "symbol": symbol,
                    "date": trade_date
                }
            
            # Analyze broker activity
            broker_activity = defaultdict(lambda: {
                "buy_quantity": 0,
                "buy_amount": 0.0,
                "buy_trades": 0,
                "sell_quantity": 0,
                "sell_amount": 0.0,
                "sell_trades": 0
            })
            
            for trade in trades:
                # Buyer activity
                broker_activity[trade.buyer_broker_no]["buy_quantity"] += trade.quantity
                broker_activity[trade.buyer_broker_no]["buy_amount"] += trade.amount
                broker_activity[trade.buyer_broker_no]["buy_trades"] += 1
                
                # Seller activity
                broker_activity[trade.seller_broker_no]["sell_quantity"] += trade.quantity
                broker_activity[trade.seller_broker_no]["sell_amount"] += trade.amount
                broker_activity[trade.seller_broker_no]["sell_trades"] += 1
            
            # Convert to list and calculate net activity
            broker_list = []
            for broker_no, activity in broker_activity.items():
                net_quantity = activity["buy_quantity"] - activity["sell_quantity"]
                net_amount = activity["buy_amount"] - activity["sell_amount"]
                
                broker_list.append({
                    "broker_no": broker_no,
                    "buy_quantity": activity["buy_quantity"],
                    "buy_amount": activity["buy_amount"],
                    "buy_trades": activity["buy_trades"],
                    "sell_quantity": activity["sell_quantity"],
                    "sell_amount": activity["sell_amount"],
                    "sell_trades": activity["sell_trades"],
                    "net_quantity": net_quantity,
                    "net_amount": net_amount,
                    "is_net_buyer": net_quantity > 0,
                    "is_net_seller": net_quantity < 0
                })
            
            # Sort by net quantity
            top_buyers = sorted(
                [b for b in broker_list if b["is_net_buyer"]],
                key=lambda x: x["net_quantity"],
                reverse=True
            )[:10]
            
            top_sellers = sorted(
                [b for b in broker_list if b["is_net_seller"]],
                key=lambda x: abs(x["net_quantity"]),
                reverse=True
            )[:10]
            
            return {
                "status": "success",
                "symbol": symbol,
                "date": trade_date,
                "total_trades": len(trades),
                "total_volume": sum(t.quantity for t in trades),
                "total_amount": sum(t.amount for t in trades),
                "top_buyers": top_buyers,
                "top_sellers": top_sellers,
                "unique_brokers": len(broker_activity)
            }
            
        except Exception as e:
            logger.error(f"Error analyzing broker activity: {e}")
            return {
                "status": "error",
                "message": str(e),
                "symbol": symbol,
                "date": trade_date
            }
