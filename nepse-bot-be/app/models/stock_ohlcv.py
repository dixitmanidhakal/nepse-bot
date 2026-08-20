"""
Stock OHLCV Model

This model stores historical OHLCV (Open, High, Low, Close, Volume) data for stocks.
Used for technical analysis, pattern detection, and indicator calculations.
"""

from sqlalchemy import Column, Integer, String, Float, Date, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.database import Base


class StockOHLCV(Base):
    """
    Stock OHLCV Model
    
    Stores daily OHLCV data for stocks.
    Essential for technical analysis and backtesting.
    """
    
    __tablename__ = "stock_ohlcv"
    
    # Primary Key
    id = Column(Integer, primary_key=True, index=True)
    
    # Foreign Key
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Date
    date = Column(Date, nullable=False, index=True)
    
    # OHLCV Data
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)
    
    # Additional Market Data
    turnover = Column(Float, nullable=True)  # Total turnover
    total_trades = Column(Integer, nullable=True)  # Number of trades
    
    # Adjusted Prices (for splits, dividends)
    adjusted_close = Column(Float, nullable=True)
    
    # Calculated Fields
    change = Column(Float, nullable=True)  # Absolute change from previous close
    change_percent = Column(Float, nullable=True)  # Percentage change
    
    # Volume Analysis
    volume_ratio = Column(Float, nullable=True)  # Volume / Average Volume
    
    # Price Action
    body_size = Column(Float, nullable=True)  # |close - open|
    upper_shadow = Column(Float, nullable=True)  # high - max(open, close)
    lower_shadow = Column(Float, nullable=True)  # min(open, close) - low
    candle_range = Column(Float, nullable=True)  # high - low
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Relationships
    stock = relationship("Stock", back_populates="ohlcv_data")
    
    # Constraints and Indexes
    __table_args__ = (
        UniqueConstraint('stock_id', 'date', name='uq_stock_date'),
        Index('idx_ohlcv_stock_id', 'stock_id'),
        Index('idx_ohlcv_date', 'date'),
        Index('idx_ohlcv_stock_date', 'stock_id', 'date'),
        Index('idx_ohlcv_volume', 'volume'),
    )
    
    def __repr__(self):
        return f"<StockOHLCV(stock_id={self.stock_id}, date={self.date}, close={self.close})>"
    
    def to_dict(self, include_stock=False):
        """Convert model to dictionary"""
        data = {
            "id": self.id,
            "stock_id": self.stock_id,
            "date": self.date.isoformat() if self.date else None,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "turnover": self.turnover,
            "total_trades": self.total_trades,
            "adjusted_close": self.adjusted_close,
            "change": self.change,
            "change_percent": self.change_percent,
            "volume_ratio": self.volume_ratio,
            "body_size": self.body_size,
            "upper_shadow": self.upper_shadow,
            "lower_shadow": self.lower_shadow,
            "candle_range": self.candle_range,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }
        
        if include_stock and self.stock:
            data["stock"] = {
                "symbol": self.stock.symbol,
                "name": self.stock.name
            }
        
        return data
