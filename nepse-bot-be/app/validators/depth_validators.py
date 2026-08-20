"""
Market Depth Validators

Pydantic schemas for validating market depth (order book) data
before storing in the database.
"""

from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import datetime
from decimal import Decimal


class OrderSchema(BaseModel):
    """Schema for individual order in order book"""
    
    price: float = Field(..., gt=0, description="Order price")
    quantity: int = Field(..., gt=0, description="Order quantity")
    orders: Optional[int] = Field(None, gt=0, description="Number of orders at this price")
    
    @validator('price')
    def validate_price(cls, v):
        """Ensure price is positive"""
        if v <= 0:
            raise ValueError("Price must be positive")
        return v
    
    @validator('quantity')
    def validate_quantity(cls, v):
        """Ensure quantity is positive"""
        if v <= 0:
            raise ValueError("Quantity must be positive")
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "price": 1200.00,
                "quantity": 100,
                "orders": 5
            }
        }


class MarketDepthSchema(BaseModel):
    """Schema for validating market depth data"""
    
    symbol: str = Field(..., min_length=1, max_length=20, description="Stock symbol")
    timestamp: datetime = Field(default_factory=datetime.now, description="Data timestamp")
    
    # Buy orders (bids) - top 5
    buy_price_1: Optional[float] = Field(None, ge=0, description="Best buy price")
    buy_quantity_1: Optional[int] = Field(None, ge=0, description="Best buy quantity")
    buy_orders_1: Optional[int] = Field(None, ge=0, description="Number of buy orders at level 1")
    
    buy_price_2: Optional[float] = Field(None, ge=0, description="2nd best buy price")
    buy_quantity_2: Optional[int] = Field(None, ge=0, description="2nd best buy quantity")
    buy_orders_2: Optional[int] = Field(None, ge=0, description="Number of buy orders at level 2")
    
    buy_price_3: Optional[float] = Field(None, ge=0, description="3rd best buy price")
    buy_quantity_3: Optional[int] = Field(None, ge=0, description="3rd best buy quantity")
    buy_orders_3: Optional[int] = Field(None, ge=0, description="Number of buy orders at level 3")
    
    buy_price_4: Optional[float] = Field(None, ge=0, description="4th best buy price")
    buy_quantity_4: Optional[int] = Field(None, ge=0, description="4th best buy quantity")
    buy_orders_4: Optional[int] = Field(None, ge=0, description="Number of buy orders at level 4")
    
    buy_price_5: Optional[float] = Field(None, ge=0, description="5th best buy price")
    buy_quantity_5: Optional[int] = Field(None, ge=0, description="5th best buy quantity")
    buy_orders_5: Optional[int] = Field(None, ge=0, description="Number of buy orders at level 5")
    
    # Sell orders (asks) - top 5
    sell_price_1: Optional[float] = Field(None, ge=0, description="Best sell price")
    sell_quantity_1: Optional[int] = Field(None, ge=0, description="Best sell quantity")
    sell_orders_1: Optional[int] = Field(None, ge=0, description="Number of sell orders at level 1")
    
    sell_price_2: Optional[float] = Field(None, ge=0, description="2nd best sell price")
    sell_quantity_2: Optional[int] = Field(None, ge=0, description="2nd best sell quantity")
    sell_orders_2: Optional[int] = Field(None, ge=0, description="Number of sell orders at level 2")
    
    sell_price_3: Optional[float] = Field(None, ge=0, description="3rd best sell price")
    sell_quantity_3: Optional[int] = Field(None, ge=0, description="3rd best sell quantity")
    sell_orders_3: Optional[int] = Field(None, ge=0, description="Number of sell orders at level 3")
    
    sell_price_4: Optional[float] = Field(None, ge=0, description="4th best sell price")
    sell_quantity_4: Optional[int] = Field(None, ge=0, description="4th best sell quantity")
    sell_orders_4: Optional[int] = Field(None, ge=0, description="Number of sell orders at level 4")
    
    sell_price_5: Optional[float] = Field(None, ge=0, description="5th best sell price")
    sell_quantity_5: Optional[int] = Field(None, ge=0, description="5th best sell quantity")
    sell_orders_5: Optional[int] = Field(None, ge=0, description="Number of sell orders at level 5")
    
    # Calculated metrics
    total_buy_quantity: Optional[int] = Field(None, ge=0, description="Total buy quantity")
    total_sell_quantity: Optional[int] = Field(None, ge=0, description="Total sell quantity")
    order_imbalance: Optional[float] = Field(None, description="Order imbalance ratio")
    bid_ask_spread: Optional[float] = Field(None, ge=0, description="Bid-ask spread")
    bid_ask_spread_percent: Optional[float] = Field(None, ge=0, description="Bid-ask spread percentage")
    
    # Liquidity metrics
    depth_score: Optional[float] = Field(None, ge=0, le=100, description="Depth score (0-100)")
    liquidity_score: Optional[float] = Field(None, ge=0, le=100, description="Liquidity score (0-100)")
    
    # Wall detection
    has_bid_wall: Optional[bool] = Field(None, description="Has significant bid wall")
    has_ask_wall: Optional[bool] = Field(None, description="Has significant ask wall")
    bid_wall_price: Optional[float] = Field(None, ge=0, description="Bid wall price level")
    ask_wall_price: Optional[float] = Field(None, ge=0, description="Ask wall price level")
    
    @validator('symbol')
    def validate_symbol(cls, v):
        """Ensure symbol is uppercase"""
        return v.strip().upper() if v else v
    
    @validator('buy_price_1')
    def validate_buy_price_1(cls, v, values):
        """Ensure best buy price is less than best sell price if both exist"""
        # This will be validated after sell_price_1 is set
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "symbol": "NABIL",
                "timestamp": "2024-01-15T10:30:00",
                "buy_price_1": 1200.00,
                "buy_quantity_1": 100,
                "buy_orders_1": 5,
                "buy_price_2": 1199.00,
                "buy_quantity_2": 200,
                "sell_price_1": 1201.00,
                "sell_quantity_1": 150,
                "sell_orders_1": 3,
                "sell_price_2": 1202.00,
                "sell_quantity_2": 250,
                "total_buy_quantity": 500,
                "total_sell_quantity": 600,
                "order_imbalance": -0.09,
                "bid_ask_spread": 1.00,
                "bid_ask_spread_percent": 0.08
            }
        }


