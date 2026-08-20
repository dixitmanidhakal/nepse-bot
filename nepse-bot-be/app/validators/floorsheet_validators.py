"""
Floorsheet Validators

Pydantic schemas for validating floorsheet (trade details) data
before storing in the database.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import datetime, date
from decimal import Decimal


class FloorsheetTradeSchema(BaseModel):
    """Schema for validating individual floorsheet trade"""
    
    symbol: str = Field(..., min_length=1, max_length=20, description="Stock symbol")
    contract_id: str = Field(..., min_length=1, description="Contract/Trade ID")
    
    # Broker information
    buyer_broker_no: int = Field(..., gt=0, description="Buyer broker number")
    buyer_broker_name: Optional[str] = Field(None, max_length=200, description="Buyer broker name")
    seller_broker_no: int = Field(..., gt=0, description="Seller broker number")
    seller_broker_name: Optional[str] = Field(None, max_length=200, description="Seller broker name")
    
    # Trade details
    quantity: int = Field(..., gt=0, description="Trade quantity")
    rate: float = Field(..., gt=0, description="Trade rate/price")
    amount: float = Field(..., gt=0, description="Trade amount")
    
    # Trade metadata
    trade_time: Optional[datetime] = Field(None, description="Trade execution time")
    trade_date: Optional[date] = Field(None, description="Trade date")
    
    # Flags
    is_institutional: Optional[bool] = Field(False, description="Is institutional trade")
    is_cross_trade: Optional[bool] = Field(False, description="Is cross trade (same broker)")
    
    @validator('symbol')
    def validate_symbol(cls, v):
        """Ensure symbol is uppercase"""
        return v.strip().upper() if v else v
    
    @validator('contract_id')
    def validate_contract_id(cls, v):
        """Ensure contract ID is not empty"""
        if not v or not v.strip():
            raise ValueError("Contract ID cannot be empty")
        return v.strip()
    
    @validator('buyer_broker_no', 'seller_broker_no')
    def validate_broker_numbers(cls, v):
        """Ensure broker numbers are positive"""
        if v <= 0:
            raise ValueError("Broker number must be positive")
        return v
    
    @validator('quantity')
    def validate_quantity(cls, v):
        """Ensure quantity is positive"""
        if v <= 0:
            raise ValueError("Quantity must be positive")
        return v
    
    @validator('rate')
    def validate_rate(cls, v):
        """Ensure rate is positive"""
        if v <= 0:
            raise ValueError("Rate must be positive")
        return v
    
    @validator('amount')
    def validate_amount(cls, v, values):
        """Validate amount matches quantity * rate"""
        if 'quantity' in values and 'rate' in values:
            expected_amount = values['quantity'] * values['rate']
            # Allow small floating point differences
            if abs(v - expected_amount) > 0.01:
                raise ValueError(f"Amount {v} doesn't match quantity * rate = {expected_amount}")
        return v
    
    @validator('is_cross_trade')
    def validate_cross_trade(cls, v, values):
        """Auto-detect cross trade if buyer and seller broker are same"""
        if 'buyer_broker_no' in values and 'seller_broker_no' in values:
            if values['buyer_broker_no'] == values['seller_broker_no']:
                return True
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "symbol": "NABIL",
                "contract_id": "20240115-001234",
                "buyer_broker_no": 10,
                "buyer_broker_name": "Broker A",
                "seller_broker_no": 25,
                "seller_broker_name": "Broker B",
                "quantity": 100,
                "rate": 1200.00,
                "amount": 120000.00,
                "trade_time": "2024-01-15T10:30:00",
                "trade_date": "2024-01-15",
                "is_institutional": False,
                "is_cross_trade": False
            }
        }


class BrokerActivitySchema(BaseModel):
    """Schema for broker activity summary"""
    
    broker_no: int = Field(..., gt=0, description="Broker number")
    broker_name: Optional[str] = Field(None, description="Broker name")
    
    # Buy activity
    total_buy_quantity: int = Field(0, ge=0, description="Total buy quantity")
    total_buy_amount: float = Field(0, ge=0, description="Total buy amount")
    buy_trades_count: int = Field(0, ge=0, description="Number of buy trades")
    
    # Sell activity
    total_sell_quantity: int = Field(0, ge=0, description="Total sell quantity")
    total_sell_amount: float = Field(0, ge=0, description="Total sell amount")
    sell_trades_count: int = Field(0, ge=0, description="Number of sell trades")
    
    # Net activity
    net_quantity: int = Field(0, description="Net quantity (buy - sell)")
    net_amount: float = Field(0, description="Net amount (buy - sell)")
    
    # Activity type
    is_net_buyer: bool = Field(False, description="Is net buyer")
    is_net_seller: bool = Field(False, description="Is net seller")
    
    class Config:
        json_schema_extra = {
            "example": {
                "broker_no": 10,
                "broker_name": "Broker A",
                "total_buy_quantity": 5000,
                "total_buy_amount": 6000000.00,
                "buy_trades_count": 50,
                "total_sell_quantity": 3000,
                "total_sell_amount": 3600000.00,
                "sell_trades_count": 30,
                "net_quantity": 2000,
                "net_amount": 2400000.00,
                "is_net_buyer": True,
                "is_net_seller": False
            }
        }


class FloorsheetResponse(BaseModel):
    """Response schema for floorsheet data fetching"""
    
    status: str = Field(..., description="Status of the operation")
    message: str = Field(..., description="Status message")
    symbol: Optional[str] = Field(None, description="Stock symbol (if filtered)")
    trades_added: int = Field(..., ge=0, description="Number of trades added")
    trades_updated: int = Field(..., ge=0, description="Number of trades updated")
    total_volume: Optional[int] = Field(None, ge=0, description="Total volume traded")
    total_amount: Optional[float] = Field(None, ge=0, description="Total amount traded")
    trade_date: Optional[date] = Field(None, description="Trade date", alias="date")
    timestamp: datetime = Field(default_factory=datetime.now, description="Operation timestamp")
    errors: Optional[list] = Field(default=[], description="List of errors if any")
    
    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "status": "success",
                "message": "Floorsheet data fetched successfully",
                "symbol": "NABIL",
                "trades_added": 150,
                "trades_updated": 0,
                "total_volume": 50000,
                "total_amount": 60000000.00,
                "date": "2024-01-15",
                "timestamp": "2024-01-15T10:30:00",
                "errors": []
            }
        }


class BrokerAnalysisResponse(BaseModel):
    """Response schema for broker analysis"""
    
    status: str = Field(..., description="Status of the operation")
    symbol: str = Field(..., description="Stock symbol")
    analysis_date: date = Field(..., description="Analysis date", alias="date")
    top_buyers: List[BrokerActivitySchema] = Field(..., description="Top buying brokers")
    top_sellers: List[BrokerActivitySchema] = Field(..., description="Top selling brokers")
    institutional_activity: Optional[dict] = Field(None, description="Institutional activity summary")
    manipulation_flags: Optional[List[str]] = Field(default=[], description="Potential manipulation flags")
    timestamp: datetime = Field(default_factory=datetime.now, description="Analysis timestamp")
    
    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "status": "success",
                "symbol": "NABIL",
                "date": "2024-01-15",
                "top_buyers": [],
                "top_sellers": [],
                "institutional_activity": {
                    "total_institutional_volume": 10000,
                    "institutional_percentage": 20.0
                },
                "manipulation_flags": [],
                "timestamp": "2024-01-15T10:30:00"
            }
        }


class FloorsheetListResponse(BaseModel):
    """Response schema for floorsheet list"""
    
    status: str = Field(..., description="Status of the operation")
    trades: List[FloorsheetTradeSchema] = Field(..., description="List of trades")
    total_count: int = Field(..., ge=0, description="Total number of trades")
    total_volume: int = Field(..., ge=0, description="Total volume")
    total_amount: float = Field(..., ge=0, description="Total amount")
    trade_date: Optional[date] = Field(None, description="Trade date", alias="date")
    timestamp: datetime = Field(default_factory=datetime.now, description="Response timestamp")
    
    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "status": "success",
                "trades": [],
                "total_count": 150,
                "total_volume": 50000,
                "total_amount": 60000000.00,
                "date": "2024-01-15",
                "timestamp": "2024-01-15T10:30:00"
            }
        }
