"""
Bot Configuration Model

This model stores all configuration parameters for trading bots.
Each bot can have different settings for its strategy components.
"""

from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, JSON, Text
from sqlalchemy.sql import func
from datetime import datetime

from app.database import Base


class BotConfiguration(Base):
    """
    Bot Configuration Model
    
    Stores configuration parameters for trading bots.
    Supports multiple bot instances with different strategies.
    """
    
    __tablename__ = "bot_configurations"
    
    # Primary Key
    id = Column(Integer, primary_key=True, index=True)
    
    # Basic Information
    name = Column(String(100), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    
    # Component Enable/Disable Flags
    sector_identifier_enabled = Column(Boolean, default=True, nullable=False)
    liquidity_hunter_enabled = Column(Boolean, default=True, nullable=False)
    market_depth_enabled = Column(Boolean, default=True, nullable=False)
    floorsheet_enabled = Column(Boolean, default=True, nullable=False)
    fundamental_enabled = Column(Boolean, default=False, nullable=False)
    
    # Sector Identifier Configuration
    sector_comparison_days = Column(Integer, default=30, nullable=False)  # Days for sector comparison
    sector_momentum_threshold = Column(Float, default=0.05, nullable=False)  # 5% momentum threshold
    
    # Stock Screening Configuration
    min_beta = Column(Float, default=0.8, nullable=False)  # Minimum beta
    max_beta = Column(Float, default=1.5, nullable=False)  # Maximum beta
    volume_days = Column(Integer, default=10, nullable=False)  # Days for volume analysis
    volume_threshold = Column(Float, default=1.5, nullable=False)  # Volume multiplier
    min_volatility = Column(Float, default=0.01, nullable=False)  # Minimum volatility
    max_volatility = Column(Float, default=0.05, nullable=False)  # Maximum volatility
    
    # Liquidity Hunter Configuration
    demand_zone_lookback = Column(Integer, default=20, nullable=False)  # Bars to look back
    volume_spike_threshold = Column(Float, default=2.0, nullable=False)  # Volume spike multiplier
    rsi_oversold = Column(Float, default=30.0, nullable=False)  # RSI oversold level
    rsi_overbought = Column(Float, default=70.0, nullable=False)  # RSI overbought level
    
    # Pattern Detection Configuration
    support_resistance_strength = Column(Integer, default=3, nullable=False)  # Min touches
    breakout_volume_threshold = Column(Float, default=1.5, nullable=False)  # Volume for breakout
    pattern_lookback_days = Column(Integer, default=60, nullable=False)  # Days for pattern detection
    
    # Market Depth Configuration
    order_imbalance_threshold = Column(Float, default=0.3, nullable=False)  # 30% imbalance
    bid_wall_threshold = Column(Float, default=100000.0, nullable=False)  # Min quantity for wall
    liquidity_score_threshold = Column(Float, default=0.6, nullable=False)  # Min liquidity score
    
    # Floorsheet Configuration
    broker_accumulation_days = Column(Integer, default=5, nullable=False)  # Days to track
    broker_volume_threshold = Column(Float, default=50000.0, nullable=False)  # Min volume
    manipulation_detection_enabled = Column(Boolean, default=True, nullable=False)
    
    # Risk Management Configuration
    risk_per_trade = Column(Float, default=1.0, nullable=False)  # % of capital
    max_risk_per_trade = Column(Float, default=2.0, nullable=False)  # Max % of capital
    reward_risk_ratio = Column(Float, default=2.0, nullable=False)  # R:R ratio
    max_open_positions = Column(Integer, default=5, nullable=False)  # Max concurrent positions
    
    # Technical Indicator Configuration
    ema_short_period = Column(Integer, default=9, nullable=False)
    ema_long_period = Column(Integer, default=21, nullable=False)
    rsi_period = Column(Integer, default=14, nullable=False)
    macd_fast = Column(Integer, default=12, nullable=False)
    macd_slow = Column(Integer, default=26, nullable=False)
    macd_signal = Column(Integer, default=9, nullable=False)
    bollinger_period = Column(Integer, default=20, nullable=False)
    bollinger_std = Column(Float, default=2.0, nullable=False)
    atr_period = Column(Integer, default=14, nullable=False)
    
    # Signal Generation Configuration
    min_confidence_score = Column(Float, default=0.6, nullable=False)  # Min confidence to generate signal
    component_weights = Column(JSON, nullable=True)  # Weights for each component
    
    # Scheduling Configuration
    market_data_interval = Column(Integer, default=5, nullable=False)  # Minutes
    analysis_interval = Column(Integer, default=15, nullable=False)  # Minutes
    signal_generation_interval = Column(Integer, default=15, nullable=False)  # Minutes
    
    # Additional Configuration (JSON for flexibility)
    additional_config = Column(JSON, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    def __repr__(self):
        return f"<BotConfiguration(id={self.id}, name='{self.name}', active={self.is_active})>"
    
    def to_dict(self):
        """Convert model to dictionary"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "is_active": self.is_active,
            "sector_identifier_enabled": self.sector_identifier_enabled,
            "liquidity_hunter_enabled": self.liquidity_hunter_enabled,
            "market_depth_enabled": self.market_depth_enabled,
            "floorsheet_enabled": self.floorsheet_enabled,
            "fundamental_enabled": self.fundamental_enabled,
            "sector_comparison_days": self.sector_comparison_days,
            "sector_momentum_threshold": self.sector_momentum_threshold,
            "min_beta": self.min_beta,
            "max_beta": self.max_beta,
            "volume_days": self.volume_days,
            "volume_threshold": self.volume_threshold,
            "min_volatility": self.min_volatility,
            "max_volatility": self.max_volatility,
            "demand_zone_lookback": self.demand_zone_lookback,
            "volume_spike_threshold": self.volume_spike_threshold,
            "rsi_oversold": self.rsi_oversold,
            "rsi_overbought": self.rsi_overbought,
            "support_resistance_strength": self.support_resistance_strength,
            "breakout_volume_threshold": self.breakout_volume_threshold,
            "pattern_lookback_days": self.pattern_lookback_days,
            "order_imbalance_threshold": self.order_imbalance_threshold,
            "bid_wall_threshold": self.bid_wall_threshold,
            "liquidity_score_threshold": self.liquidity_score_threshold,
            "broker_accumulation_days": self.broker_accumulation_days,
            "broker_volume_threshold": self.broker_volume_threshold,
            "manipulation_detection_enabled": self.manipulation_detection_enabled,
            "risk_per_trade": self.risk_per_trade,
            "max_risk_per_trade": self.max_risk_per_trade,
            "reward_risk_ratio": self.reward_risk_ratio,
            "max_open_positions": self.max_open_positions,
            "ema_short_period": self.ema_short_period,
            "ema_long_period": self.ema_long_period,
            "rsi_period": self.rsi_period,
            "macd_fast": self.macd_fast,
            "macd_slow": self.macd_slow,
            "macd_signal": self.macd_signal,
            "bollinger_period": self.bollinger_period,
            "bollinger_std": self.bollinger_std,
            "atr_period": self.atr_period,
            "min_confidence_score": self.min_confidence_score,
            "component_weights": self.component_weights,
            "market_data_interval": self.market_data_interval,
            "analysis_interval": self.analysis_interval,
            "signal_generation_interval": self.signal_generation_interval,
            "additional_config": self.additional_config,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }
