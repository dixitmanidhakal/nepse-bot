"""
Live Market Cache
=================
Stores the latest scraped live market snapshot per symbol in PostgreSQL.

One row per symbol — upserted on every scraper cycle.
Used by bots to check current prices without making HTTP calls during the
trade cycle.

Scraper writes here every 5 minutes during NEPSE hours from 4 rotating
sources (merolagani → nepsealpha → sharesansar → yonepse).
"""
from sqlalchemy import Column, Integer, String, Float, DateTime, Index
from sqlalchemy.sql import func

from app.database import Base


class LiveMarketCache(Base):
    __tablename__ = "live_market_cache"

    id             = Column(Integer, primary_key=True)
    symbol         = Column(String(20), nullable=False, unique=True, index=True)

    # Price snapshot
    ltp            = Column(Float, nullable=True)   # last traded price
    open_price     = Column(Float, nullable=True)
    high_price     = Column(Float, nullable=True)
    low_price      = Column(Float, nullable=True)
    previous_close = Column(Float, nullable=True)
    percent_change = Column(Float, nullable=True)
    volume         = Column(Float, nullable=True)
    turnover       = Column(Float, nullable=True)

    # Meta
    source         = Column(String(40), nullable=True)  # "merolagani" | "nepsealpha" | "sharesansar" | "yonepse"
    scraped_at     = Column(DateTime(timezone=True), nullable=False)
    created_at     = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at     = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_lmc_scraped_at", "scraped_at"),
    )

    def __repr__(self):
        return f"<LiveMarketCache({self.symbol} ltp={self.ltp} src={self.source})>"
