"""
Advanced Quant Routes — expose modules ported from nepse-quant-terminal.

Routes
------
  POST /quant/advanced/regime-hmm           - Gaussian HMM regime detection
  POST /quant/advanced/regime-bocpd         - Bayesian online changepoint detection
  POST /quant/advanced/market-state         - composite market regime state
  POST /quant/advanced/pairs-spread         - cointegration spread + z-score
  POST /quant/advanced/portfolio-allocate   - HRP / CVaR / shrinkage allocation
  POST /quant/advanced/conformal-var        - split-conformal VaR estimate
  POST /quant/advanced/signals-rank         - rank raw signal candidates
  POST /quant/advanced/disposition          - capital-gains disposition signal

Every endpoint takes raw numeric input (close-price arrays, returns arrays,
prices dataframe as JSON) so it's framework-agnostic — no database calls.
The frontend can mount these directly against its own cached data.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.components.quant import (
    conformal,
    disposition,
    market_state,
    pairs,
    portfolio,
    regime,
    signals,
)
from app.components.quant.regime import detect_regime_numpy_fallback

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/quant/advanced", tags=["Quant — Advanced"])


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────
def _prices_wide_from_json(prices_json: Dict[str, List[float]],
                           dates: Optional[List[str]] = None) -> pd.DataFrame:
    """
    Build a WIDE DataFrame (date × symbol) from column-major JSON.

    Accepted shape:
        {"NABIL": [p1, p2, …], "GBIME": [...], …}
    """
    df = pd.DataFrame(prices_json)
    if df.empty:
        raise ValueError("prices payload is empty")
    if dates:
        df.index = pd.to_datetime(dates)
    else:
        df.index = pd.date_range(
            end=datetime.utcnow().date(), periods=len(df), freq="B"
        )
    return df.sort_index()


def _prices_long_from_json(
    prices_json: Dict[str, List[float]],
    dates: Optional[List[str]] = None,
    volume_json: Optional[Dict[str, List[float]]] = None,
) -> pd.DataFrame:
    """
    Build a LONG-format DataFrame with columns ``symbol``, ``date``, ``close``
    (and ``volume`` if supplied). This is the shape the ported quant modules
    consume (see tests/unit/test_quant_components.py::_synthetic_universe).
    """
    wide = _prices_wide_from_json(prices_json, dates)
    long_frames: List[pd.DataFrame] = []
    for sym in wide.columns:
        sub = pd.DataFrame({
            "symbol": sym,
            "date":   wide.index,
            "close":  wide[sym].values,
            "open":   wide[sym].values,
            "high":   wide[sym].values * 1.01,
            "low":    wide[sym].values * 0.99,
        })
        if volume_json and sym in volume_json:
            vol = list(volume_json[sym])
            if len(vol) != len(sub):
                raise ValueError(
                    f"volume length mismatch for {sym}: got {len(vol)}, "
                    f"expected {len(sub)}"
                )
            sub["volume"] = vol
        else:
            sub["volume"] = 100_000.0
        long_frames.append(sub)
    return pd.concat(long_frames, ignore_index=True)


def _clean_for_json(obj: Any) -> Any:
    """Recursively coerce numpy arrays / scalars / NaN into JSON-safe values."""
    if isinstance(obj, np.ndarray):
        return _clean_for_json(obj.tolist())
    if isinstance(obj, dict):
        return {k: _clean_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean_for_json(v) for v in obj]
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float) and (np.isnan(obj) or np.isinf(obj)):
        return None
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    return obj


# ════════════════════════════════════════════════════════════════════════════
# 1. Regime — HMM
# ════════════════════════════════════════════════════════════════════════════
class RegimeHMMRequest(BaseModel):
    closes: List[float] = Field(..., min_length=30, description="Close prices, chronological")
    n_states: int = Field(3, ge=2, le=5)
    lookback: int = Field(252, ge=30)
    n_init: int = Field(5, ge=1, le=20)


@router.post("/regime-hmm")
def regime_hmm(req: RegimeHMMRequest):
    """
    Fit a Gaussian HMM on the close-price series and return the posterior
    regime probabilities for the most recent observation.

    Falls back to a pure-numpy percentile-based classifier when hmmlearn
    is not installed, so the endpoint always returns meaningful data.
    """
    prices = pd.Series(req.closes)
    try:
        result = regime.detect_regime_from_prices(
            prices,
            n_states=req.n_states,
            lookback=req.lookback,
            n_init=req.n_init,
        )
    except ImportError:
        # hmmlearn not installed — use the numpy percentile fallback
        logger.warning("hmmlearn unavailable; using numpy percentile fallback for regime-hmm")
        try:
            result = detect_regime_numpy_fallback(
                prices,
                n_states=req.n_states,
                lookback=req.lookback,
            )
        except Exception as exc2:
            raise HTTPException(status_code=400, detail=str(exc2)) from exc2
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"success": True, "data": _clean_for_json(result)}


# ════════════════════════════════════════════════════════════════════════════
# 2. Regime — BOCPD
# ════════════════════════════════════════════════════════════════════════════
class RegimeBOCPDRequest(BaseModel):
    returns: List[float] = Field(..., min_length=10, description="Daily returns, chronological")
    hazard_lambda: float = Field(200.0, gt=0)
    threshold: float = Field(0.5, ge=0.0, le=1.0)


@router.post("/regime-bocpd")
def regime_bocpd(req: RegimeBOCPDRequest):
    """
    Run Bayesian Online Changepoint Detection on a returns series.
    Returns a per-timestep changepoint probability + detected breakpoints.
    """
    returns = np.asarray(req.returns, dtype=float)
    try:
        cp_probs, changepoints = regime.run_bocpd_on_returns(
            returns,
            hazard_lambda=req.hazard_lambda,
            threshold=req.threshold,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    cp_list = np.asarray(cp_probs).tolist()
    cps_arr = np.asarray(changepoints)
    # changepoints is a boolean/0-1 array per timestep -> expose BOTH the raw
    # array and the list of indices where it fires, so the FE can pick.
    cps_indices = np.where(cps_arr > 0)[0].tolist()

    return {
        "success": True,
        "data": {
            "cp_probs": _clean_for_json(cp_list),
            "changepoints": cps_indices,
            "n_observations": len(cp_list),
            "n_changepoints": len(cps_indices),
        },
    }


# ════════════════════════════════════════════════════════════════════════════
# 3. Market state
# ════════════════════════════════════════════════════════════════════════════
class MarketStateRequest(BaseModel):
    prices: Dict[str, List[float]] = Field(
        ..., description="Column-major wide DF: {symbol: [close, …]}"
    )
    dates: Optional[List[str]] = Field(
        None, description="Optional ISO-date index aligned with prices length"
    )
    as_of: Optional[str] = Field(
        None, description="ISO date. Defaults to last row of prices."
    )


@router.post("/market-state")
def market_state_route(req: MarketStateRequest):
    """Compute composite market state (TRENDING / NEUTRAL / CHOPPY)."""
    try:
        df = _prices_long_from_json(req.prices, req.dates)
        if req.as_of:
            as_of = pd.to_datetime(req.as_of)
        else:
            as_of = df["date"].max()
        state = market_state.compute_market_state(df, as_of)
        return {
            "success": True,
            "data": {
                "summary":  state.summary(),
                "regime":   state.regime,
                "score":    float(state.score),
                "engine":   state.engine,
                "nms":      float(state.nms),
                "rb":       float(state.rb),
                "vr":       float(state.vr),
                "mp":       float(state.mp),
                "note":     state.note,
                "as_of":    as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of),
            },
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("market-state failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ════════════════════════════════════════════════════════════════════════════
# 4. Pairs trading — spread + z-score
# ════════════════════════════════════════════════════════════════════════════
class PairsSpreadRequest(BaseModel):
    prices_a: List[float] = Field(..., min_length=30)
    prices_b: List[float] = Field(..., min_length=30)
    lookback: int = Field(60, ge=20)


@router.post("/pairs-spread")
def pairs_spread(req: PairsSpreadRequest):
    """
    Compute the cointegration spread z-score between two symbols.
    Returns the spread, its rolling mean/std, and current z-score.
    """
    try:
        a = np.asarray(req.prices_a, dtype=float)
        b = np.asarray(req.prices_b, dtype=float)
        if len(a) != len(b):
            raise ValueError("prices_a and prices_b must have equal length")
        trader = pairs.PairsTrader(lookback=req.lookback)
        spread, z_score, beta, spread_mean, halflife = trader.compute_spread(a, b)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("pairs-spread failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    spread_arr = np.asarray(spread)
    result = {
        "z_score":     float(z_score),
        "hedge_ratio": float(beta),
        "spread_mean": float(spread_mean),
        "spread_last": float(spread_arr[-1]) if len(spread_arr) else None,
        "halflife":    None if halflife is None else float(halflife),
        "n_observations": int(len(spread_arr)),
    }
    return {"success": True, "data": _clean_for_json(result)}


# ════════════════════════════════════════════════════════════════════════════
# 5. Portfolio allocation
# ════════════════════════════════════════════════════════════════════════════
class PortfolioAllocRequest(BaseModel):
    method: str = Field("hrp", description="hrp | cvar | shrinkage_hrp | equal")
    prices: Dict[str, List[float]]
    dates: Optional[List[str]] = None
    symbols: List[str] = Field(..., min_length=1)
    capital: float = Field(1_000_000.0, gt=0)
    as_of: Optional[str] = None


@router.post("/portfolio-allocate")
def portfolio_allocate(req: PortfolioAllocRequest):
    """
    Allocate ``capital`` across ``symbols`` using the chosen method.
    Returns {symbol: allocated_capital}.
    """
    try:
        df = _prices_long_from_json(req.prices, req.dates)
        if req.as_of:
            as_of = pd.to_datetime(req.as_of)
        else:
            as_of = df["date"].max()
        alloc = portfolio.allocate_portfolio(
            method=req.method,
            prices_df=df,
            symbols=req.symbols,
            date=as_of,
            capital=req.capital,
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("portfolio-allocate failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # strip internal keys like _HEDGE_RESULT
    clean = {k: v for k, v in alloc.items() if not k.startswith("_")}
    return {
        "success": True,
        "data": {
            "method": req.method,
            "capital": req.capital,
            "allocation": _clean_for_json(clean),
        },
    }


# ════════════════════════════════════════════════════════════════════════════
# 6. Conformal VaR
# ════════════════════════════════════════════════════════════════════════════
class ConformalVaRRequest(BaseModel):
    returns: List[float] = Field(..., min_length=50)
    alpha: float = Field(0.05, gt=0.0, lt=0.5,
                         description="Tail probability (0.05 = 95% VaR)")
    window: int = Field(252, ge=50)


@router.post("/conformal-var")
def conformal_var(req: ConformalVaRRequest):
    """Compute split-conformal VaR at the requested confidence level."""
    try:
        returns = np.asarray(req.returns, dtype=float)
        var_est = conformal.compute_conformal_var(
            returns=returns, alpha=req.alpha, window=req.window
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "success": True,
        "data": {
            "var": _clean_for_json(var_est),
            "alpha": req.alpha,
            "confidence": round(1 - req.alpha, 4),
            "window": req.window,
            "n_observations": len(returns),
        },
    }


# ════════════════════════════════════════════════════════════════════════════
# 7. Signal ranking
# ════════════════════════════════════════════════════════════════════════════
class SignalRankRequest(BaseModel):
    candidates: List[Dict[str, Any]] = Field(
        ..., description="Raw signals (each needs symbol/signal_type/"
                         "strength/confidence/reasoning)"
    )
    top_n: int = Field(20, ge=1, le=200)


@router.post("/signals-rank")
def signals_rank(req: SignalRankRequest):
    """
    Canonicalise, merge and rank raw signal candidates. Returns the top N
    by score, with non-tradeable / duplicate symbols collapsed.
    """
    try:
        merged = signals.merge_signal_candidates(req.candidates)
        ranked = signals.rank_signal_candidates(merged)[: req.top_n]
    except Exception as exc:
        logger.exception("signals-rank failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"success": True, "data": _clean_for_json(ranked)}


# ════════════════════════════════════════════════════════════════════════════
# 8. Disposition (capital-gains overhang)
# ════════════════════════════════════════════════════════════════════════════
class DispositionRequest(BaseModel):
    prices: Dict[str, List[float]]
    volume: Optional[Dict[str, List[float]]] = Field(
        None, description="Optional volumes aligned with prices"
    )
    dates: Optional[List[str]] = None
    as_of: Optional[str] = None
    cgo_threshold: float = Field(0.15, ge=0.0, le=1.0)
    volume_spike: float = Field(1.5, ge=1.0)


@router.post("/disposition")
def disposition_route(req: DispositionRequest):
    """
    Generate CGO (capital-gains overhang) disposition-effect signals.
    Returns a list of AlphaSignal dicts for symbols with strong overhang.
    """
    try:
        prices_df = _prices_long_from_json(
            req.prices, req.dates, volume_json=req.volume
        )
        if req.as_of:
            as_of = pd.to_datetime(req.as_of)
        else:
            as_of = prices_df["date"].max()
        sigs = disposition.generate_cgo_signals_at_date(
            prices_df=prices_df,
            date=as_of,
            cgo_threshold=req.cgo_threshold,
            volume_spike=req.volume_spike,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("disposition failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "success": True,
        "data": [s.to_dict() for s in sigs],
    }
