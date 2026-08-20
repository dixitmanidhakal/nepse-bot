"""
Paper Trade Model
=================
Represents a single paper (simulated) trade opened by a bot.

Lifecycle:
    OPEN → bot enters a position at entry_price
    MONITORING → each bot cycle checks if target or stop is hit
    CLOSED → trade resolved (WIN / LOSS / TIMEOUT)

The resolved trade is then fed back to the RL engine so the bot can learn.
"""

import enum
from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Float, Boolean,
    DateTime, JSON, Text, Enum as SAEnum, Index,
)
from sqlalchemy.sql import func

from app.database import Base


class TradeOutcome(str, enum.Enum):
    WIN     = "WIN"
    LOSS    = "LOSS"
    TIMEOUT = "TIMEOUT"   # closed after max_hold_days without hitting target/stop
    OPEN    = "OPEN"      # still active


class TradeDirection(str, enum.Enum):
    LONG  = "LONG"
    SHORT = "SHORT"   # NEPSE is long-only for now, kept for future


class PaperTrade(Base):
    """One paper trade issued by a bot."""

    __tablename__ = "paper_trades"

    id              = Column(Integer, primary_key=True, index=True)

    # Which bot opened this trade
    bot_id          = Column(String(50), nullable=False, index=True)
    bot_name        = Column(String(100), nullable=False)
    strategy        = Column(String(50), nullable=False, index=True)

    # Trade details
    symbol          = Column(String(20), nullable=False, index=True)
    direction       = Column(SAEnum(TradeDirection), default=TradeDirection.LONG, nullable=False)

    entry_price     = Column(Float, nullable=False)
    target_price    = Column(Float, nullable=False)   # take-profit level
    stop_price      = Column(Float, nullable=False)   # stop-loss level
    entry_date      = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Money management — NPR position sizing
    capital_allocated = Column(Float, nullable=True)   # NPR amount placed in this trade
    shares_qty        = Column(Integer, nullable=True)  # number of shares bought
    timeframe         = Column(String(10), default="daily", nullable=True)  # daily / weekly / monthly

    # Resolution fields (filled when trade is closed)
    close_price     = Column(Float, nullable=True)
    close_date      = Column(DateTime(timezone=True), nullable=True)
    outcome         = Column(SAEnum(TradeOutcome), default=TradeOutcome.OPEN, nullable=False, index=True)
    pnl_pct         = Column(Float, nullable=True)    # (close-entry)/entry * 100
    pnl_nrs         = Column(Float, nullable=True)    # P&L in Nepalese Rupees
    is_open         = Column(Boolean, default=True, nullable=False, index=True)

    # Signal metadata that triggered this trade (for RL post-mortem)
    signal_score    = Column(Float, nullable=False)   # 0-100 signal confidence
    signal_context  = Column(JSON, nullable=True)     # full signal dict (zone, trend, factors…)

    # RL learning fields (written after trade closes)
    mistake_analysis = Column(Text, nullable=True)    # what the bot learned from this trade
    regime_at_entry  = Column(String(30), nullable=True)   # "trending" / "sideways" / "volatile"
    sector           = Column(String(100), nullable=True)

    # Max hold period (default 10 trading days; configurable per bot)
    max_hold_days   = Column(Integer, default=10, nullable=False)

    created_at      = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at      = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_paper_trades_bot_open", "bot_id", "is_open"),
        Index("ix_paper_trades_strategy_outcome", "strategy", "outcome"),
    )

    def to_dict(self) -> dict:
        return {
            "id":                 self.id,
            "bot_id":             self.bot_id,
            "bot_name":           self.bot_name,
            "strategy":           self.strategy,
            "symbol":             self.symbol,
            "direction":          self.direction,
            "entry_price":        self.entry_price,
            "target_price":       self.target_price,
            "stop_price":         self.stop_price,
            "entry_date":         self.entry_date.isoformat() if self.entry_date else None,
            "close_price":        self.close_price,
            "close_date":         self.close_date.isoformat() if self.close_date else None,
            "outcome":            self.outcome,
            "pnl_pct":            round(self.pnl_pct, 2) if self.pnl_pct is not None else None,
            "pnl_nrs":            round(self.pnl_nrs, 0) if self.pnl_nrs is not None else None,
            "is_open":            self.is_open,
            "signal_score":       self.signal_score,
            "signal_context":     self.signal_context,
            "mistake_analysis":   self.mistake_analysis,
            "regime_at_entry":    self.regime_at_entry,
            "sector":             self.sector,
            "max_hold_days":      self.max_hold_days,
            "capital_allocated":  self.capital_allocated,
            "shares_qty":         self.shares_qty,
            "timeframe":          self.timeframe or "daily",
            "created_at":         self.created_at.isoformat() if self.created_at else None,
        }
