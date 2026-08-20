"""
Stock Data Validators

Pydantic schemas for validating stock data, OHLCV data,
and fundamental data before storing in the database.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import datetime, date
from decimal import Decimal


class StockDataSchema(BaseModel):
    """Schema for validating stock data"""
    
    symbol: str = Field(..., min_length=1, max_length=20, description="Stock symbol")
    name: str = Field(..., min_length=1, max_length=200, description="Stock name")
    sector_name: Optional[str] = Field(None, max_length=100, description="Sector name")
    
    # Current market data
    ltp: Optional[float] = Field(None, ge=0, description="Last traded price")
    open_price: Optional[float] = Field(None, ge=0, description="Opening price")
    high_price: Optional[float] = Field(None, ge=0, description="High price")
    low_price: Optional[float] = Field(None, ge=0, description="Low price")
    close_price: Optional[float] = Field(None, ge=0, description="Closing price")
    previous_close: Optional[float] = Field(None, ge=0, description="Previous closing price")
    
    # Volume data
    volume: Optional[int] = Field(None, ge=0, description="Trading volume")
    turnover: Optional[float] = Field(None, ge=0, description="Turnover amount")
    
    # Change metrics
    change: Optional[float] = Field(None, description="Price change")
    change_percent: Optional[float] = Field(None, description="Percentage change")
    
    # Trading metrics
    total_trades: Optional[int] = Field(None, ge=0, description="Total number of trades")
    fifty_two_week_high: Optional[float] = Field(None, ge=0, description="52-week high")
    fifty_two_week_low: Optional[float] = Field(None, ge=0, description="52-week low")
    
    # Market cap
    market_cap: Optional[float] = Field(None, ge=0, description="Market capitalization")
    
    @validator('symbol', 'name')
    def validate_not_empty(cls, v):
        """Ensure symbol and name are not empty"""
        if not v or not v.strip():
            raise ValueError("Field cannot be empty")
        return v.strip().upper() if v else v
    
    @validator('ltp', 'open_price', 'high_price', 'low_price', 'close_price')
    def validate_prices(cls, v):
        """Ensure prices are non-negative"""
        if v is not None and v < 0:
            raise ValueError("Price cannot be negative")
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "symbol": "NABIL",
                "name": "Nabil Bank Limited",
                "sector_name": "Commercial Banks",
                "ltp": 1200.00,
                "open_price": 1190.00,
                "high_price": 1210.00,
                "low_price": 1185.00,
                "close_price": 1200.00,
                "previous_close": 1195.00,
                "volume": 50000,
                "turnover": 60000000.00,
                "change": 5.00,
                "change_percent": 0.42
            }
        }


class OHLCVDataSchema(BaseModel):
    """Schema for validating OHLCV data"""
    
    symbol: str = Field(..., min_length=1, max_length=20, description="Stock symbol")
    trade_date: date = Field(..., description="Trading date", alias="date")
    
    # OHLCV data
    open_price: float = Field(..., ge=0, description="Opening price", alias="open")
    high_price: float = Field(..., ge=0, description="High price", alias="high")
    low_price: float = Field(..., ge=0, description="Low price", alias="low")
    close_price: float = Field(..., ge=0, description="Closing price", alias="close")
    volume: int = Field(..., ge=0, description="Trading volume")
    
    # Additional data
    turnover: Optional[float] = Field(None, ge=0, description="Turnover amount")
    total_trades: Optional[int] = Field(None, ge=0, description="Total trades")
    
    # Adjusted prices (for corporate actions)
    adjusted_open: Optional[float] = Field(None, ge=0, description="Adjusted opening price")
    adjusted_high: Optional[float] = Field(None, ge=0, description="Adjusted high price")
    adjusted_low: Optional[float] = Field(None, ge=0, description="Adjusted low price")
    adjusted_close: Optional[float] = Field(None, ge=0, description="Adjusted closing price")
    
    @validator('symbol')
    def validate_symbol(cls, v):
        """Ensure symbol is uppercase"""
        return v.strip().upper() if v else v
    
    @validator('high_price')
    def validate_high(cls, v, values):
        """Ensure high is >= low"""
        if 'low_price' in values and v < values['low_price']:
            raise ValueError("High price must be >= low price")
        return v
    
    @validator('close_price')
    def validate_close(cls, v, values):
        """Ensure close is within high-low range"""
        if 'high_price' in values and 'low_price' in values:
            if v > values['high_price'] or v < values['low_price']:
                raise ValueError("Close price must be within high-low range")
        return v
    
    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "symbol": "NABIL",
                "date": "2024-01-15",
                "open": 1190.00,
                "high": 1210.00,
                "low": 1185.00,
                "close": 1200.00,
                "volume": 50000,
                "turnover": 60000000.00,
                "total_trades": 500
            }
        }


class FundamentalDataSchema(BaseModel):
    """Schema for validating fundamental data"""
    
    symbol: str = Field(..., min_length=1, max_length=20, description="Stock symbol")
    
    # Valuation metrics
    eps: Optional[float] = Field(None, description="Earnings per share")
    pe_ratio: Optional[float] = Field(None, ge=0, description="Price to earnings ratio")
    book_value: Optional[float] = Field(None, ge=0, description="Book value per share")
    pb_ratio: Optional[float] = Field(None, ge=0, description="Price to book ratio")
    
    # Profitability metrics
    roe: Optional[float] = Field(None, description="Return on equity")
    roa: Optional[float] = Field(None, description="Return on assets")
    net_profit_margin: Optional[float] = Field(None, description="Net profit margin")
    
    # Financial health
    debt_to_equity: Optional[float] = Field(None, ge=0, description="Debt to equity ratio")
    current_ratio: Optional[float] = Field(None, ge=0, description="Current ratio")
    
    # Dividend metrics
    dividend_yield: Optional[float] = Field(None, ge=0, description="Dividend yield")
    dividend_per_share: Optional[float] = Field(None, ge=0, description="Dividend per share")
    
    # Market metrics
    market_cap: Optional[float] = Field(None, ge=0, description="Market capitalization")
    shares_outstanding: Optional[int] = Field(None, ge=0, description="Shares outstanding")
    
    @validator('symbol')
    def validate_symbol(cls, v):
        """Ensure symbol is uppercase"""
        return v.strip().upper() if v else v
    
    class Config:
        json_schema_extra = {
            "example": {
                "symbol": "NABIL",
                "eps": 45.50,
                "pe_ratio": 26.37,
                "book_value": 250.00,
                "pb_ratio": 4.80,
                "roe": 18.20,
                "debt_to_equity": 1.50,
                "dividend_yield": 5.20,
                "market_cap": 50000000000
            }
        }


class StockListResponse(BaseModel):
    """Response schema for stock list"""
    
    status: str = Field(..., description="Status of the operation")
    stocks: List[StockDataSchema] = Field(..., description="List of stocks")
    total_count: int = Field(..., ge=0, description="Total number of stocks")
    timestamp: datetime = Field(default_factory=datetime.now, description="Response timestamp")
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "success",
                "stocks": [],
                "total_count": 250,
                "timestamp": "2024-01-15T10:30:00"
            }
        }


class OHLCVResponse(BaseModel):
    """Response schema for OHLCV data fetching"""
    
    status: str = Field(..., description="Status of the operation")
    message: str = Field(..., description="Status message")
    symbol: str = Field(..., description="Stock symbol")
    records_added: int = Field(..., ge=0, description="Number of records added")
    records_updated: int = Field(..., ge=0, description="Number of records updated")
    date_range: Optional[dict] = Field(None, description="Date range of data")
    timestamp: datetime = Field(default_factory=datetime.now, description="Operation timestamp")
    errors: Optional[list] = Field(default=[], description="List of errors if any")
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "success",
                "message": "OHLCV data fetched successfully",
                "symbol": "NABIL",
                "records_added": 30,
                "records_updated": 0,
                "date_range": {
                    "start": "2024-01-01",
                    "end": "2024-01-30"
                },
                "timestamp": "2024-01-15T10:30:00",
                "errors": []
            }
        }


class StockDataResponse(BaseModel):
    """Response schema for stock data fetching"""
    
    status: str = Field(..., description="Status of the operation")
    message: str = Field(..., description="Status message")
    stocks_added: int = Field(..., ge=0, description="Number of stocks added")
    stocks_updated: int = Field(..., ge=0, description="Number of stocks updated")
    timestamp: datetime = Field(default_factory=datetime.now, description="Operation timestamp")
    errors: Optional[list] = Field(default=[], description="List of errors if any")
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "success",
                "message": "Stock data fetched successfully",
                "stocks_added": 10,
                "stocks_updated": 240,
                "timestamp": "2024-01-15T10:30:00",
                "errors": []
            }
        }
