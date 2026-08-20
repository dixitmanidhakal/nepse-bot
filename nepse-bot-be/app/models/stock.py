"""
Stock Model

This model stores stock information and current market data.
Includes fundamental data, technical metrics, and relationships to other models.
"""

from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey, Index
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.database import Base


class Stock(Base):
    """
    Stock Model
    
    Stores stock information, current prices, and fundamental data.
    Central model that connects to OHLCV, signals, and patterns.
    """
    
    __tablename__ = "stocks"
    
    # Primary Key
    id = Column(Integer, primary_key=True, index=True)
    
    # Stock Identification
    symbol = Column(String(20), nullable=False, unique=True, index=True)
    name = Column(String(200), nullable=False)
    sector_id = Column(Integer, ForeignKey("sectors.id"), nullable=True, index=True)
    
    # Stock Classification
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    is_tradeable = Column(Boolean, default=True, nullable=False)
    listing_date = Column(DateTime(timezone=True), nullable=True)
    
    # Current Market Data
    ltp = Column(Float, nullable=True)  # Last Traded Price
    previous_close = Column(Float, nullable=True)
    open_price = Column(Float, nullable=True)
    high_price = Column(Float, nullable=True)
    low_price = Column(Float, nullable=True)
    change = Column(Float, nullable=True)  # Absolute change
    change_percent = Column(Float, nullable=True)  # Percentage change
    
    # Volume Data
    volume = Column(Float, nullable=True)  # Today's volume
    turnover = Column(Float, nullable=True)  # Today's turnover
    total_trades = Column(Integer, nullable=True)  # Number of trades
    
    # Price Ranges
    week_52_high = Column(Float, nullable=True)
    week_52_low = Column(Float, nullable=True)
    week_52_high_date = Column(DateTime(timezone=True), nullable=True)
    week_52_low_date = Column(DateTime(timezone=True), nullable=True)
    
    # Market Statistics
    market_cap = Column(Float, nullable=True)  # Market capitalization
    outstanding_shares = Column(Float, nullable=True)
    free_float = Column(Float, nullable=True)  # Free float shares
    
    # Fundamental Data
    eps = Column(Float, nullable=True)  # Earnings Per Share
    pe_ratio = Column(Float, nullable=True)  # Price to Earnings
    book_value = Column(Float, nullable=True)  # Book Value Per Share
    pb_ratio = Column(Float, nullable=True)  # Price to Book
    roe = Column(Float, nullable=True)  # Return on Equity
    dividend_yield = Column(Float, nullable=True)  # Dividend Yield %
    
    # Technical Indicators (Cached)
    beta = Column(Float, nullable=True)  # Beta vs NEPSE
    volatility = Column(Float, nullable=True)  # Historical volatility
    avg_volume_10d = Column(Float, nullable=True)  # 10-day average volume
    avg_volume_30d = Column(Float, nullable=True)  # 30-day average volume
    
    # Moving Averages (Cached)
    sma_20 = Column(Float, nullable=True)
    sma_50 = Column(Float, nullable=True)
    sma_200 = Column(Float, nullable=True)
    ema_9 = Column(Float, nullable=True)
    ema_21 = Column(Float, nullable=True)
    
    # Momentum Indicators (Cached)
    rsi_14 = Column(Float, nullable=True)
    macd = Column(Float, nullable=True)
    macd_signal = Column(Float, nullable=True)
    macd_histogram = Column(Float, nullable=True)
    
    # Volatility Indicators (Cached)
    atr_14 = Column(Float, nullable=True)
    bollinger_upper = Column(Float, nullable=True)
    bollinger_middle = Column(Float, nullable=True)
    bollinger_lower = Column(Float, nullable=True)
    
    # Support and Resistance Levels (Cached)
    support_1 = Column(Float, nullable=True)
    support_2 = Column(Float, nullable=True)
    support_3 = Column(Float, nullable=True)
    resistance_1 = Column(Float, nullable=True)
    resistance_2 = Column(Float, nullable=True)
    resistance_3 = Column(Float, nullable=True)
    
    # Screening Flags
    passes_volume_filter = Column(Boolean, default=False, nullable=False)
    passes_beta_filter = Column(Boolean, default=False, nullable=False)
    passes_volatility_filter = Column(Boolean, default=False, nullable=False)
    in_bullish_sector = Column(Boolean, default=False, nullable=False)
    
    # Timestamps
    last_traded_date = Column(DateTime(timezone=True), nullable=True)
    indicators_updated_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Relationships
    sector = relationship("Sector", back_populates="stocks")
    ohlcv_data = relationship("StockOHLCV", back_populates="stock", cascade="all, delete-orphan")
    market_depth = relationship("MarketDepth", back_populates="stock", cascade="all, delete-orphan")
    floorsheet = relationship("Floorsheet", back_populates="stock", cascade="all, delete-orphan")
    signals = relationship("Signal", back_populates="stock", cascade="all, delete-orphan")
    patterns = relationship("Pattern", back_populates="stock", cascade="all, delete-orphan")
    
    # Indexes for performance
    __table_args__ = (
        Index('idx_stock_symbol', 'symbol'),
        Index('idx_stock_sector', 'sector_id'),
        Index('idx_stock_active', 'is_active'),
        Index('idx_stock_tradeable', 'is_tradeable'),
        Index('idx_stock_beta', 'beta'),
        Index('idx_stock_volume', 'avg_volume_30d'),
        Index('idx_stock_filters', 'passes_volume_filter', 'passes_beta_filter', 'passes_volatility_filter'),
        Index('idx_stock_updated', 'updated_at'),
    )
    
    def __repr__(self):
        return f"<Stock(id={self.id}, symbol='{self.symbol}', ltp={self.ltp})>"
    
    def to_dict(self, include_relationships=False):
        """Convert model to dictionary"""
        data = {
            "id": self.id,
            "symbol": self.symbol,
            "name": self.name,
            "sector_id": self.sector_id,
            "is_active": self.is_active,
            "is_tradeable": self.is_tradeable,
            "listing_date": self.listing_date.isoformat() if self.listing_date else None,
            "ltp": self.ltp,
            "previous_close": self.previous_close,
            "open_price": self.open_price,
            "high_price": self.high_price,
            "low_price": self.low_price,
            "change": self.change,
            "change_percent": self.change_percent,
            "volume": self.volume,
            "turnover": self.turnover,
            "total_trades": self.total_trades,
            "week_52_high": self.week_52_high,
            "week_52_low": self.week_52_low,
            "week_52_high_date": self.week_52_high_date.isoformat() if self.week_52_high_date else None,
            "week_52_low_date": self.week_52_low_date.isoformat() if self.week_52_low_date else None,
            "market_cap": self.market_cap,
            "outstanding_shares": self.outstanding_shares,
            "free_float": self.free_float,
            "eps": self.eps,
            "pe_ratio": self.pe_ratio,
            "book_value": self.book_value,
            "pb_ratio": self.pb_ratio,
            "roe": self.roe,
            "dividend_yield": self.dividend_yield,
            "beta": self.beta,
            "volatility": self.volatility,
            "avg_volume_10d": self.avg_volume_10d,
            "avg_volume_30d": self.avg_volume_30d,
            "sma_20": self.sma_20,
            "sma_50": self.sma_50,
            "sma_200": self.sma_200,
            "ema_9": self.ema_9,
            "ema_21": self.ema_21,
            "rsi_14": self.rsi_14,
            "macd": self.macd,
            "macd_signal": self.macd_signal,
            "macd_histogram": self.macd_histogram,
            "atr_14": self.atr_14,
            "bollinger_upper": self.bollinger_upper,
            "bollinger_middle": self.bollinger_middle,
            "bollinger_lower": self.bollinger_lower,
            "support_1": self.support_1,
            "support_2": self.support_2,
            "support_3": self.support_3,
            "resistance_1": self.resistance_1,
            "resistance_2": self.resistance_2,
            "resistance_3": self.resistance_3,
            "passes_volume_filter": self.passes_volume_filter,
            "passes_beta_filter": self.passes_beta_filter,
            "passes_volatility_filter": self.passes_volatility_filter,
            "in_bullish_sector": self.in_bullish_sector,
            "last_traded_date": self.last_traded_date.isoformat() if self.last_traded_date else None,
            "indicators_updated_at": self.indicators_updated_at.isoformat() if self.indicators_updated_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }
        
        if include_relationships and self.sector:
            data["sector"] = self.sector.to_dict()
        
        return data
