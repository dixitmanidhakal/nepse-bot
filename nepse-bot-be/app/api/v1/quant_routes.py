"""
Quant Routes — Regime + Risk / Position Sizing

Exposes the ported quant helpers:
    POST /quant/regime          - classify regime from a close-price series
    POST /quant/regime/returns  - classify regime from a returns series
    POST /quant/size-positions  - size positions from scored signals
    POST /quant/transaction-cost- one-leg NEPSE transaction cost
    POST /quant/kelly           - half-Kelly fraction
"""

from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.indicators import regime as regime_mod
from app.components import position_sizing as psm


router = APIRouter(prefix="/quant", tags=["Quant"])


# --------------------------------------------------------------------------- #
# Regime                                                                       #
# --------------------------------------------------------------------------- #

class RegimeClosesRequest(BaseModel):
    closes: List[float] = Field(..., min_length=2, description="Benchmark close prices, chronological")
    window: int = 60
    bull_threshold: float = 0.05
    bear_threshold: float = -0.05


class RegimeReturnsRequest(BaseModel):
    returns: List[float] = Field(..., min_length=2, description="Daily returns, chronological")
    window: int = 60
    bull_threshold: float = 0.05
    bear_threshold: float = -0.05


@router.post("/regime")
def classify_regime(req: RegimeClosesRequest):
    try:
        result = regime_mod.classify_regime(
            closes=req.closes,
            window=req.window,
            bull_threshold=req.bull_threshold,
            bear_threshold=req.bear_threshold,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "success", "data": result.as_dict()}


@router.post("/regime/returns")
def classify_regime_returns(req: RegimeReturnsRequest):
    try:
        result = regime_mod.classify_regime_from_returns(
            returns=req.returns,
            window=req.window,
            bull_threshold=req.bull_threshold,
            bear_threshold=req.bear_threshold,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "success", "data": result.as_dict()}


# --------------------------------------------------------------------------- #
# Position sizing                                                              #
# --------------------------------------------------------------------------- #

class SignalIn(BaseModel):
    symbol: str
    strength: Optional[float] = None   # 0–1
    score: Optional[float] = None      # 0–1, alternative to strength
    confidence: float = 0.5
    signal_type: str = "unknown"
    sector: Optional[str] = None


class SizePositionsRequest(BaseModel):
    signals: List[SignalIn]
    capital: float = Field(..., gt=0)
    prices: Dict[str, float]
    max_positions: int = 7
    max_single_pct: float = 0.15
    max_sector_pct: float = 0.35
    cash_reserve_pct: float = 0.20


@router.post("/size-positions")
def size_positions(req: SizePositionsRequest):
    signals_dicts: List[Dict] = [s.model_dump(exclude_none=True) for s in req.signals]
    positions = psm.size_positions(
        signals=signals_dicts,
        capital=req.capital,
        prices=req.prices,
        max_positions=req.max_positions,
        max_single_pct=req.max_single_pct,
        max_sector_pct=req.max_sector_pct,
        cash_reserve_pct=req.cash_reserve_pct,
    )
    return {
        "status": "success",
        "data": {
            "positions": [p.as_dict() for p in positions],
            "estimated_round_trip_cost": psm.estimate_round_trip_cost(positions),
        },
    }


# --------------------------------------------------------------------------- #
# Transaction cost & Kelly                                                     #
# --------------------------------------------------------------------------- #

class TransactionCostRequest(BaseModel):
    amount: float = Field(..., gt=0)
    is_buy: bool = True


@router.post("/transaction-cost")
def transaction_cost(req: TransactionCostRequest):
    cost = psm.calculate_transaction_cost(req.amount, is_buy=req.is_buy)
    return {"status": "success", "data": {"amount": req.amount, "cost": cost, "is_buy": req.is_buy}}


class KellyRequest(BaseModel):
    win_prob: float = Field(..., ge=0.0, le=1.0)
    avg_win: float = Field(..., gt=0)
    avg_loss: float = Field(..., gt=0)
    max_kelly: float = 0.25


@router.post("/kelly")
def kelly(req: KellyRequest):
    frac = psm.calculate_kelly_fraction(
        win_prob=req.win_prob,
        avg_win=req.avg_win,
        avg_loss=req.avg_loss,
        max_kelly=req.max_kelly,
    )
    return {"status": "success", "data": {"kelly_fraction": frac}}


# --------------------------------------------------------------------------- #
# Auto-Analysis Snapshot                                                        #
# --------------------------------------------------------------------------- #

@router.get("/snapshot")
def get_quant_snapshot():
    """
    Return the most recent auto-computed quant analysis snapshot.

    The snapshot is refreshed every 30 minutes during NEPSE market hours by the
    APScheduler job (run_quant_snapshot). It includes:
      - HMM regime + probabilities
      - BOCPD changepoint status
      - Composite market state (TRENDING / NEUTRAL / CHOPPY)
      - Conformal 95% VaR
      - Top-10 ranked signals (momentum + mean_reversion + disposition)
      - Kelly fraction from current bot win rate
      - HRP portfolio weights hint
    """
    from app.services.quant_snapshot import get_snapshot
    return get_snapshot()


@router.post("/snapshot/refresh")
def refresh_quant_snapshot():
    """
    Trigger an immediate quant snapshot computation (not gated by market hours).
    Useful for on-demand refresh from the frontend or for testing.
    """
    try:
        from app.services.quant_snapshot import run_snapshot
        run_snapshot()
        from app.services.quant_snapshot import get_snapshot
        return get_snapshot()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
