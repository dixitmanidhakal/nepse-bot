"""
Bot Learning State Model
========================
Stores the RL (contextual-bandit) state for each bot.

The RL engine updates this after every closed trade:
- rolling_accuracy    : EMA of win-rate over recent trades
- current_threshold   : minimum signal score required to enter a trade
                        (starts at 80, rises when accuracy drops)
- signal_weights      : JSON map of factor → weight (tuned by RL)
- mistakes_log        : recent learning events (what went wrong & why)
"""

from sqlalchemy import Column, Integer, String, Float, DateTime, JSON, Text
from sqlalchemy.sql import func

from app.database import Base


class BotLearningState(Base):
    __tablename__ = "bot_learning_states"

    id                  = Column(Integer, primary_key=True, index=True)

    # One row per bot
    bot_id              = Column(String(50), nullable=False, unique=True, index=True)
    bot_name            = Column(String(100), nullable=False)
    strategy            = Column(String(50), nullable=False)

    # Performance counters
    total_trades        = Column(Integer, default=0, nullable=False)
    wins                = Column(Integer, default=0, nullable=False)
    losses              = Column(Integer, default=0, nullable=False)
    timeouts            = Column(Integer, default=0, nullable=False)

    # Rolling accuracy (EMA, alpha=0.2 so last ~5 trades dominate)
    rolling_accuracy    = Column(Float, default=1.0, nullable=False)

    # Adaptive entry threshold (80 → 90 when accuracy drops)
    current_threshold   = Column(Float, default=80.0, nullable=False)

    # Factor weights for signal scoring (JSON dict, e.g. {"trend": 0.30, ...})
    signal_weights      = Column(JSON, nullable=True)

    # Sector-level accuracy tracking (JSON: {"Banking": 0.82, "Hydropower": 0.61, ...})
    sector_accuracy     = Column(JSON, nullable=True)

    # Regime accuracy (JSON: {"trending": 0.88, "sideways": 0.55, "volatile": 0.70})
    regime_accuracy     = Column(JSON, nullable=True)

    # Trade counts per sector / regime (used to decide if accuracy estimate is reliable)
    # JSON: {"Banking": 7, "Hydropower": 3}
    sector_counts       = Column(JSON, nullable=True)
    regime_counts       = Column(JSON, nullable=True)

    # Last 20 learning events (circular buffer, newest first)
    mistakes_log        = Column(JSON, nullable=True)

    # Human-readable summary of last learning cycle
    last_lesson         = Column(Text, nullable=True)

    # ── Capital / money management (10 lakhs NPR per bot) ─────────────────
    capital_nrs         = Column(Float, default=1_000_000.0, nullable=True)   # total assigned capital
    capital_deployed    = Column(Float, default=0.0,         nullable=True)   # NPR in open trades
    total_pnl_nrs       = Column(Float, default=0.0,         nullable=True)   # cumulative NPR P&L
    peak_capital_nrs    = Column(Float, default=1_000_000.0, nullable=True)   # high-water mark
    max_drawdown_pct    = Column(Float, default=0.0,         nullable=True)   # worst drawdown %

    last_trade_at       = Column(DateTime(timezone=True), nullable=True)
    created_at          = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at          = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def to_dict(self) -> dict:
        win_rate = (self.wins / self.total_trades * 100) if self.total_trades > 0 else 0.0
        cap_nrs      = self.capital_nrs or 1_000_000.0
        deployed     = self.capital_deployed or 0.0
        pnl_nrs      = self.total_pnl_nrs or 0.0
        peak         = self.peak_capital_nrs or cap_nrs
        current_cap  = cap_nrs + pnl_nrs
        available    = max(0.0, current_cap - deployed)
        return {
            "bot_id":            self.bot_id,
            "bot_name":          self.bot_name,
            "strategy":          self.strategy,
            "total_trades":      self.total_trades,
            "wins":              self.wins,
            "losses":            self.losses,
            "timeouts":          self.timeouts,
            "win_rate_pct":      round(win_rate, 1),
            "rolling_accuracy":  round(self.rolling_accuracy, 3),
            "current_threshold": round(self.current_threshold, 1),
            "signal_weights":    self.signal_weights,
            "sector_accuracy":   self.sector_accuracy,
            "regime_accuracy":   self.regime_accuracy,
            "sector_counts":     self.sector_counts,
            "regime_counts":     self.regime_counts,
            "mistakes_log":      self.mistakes_log or [],
            "last_lesson":       self.last_lesson,
            # Capital / money management
            "capital_nrs":       round(cap_nrs, 0),
            "capital_deployed":  round(deployed, 0),
            "capital_available": round(available, 0),
            "total_pnl_nrs":     round(pnl_nrs, 0),
            "current_capital":   round(current_cap, 0),
            "max_drawdown_pct":  round(self.max_drawdown_pct or 0.0, 2),
            "last_trade_at":     self.last_trade_at.isoformat() if self.last_trade_at else None,
            "updated_at":        self.updated_at.isoformat() if self.updated_at else None,
        }
