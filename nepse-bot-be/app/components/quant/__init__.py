"""
Quant math components ported from nepse-quant-terminal.

Exports:
    regime        - HMM regime + BOCPD change-point detectors
    market_state  - aggregate market regime scanner
    pairs         - cointegration-based pairs trading
    portfolio     - HRP / CVaR / shrinkage portfolio allocators
    conformal     - split-conformal VaR & position-scaling
    signals       - canonicalisation + ranking of raw signal candidates
    disposition   - capital-gains-overhang disposition-effect signals
    signal_types  - shared AlphaSignal dataclass + SignalType enum
"""
from app.components.quant import (  # noqa: F401
    conformal,
    disposition,
    market_state,
    pairs,
    portfolio,
    regime,
    signal_types,
    signals,
)

__all__ = [
    "conformal",
    "disposition",
    "market_state",
    "pairs",
    "portfolio",
    "regime",
    "signal_types",
    "signals",
]
