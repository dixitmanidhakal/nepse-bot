"""
Position Sizing & NEPSE Transaction-Cost Helpers

Adapted from nepse-quant-terminal (MIT) with all internal imports inlined
so this module stands on its own inside the nepse-bot backend.

Features:
    - Tiered NEPSE broker commission + SEBON fee + DP charge
    - Kelly-fraction calculator (half-Kelly, capped)
    - Portfolio sizer with max-per-position, max-per-sector, cash-reserve rules
    - Round-trip cost estimator
    - No-trade-zone gate that compares turnover to expected cost
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List, Optional


@dataclass
class Position:
    symbol: str
    shares: int
    weight: float
    value: float
    sector: str
    signal_type: str
    confidence: float

    def as_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# NEPSE transaction costs                                                      #
# --------------------------------------------------------------------------- #

BROKER_COMMISSION_TIERS = [
    (2_500, 10.0),         # Flat Rs 10 below 2 500
    (50_000, 0.0036),
    (500_000, 0.0033),
    (2_000_000, 0.0031),
    (10_000_000, 0.0027),
    (float("inf"), 0.0024),
]
SEBON_FEE = 0.00015
DP_CHARGE = 25.0
NO_TRADE_UTILITY_THRESHOLD = 0.05  # 5% turnover before trading is worth it


def calculate_transaction_cost(amount: float, is_buy: bool = True) -> float:
    """Calculate one-leg NEPSE transaction cost in NPR."""
    if amount <= 0:
        return 0.0

    commission = amount * 0.0024
    for threshold, rate in BROKER_COMMISSION_TIERS:
        if amount <= threshold:
            if isinstance(rate, float) and rate < 1:
                commission = amount * rate
            else:
                commission = float(rate)
            break

    sebon = amount * SEBON_FEE
    dp = DP_CHARGE
    return commission + sebon + dp


# --------------------------------------------------------------------------- #
# Sector lookup                                                                #
# --------------------------------------------------------------------------- #

# Minimal built-in sector map — the bot already has its own sector model in
# the DB, so this is just a fallback when the caller doesn't pass one.
DEFAULT_SECTOR_GROUPS: Dict[str, List[str]] = {
    "Commercial Banks": [],
    "Development Banks": [],
    "Finance": [],
    "Microfinance": [],
    "Life Insurance": [],
    "Non-Life Insurance": [],
    "Hydropower": [],
    "Hotels": [],
    "Manufacturing": [],
    "Trading": [],
    "Others": [],
}


def get_symbol_sector(
    symbol: str,
    sector_groups: Optional[Dict[str, List[str]]] = None,
) -> str:
    """Look up sector for a symbol; returns 'Others' if unknown."""
    groups = sector_groups or DEFAULT_SECTOR_GROUPS
    for sector, symbols in groups.items():
        if symbol in symbols:
            return sector
    return "Others"


# --------------------------------------------------------------------------- #
# Kelly fraction                                                               #
# --------------------------------------------------------------------------- #

def calculate_kelly_fraction(
    win_prob: float,
    avg_win: float,
    avg_loss: float,
    max_kelly: float = 0.25,
) -> float:
    """
    Half-Kelly position size, capped at max_kelly.

        f* = (p * b - q) / b    (full Kelly)
        result = clamp(f* / 2, 0, max_kelly)
    """
    if avg_loss == 0 or avg_win <= 0:
        return 0.0
    q = 1.0 - win_prob
    b = avg_win / avg_loss
    kelly = (win_prob * b - q) / b
    half_kelly = kelly / 2.0
    return max(0.0, min(half_kelly, max_kelly))


# --------------------------------------------------------------------------- #
# Portfolio sizer                                                              #
# --------------------------------------------------------------------------- #

def size_positions(
    signals: List[Dict],
    capital: float,
    prices: Dict[str, float],
    max_positions: int = 7,
    max_single_pct: float = 0.15,
    max_sector_pct: float = 0.35,
    cash_reserve_pct: float = 0.20,
    sector_groups: Optional[Dict[str, List[str]]] = None,
) -> List[Position]:
    """
    Convert scored signals to concrete positions under risk limits.

    Each signal dict must have at least:
        - "symbol"
        - "strength"   (0-1)  OR "score"
        - "confidence" (0-1)  — optional, defaults to 0.5
        - "signal_type" — optional, defaults to "unknown"
    """
    if not signals or capital <= 0:
        return []

    deployable = capital * (1.0 - cash_reserve_pct)

    def _rank_key(sig: Dict) -> float:
        strength = sig.get("strength", sig.get("score", 0.0))
        return float(strength) * float(sig.get("confidence", 0.5))

    sorted_signals = sorted(signals, key=_rank_key, reverse=True)

    positions: List[Position] = []
    sector_allocations: Dict[str, float] = {}
    total_allocated = 0.0

    for sig in sorted_signals:
        if len(positions) >= max_positions:
            break

        symbol = sig.get("symbol")
        if not symbol or symbol not in prices or prices[symbol] <= 0:
            continue

        price = float(prices[symbol])
        sector = sig.get("sector") or get_symbol_sector(symbol, sector_groups)

        strength = float(sig.get("strength", sig.get("score", 0.5)))
        confidence = float(sig.get("confidence", 0.5))
        raw_weight = strength * confidence
        weight = min(raw_weight, max_single_pct)

        current_sector = sector_allocations.get(sector, 0.0)
        if current_sector + weight > max_sector_pct:
            weight = max(0.0, max_sector_pct - current_sector)

        if weight < 0.03:
            continue
        if total_allocated + weight > 1.0:
            weight = max(0.0, 1.0 - total_allocated)
        if weight < 0.03:
            continue

        value = deployable * weight
        shares = int(value / price)
        if shares <= 0:
            continue

        actual_value = shares * price
        actual_weight = actual_value / capital

        positions.append(Position(
            symbol=symbol,
            shares=shares,
            weight=actual_weight,
            value=actual_value,
            sector=sector,
            signal_type=sig.get("signal_type", "unknown"),
            confidence=confidence,
        ))
        sector_allocations[sector] = current_sector + actual_weight
        total_allocated += actual_weight

    return positions


def estimate_round_trip_cost(positions: List[Position]) -> float:
    """Sum of buy + sell transaction costs across all positions."""
    total = 0.0
    for pos in positions:
        total += calculate_transaction_cost(pos.value, is_buy=True)
        total += calculate_transaction_cost(pos.value, is_buy=False)
    return total


def should_rebalance(
    current_positions: Dict[str, float],
    proposed_positions: Dict[str, float],
    transaction_cost_rate: float = 0.006,
    no_trade_threshold: float = NO_TRADE_UTILITY_THRESHOLD,
) -> bool:
    """
    Decide whether rebalancing is worth the friction.

    Returns True iff total turnover > no_trade_threshold + expected cost.
    """
    all_symbols = set(current_positions) | set(proposed_positions)
    total_turnover = sum(
        abs(proposed_positions.get(s, 0.0) - current_positions.get(s, 0.0))
        for s in all_symbols
    )
    expected_cost = total_turnover * transaction_cost_rate
    return total_turnover > no_trade_threshold + expected_cost


__all__ = [
    "Position",
    "BROKER_COMMISSION_TIERS",
    "SEBON_FEE",
    "DP_CHARGE",
    "NO_TRADE_UTILITY_THRESHOLD",
    "calculate_transaction_cost",
    "get_symbol_sector",
    "calculate_kelly_fraction",
    "size_positions",
    "estimate_round_trip_cost",
    "should_rebalance",
]