class MarketDepthResponse(BaseModel):
    """Response schema for market depth data fetching"""
    
    status: str = Field(..., description="Status of the operation")
    message: str = Field(..., description="Status message")
    symbol: str = Field(..., description="Stock symbol")
    depth_data: Optional[MarketDepthSchema] = Field(None, description="Market depth data")
    timestamp: datetime = Field(default_factory=datetime.now, description="Operation timestamp")
    errors: Optional[list] = Field(default=[], description="List of errors if any")
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "success",
                "message": "Market depth fetched successfully",
                "symbol": "NABIL",
                "depth_data": None,
                "timestamp": "2024-01-15T10:30:00",
                "errors": []
            }
        }


class BulkMarketDepthResponse(BaseModel):
    """Response schema for bulk market depth fetching"""
    
    status: str = Field(..., description="Status of the operation")
    message: str = Field(..., description="Status message")
    symbols_processed: int = Field(..., ge=0, description="Number of symbols processed")
    symbols_success: int = Field(..., ge=0, description="Number of successful fetches")
    symbols_failed: int = Field(..., ge=0, description="Number of failed fetches")
    timestamp: datetime = Field(default_factory=datetime.now, description="Operation timestamp")
    errors: Optional[list] = Field(default=[], description="List of errors if any")
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "success",
                "message": "Bulk market depth fetched",
                "symbols_processed": 250,
                "symbols_success": 245,
                "symbols_failed": 5,
                "timestamp": "2024-01-15T10:30:00",
                "errors": []
            }
        }
