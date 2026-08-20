"""
Shared signal taxonomy (ported from quant_pro.alpha_practical).

Every quant component that produces an :class:`AlphaSignal` imports its
types from here so the whole pipeline speaks the same language.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class SignalType(Enum):
    """Types of alpha signals supported by the system."""
    CORPORATE_ACTION        = "corporate_action"
    FUNDAMENTAL             = "fundamental"
    MOMENTUM                = "momentum"
    MEAN_REVERSION          = "mean_reversion"
    DIVIDEND                = "dividend"
    LIQUIDITY               = "liquidity"
    SENTIMENT               = "sentiment"
    QUARTERLY_FUNDAMENTAL   = "quarterly_fundamental"
    XSEC_MOMENTUM           = "xsec_momentum"
    ACCUMULATION            = "accumulation"
    DISPOSITION             = "disposition"
    RESIDUAL_MOMENTUM       = "residual_momentum"
    LEAD_LAG                = "lead_lag"
    ANCHORING_52WK          = "anchoring_52wk"
    INFORMED_TRADING        = "informed_trading"
    PAIRS_TRADE             = "pairs_trade"
    EARNINGS_DRIFT          = "earnings_drift"
    MACRO_REMITTANCE        = "macro_remittance"
    SATELLITE_HYDRO         = "satellite_hydro"
    NLP_SENTIMENT           = "nlp_sentiment"
    SETTLEMENT_PRESSURE     = "settlement_pressure"
    VALUE_BOUNCE            = "value_bounce"


@dataclass
class AlphaSignal:
    """A single alpha signal for a symbol."""
    symbol:           str
    signal_type:      SignalType
    direction:        int                       # +1 long, 0 neutral, -1 short
    strength:         float                     # [0, 1]
    confidence:       float                     # [0, 1]
    reasoning:        str
    expires:          Optional[datetime] = None
    target_exit_date: Optional[datetime] = None

    @property
    def score(self) -> float:
        return self.direction * self.strength * self.confidence

    def to_dict(self) -> dict:
        """Serialise for JSON API responses."""
        return {
            "symbol":           self.symbol,
            "signal_type":      self.signal_type.value,
            "direction":        self.direction,
            "strength":         self.strength,
            "confidence":       self.confidence,
            "score":            self.score,
            "reasoning":        self.reasoning,
            "expires":          self.expires.isoformat() if self.expires else None,
            "target_exit_date": (
                self.target_exit_date.isoformat()
                if self.target_exit_date else None
            ),
        }
