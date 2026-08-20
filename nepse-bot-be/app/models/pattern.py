"""
Pattern Model

This model stores detected chart patterns and technical formations.
Used for pattern-based trading strategies and breakout detection.
"""

from sqlalchemy import Column, Integer, String, Float, DateTime, Date, ForeignKey, Index, Enum as SQLEnum
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import enum

from app.database import Base


class PatternType(enum.Enum):
    """Pattern type enumeration"""
    SUPPORT = "SUPPORT"
    RESISTANCE = "RESISTANCE"
    TREND_LINE = "TREND_LINE"
    DOUBLE_TOP = "DOUBLE_TOP"
    DOUBLE_BOTTOM = "DOUBLE_BOTTOM"
    HEAD_SHOULDERS = "HEAD_SHOULDERS"
    INVERSE_HEAD_SHOULDERS = "INVERSE_HEAD_SHOULDERS"
    TRIANGLE_ASCENDING = "TRIANGLE_ASCENDING"
    TRIANGLE_DESCENDING = "TRIANGLE_DESCENDING"
    TRIANGLE_SYMMETRICAL = "TRIANGLE_SYMMETRICAL"
    FLAG_BULLISH = "FLAG_BULLISH"
    FLAG_BEARISH = "FLAG_BEARISH"
    PENNANT = "PENNANT"
    WEDGE_RISING = "WEDGE_RISING"
    WEDGE_FALLING = "WEDGE_FALLING"
    CHANNEL_ASCENDING = "CHANNEL_ASCENDING"
    CHANNEL_DESCENDING = "CHANNEL_DESCENDING"
    BREAKOUT_BULLISH = "BREAKOUT_BULLISH"
    BREAKOUT_BEARISH = "BREAKOUT_BEARISH"


class PatternStatus(enum.Enum):
    """Pattern status enumeration"""
    FORMING = "FORMING"
    CONFIRMED = "CONFIRMED"
    BROKEN = "BROKEN"
    COMPLETED = "COMPLETED"


class Pattern(Base):
    """
    Pattern Model
    
    Stores detected chart patterns and technical formations.
    Tracks pattern formation, confirmation, and breakouts.
    """
    
    __tablename__ = "patterns"
    
    # Primary Key
    id = Column(Integer, primary_key=True, index=True)
    
    # Foreign Key
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Pattern Information
    pattern_type = Column(SQLEnum(PatternType), nullable=False, index=True)
    status = Column(SQLEnum(PatternStatus), default=PatternStatus.FORMING, nullable=False, index=True)
    
    # Pattern Details
    pattern_name = Column(String(100), nullable=False)
    description = Column(String(500), nullable=True)
    
    # Price Levels
    level_1 = Column(Float, nullable=True)  # Primary level (support/resistance)
    level_2 = Column(Float, nullable=True)  # Secondary level
    level_3 = Column(Float, nullable=True)  # Tertiary level
    
    # Pattern Metrics
    strength = Column(Float, nullable=True)  # Pattern strength (0-1)
    touches = Column(Integer, nullable=True)  # Number of touches
    duration_days = Column(Integer, nullable=True)  # Pattern duration
    
    # Breakout Information
    breakout_price = Column(Float, nullable=True)
    breakout_date = Column(Date, nullable=True)
    breakout_volume = Column(Float, nullable=True)
    volume_confirmation = Column(Integer, default=False, nullable=False)
    
    # Target Levels
    target_1 = Column(Float, nullable=True)  # First target
    target_2 = Column(Float, nullable=True)  # Second target
    target_3 = Column(Float, nullable=True)  # Third target
    
    # Risk Level
    invalidation_level = Column(Float, nullable=True)  # Pattern invalidation price
    
    # Timeframe
    timeframe = Column(String(20), nullable=True)  # Daily, Weekly, etc.
    
    # Detection Details
    first_detected = Column(Date, nullable=False, index=True)
    last_updated = Column(Date, nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Relationships
    stock = relationship("Stock", back_populates="patterns")
    
    # Indexes for performance
    __table_args__ = (
        Index('idx_pattern_stock_id', 'stock_id'),
        Index('idx_pattern_type', 'pattern_type'),
        Index('idx_pattern_status', 'status'),
        Index('idx_pattern_detected', 'first_detected'),
        Index('idx_pattern_stock_type', 'stock_id', 'pattern_type'),
        Index('idx_pattern_active', 'status', 'first_detected'),
    )
    
    def __repr__(self):
        return f"<Pattern(id={self.id}, stock_id={self.stock_id}, type={self.pattern_type}, status={self.status})>"
    
    def to_dict(self, include_stock=False):
        """Convert model to dictionary"""
        data = {
            "id": self.id,
            "stock_id": self.stock_id,
            "pattern_type": self.pattern_type.value if self.pattern_type else None,
            "status": self.status.value if self.status else None,
            "pattern_name": self.pattern_name,
            "description": self.description,
            "level_1": self.level_1,
            "level_2": self.level_2,
            "level_3": self.level_3,
            "strength": self.strength,
            "touches": self.touches,
            "duration_days": self.duration_days,
            "breakout_price": self.breakout_price,
            "breakout_date": self.breakout_date.isoformat() if self.breakout_date else None,
            "breakout_volume": self.breakout_volume,
            "volume_confirmation": self.volume_confirmation,
            "target_1": self.target_1,
            "target_2": self.target_2,
            "target_3": self.target_3,
            "invalidation_level": self.invalidation_level,
            "timeframe": self.timeframe,
            "first_detected": self.first_detected.isoformat() if self.first_detected else None,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
            "confirmed_at": self.confirmed_at.isoformat() if self.confirmed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }
        
        if include_stock and self.stock:
            data["stock"] = {
                "symbol": self.stock.symbol,
                "name": self.stock.name
            }
        
        return data
