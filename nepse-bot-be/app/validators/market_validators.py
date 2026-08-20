"""
Market Data Validators

Pydantic schemas for validating market indices and sector data
before storing in the database.
"""

from pydantic import BaseModel, Field, validator
from typing import Optional, Dict, Any
from datetime import datetime
from decimal import Decimal


class MarketIndexSchema(BaseModel):
    """Schema for validating market index data"""
    
    index_value: float = Field(..., gt=0, description="Current index value")
    change: Optional[float] = Field(None, description="Change in index value")
    change_percent: Optional[float] = Field(None, description="Percentage change")
    timestamp: datetime = Field(default_factory=datetime.now, description="Data timestamp")
    
    @validator('index_value')
    def validate_index_value(cls, v):
        """Ensure index value is positive"""
        if v <= 0:
            raise ValueError("Index value must be positive")
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "index_value": 2100.50,
                "change": 15.25,
                "change_percent": 0.73,
                "timestamp": "2024-01-15T10:30:00"
            }
        }


class SectorDataSchema(BaseModel):
    """Schema for validating sector data"""
    
    name: str = Field(..., min_length=1, max_length=100, description="Sector name")
    symbol: str = Field(..., min_length=1, max_length=50, description="Sector symbol")
    current_index: float = Field(..., gt=0, description="Current sector index")
    change: Optional[float] = Field(None, description="Change in index")
    change_percent: Optional[float] = Field(None, description="Percentage change")
    
    # Momentum indicators
    momentum_1d: Optional[float] = Field(None, description="1-day momentum")
    momentum_5d: Optional[float] = Field(None, description="5-day momentum")
    momentum_10d: Optional[float] = Field(None, description="10-day momentum")
    momentum_20d: Optional[float] = Field(None, description="20-day momentum")
    momentum_30d: Optional[float] = Field(None, description="30-day momentum")
    
    # Volume metrics
    total_volume: Optional[int] = Field(None, ge=0, description="Total trading volume")
    total_turnover: Optional[float] = Field(None, ge=0, description="Total turnover")
    avg_volume_10d: Optional[float] = Field(None, ge=0, description="10-day average volume")
    
    # Market statistics
    total_stocks: Optional[int] = Field(None, ge=0, description="Number of stocks in sector")
    advancing_stocks: Optional[int] = Field(None, ge=0, description="Number of advancing stocks")
    declining_stocks: Optional[int] = Field(None, ge=0, description="Number of declining stocks")
    unchanged_stocks: Optional[int] = Field(None, ge=0, description="Number of unchanged stocks")
    
    # Relative strength
    relative_strength_nepse: Optional[float] = Field(None, description="Relative strength vs NEPSE")
    
    # Ranking
    sector_rank: Optional[int] = Field(None, ge=1, description="Sector ranking")
    
    @validator('name', 'symbol')
    def validate_not_empty(cls, v):
        """Ensure name and symbol are not empty"""
        if not v or not v.strip():
            raise ValueError("Field cannot be empty")
        return v.strip()
    
    @validator('current_index')
    def validate_current_index(cls, v):
        """Ensure current index is positive"""
        if v <= 0:
            raise ValueError("Current index must be positive")
        return v
    
    @validator('total_stocks', 'advancing_stocks', 'declining_stocks', 'unchanged_stocks')
    def validate_stock_counts(cls, v):
        """Ensure stock counts are non-negative"""
        if v is not None and v < 0:
            raise ValueError("Stock count cannot be negative")
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "name": "Commercial Banks",
                "symbol": "BANKING",
                "current_index": 1500.25,
                "change": 10.50,
                "change_percent": 0.70,
                "momentum_1d": 0.70,
                "momentum_5d": 2.50,
                "momentum_10d": 5.20,
                "total_volume": 1000000,
                "total_turnover": 1500000000.00,
                "total_stocks": 25,
                "advancing_stocks": 15,
                "declining_stocks": 8,
                "unchanged_stocks": 2
            }
        }


class MarketDataResponse(BaseModel):
    """Response schema for market data fetching"""
    
    status: str = Field(..., description="Status of the operation")
    message: str = Field(..., description="Status message")
    nepse_index: Optional[float] = Field(None, description="NEPSE index value")
    sectors_updated: Optional[int] = Field(None, description="Number of sectors updated")
    timestamp: datetime = Field(default_factory=datetime.now, description="Operation timestamp")
    errors: Optional[list] = Field(default=[], description="List of errors if any")
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "success",
                "message": "Market data fetched successfully",
                "nepse_index": 2100.50,
                "sectors_updated": 12,
                "timestamp": "2024-01-15T10:30:00",
                "errors": []
            }
        }


class SectorListResponse(BaseModel):
    """Response schema for sector list"""
    
    status: str = Field(..., description="Status of the operation")
    sectors: list[SectorDataSchema] = Field(..., description="List of sectors")
    total_count: int = Field(..., ge=0, description="Total number of sectors")
    timestamp: datetime = Field(default_factory=datetime.now, description="Response timestamp")
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "success",
                "sectors": [],
                "total_count": 12,
                "timestamp": "2024-01-15T10:30:00"
            }
        }
