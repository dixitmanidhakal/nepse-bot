"""
Sector Model

This model stores sector information and performance metrics.
Used for sector rotation analysis and sector-based stock screening.
"""

from sqlalchemy import Column, Integer, String, Float, DateTime, Index
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.database import Base


class Sector(Base):
    """
    Sector Model
    
    Stores sector information and performance metrics.
    Tracks sector indices and relative strength.
    """
    
    __tablename__ = "sectors"
    
    # Primary Key
    id = Column(Integer, primary_key=True, index=True)
    
    # Sector Information
    name = Column(String(100), nullable=False, unique=True, index=True)
    code = Column(String(50), nullable=True, unique=True, index=True)
    description = Column(String(500), nullable=True)
    
    # Current Metrics
    current_index = Column(Float, nullable=True)  # Current sector index value
    previous_close = Column(Float, nullable=True)  # Previous close
    change = Column(Float, nullable=True)  # Absolute change
    change_percent = Column(Float, nullable=True)  # Percentage change
    
    # Performance Metrics
    day_high = Column(Float, nullable=True)
    day_low = Column(Float, nullable=True)
    week_high = Column(Float, nullable=True)
    week_low = Column(Float, nullable=True)
    month_high = Column(Float, nullable=True)
    month_low = Column(Float, nullable=True)
    year_high = Column(Float, nullable=True)
    year_low = Column(Float, nullable=True)
    
    # Momentum Indicators
    momentum_1d = Column(Float, nullable=True)  # 1-day momentum
    momentum_5d = Column(Float, nullable=True)  # 5-day momentum
    momentum_10d = Column(Float, nullable=True)  # 10-day momentum
    momentum_20d = Column(Float, nullable=True)  # 20-day momentum
    momentum_30d = Column(Float, nullable=True)  # 30-day momentum
    
    # Relative Strength (vs NEPSE index)
    relative_strength_1d = Column(Float, nullable=True)
    relative_strength_5d = Column(Float, nullable=True)
    relative_strength_10d = Column(Float, nullable=True)
    relative_strength_20d = Column(Float, nullable=True)
    relative_strength_30d = Column(Float, nullable=True)
    
    # Volume Metrics
    total_volume = Column(Float, nullable=True)  # Total trading volume
    total_turnover = Column(Float, nullable=True)  # Total turnover
    avg_volume_10d = Column(Float, nullable=True)  # 10-day average volume
    avg_volume_30d = Column(Float, nullable=True)  # 30-day average volume
    
    # Market Statistics
    total_stocks = Column(Integer, nullable=True)  # Number of stocks in sector
    advancing_stocks = Column(Integer, nullable=True)  # Stocks with positive change
    declining_stocks = Column(Integer, nullable=True)  # Stocks with negative change
    unchanged_stocks = Column(Integer, nullable=True)  # Stocks with no change
    
    # Ranking
    rank = Column(Integer, nullable=True)  # Sector rank by performance
    rank_change = Column(Integer, nullable=True)  # Change in rank
    
    # Timestamps
    last_updated = Column(DateTime(timezone=True), nullable=True)  # Last data update
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Relationships
    stocks = relationship("Stock", back_populates="sector", cascade="all, delete-orphan")
    
    # Indexes for performance
    __table_args__ = (
        Index('idx_sector_name', 'name'),
        Index('idx_sector_code', 'code'),
        Index('idx_sector_rank', 'rank'),
        Index('idx_sector_momentum', 'momentum_30d'),
        Index('idx_sector_updated', 'last_updated'),
    )
    
    def __repr__(self):
        return f"<Sector(id={self.id}, name='{self.name}', index={self.current_index})>"
    
    def to_dict(self):
        """Convert model to dictionary"""
        return {
            "id": self.id,
            "name": self.name,
            "code": self.code,
            "description": self.description,
            "current_index": self.current_index,
            "previous_close": self.previous_close,
            "change": self.change,
            "change_percent": self.change_percent,
            "day_high": self.day_high,
            "day_low": self.day_low,
            "week_high": self.week_high,
            "week_low": self.week_low,
            "month_high": self.month_high,
            "month_low": self.month_low,
            "year_high": self.year_high,
            "year_low": self.year_low,
            "momentum_1d": self.momentum_1d,
            "momentum_5d": self.momentum_5d,
            "momentum_10d": self.momentum_10d,
            "momentum_20d": self.momentum_20d,
            "momentum_30d": self.momentum_30d,
            "relative_strength_1d": self.relative_strength_1d,
            "relative_strength_5d": self.relative_strength_5d,
            "relative_strength_10d": self.relative_strength_10d,
            "relative_strength_20d": self.relative_strength_20d,
            "relative_strength_30d": self.relative_strength_30d,
            "total_volume": self.total_volume,
            "total_turnover": self.total_turnover,
            "avg_volume_10d": self.avg_volume_10d,
            "avg_volume_30d": self.avg_volume_30d,
            "total_stocks": self.total_stocks,
            "advancing_stocks": self.advancing_stocks,
            "declining_stocks": self.declining_stocks,
            "unchanged_stocks": self.unchanged_stocks,
            "rank": self.rank,
            "rank_change": self.rank_change,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }
