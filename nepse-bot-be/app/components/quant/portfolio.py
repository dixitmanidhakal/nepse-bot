"""Portfolio construction module: HRP, CVaR, shrinkage, and gold/silver hedge overlay.

Uses skfolio for Hierarchical Risk Parity and CVaR optimization.
Replaces equal-weight allocation when PORTFOLIO_METHOD != 'equal_weight'.

Models implemented:
    6. HRP (Hierarchical Risk Parity) - Lopez de Prado 2016
    7. NL Shrinkage - Ledoit-Wolf via skfolio EmpiricalPrior
    8. CVaR Optimization - Rockafellar & Uryasev 2000
    9. Gold/Silver Hedge Overlay - Ederington (1979) rolling MVHR

Hedge overlay:
    When `gold_hedge_db_path` is provided to `allocate_portfolio()`, the module
    computes a Minimum Variance Hedge Ratio (h*) against gold prices and
    withholds a proportional cash buffer from equity deployment.

    Since NEPSE has no gold ETF, the hedge is implemented as a cash reserve:
    capital × h* (capped at 20%) is held back, reducing NEPSE equity exposure
    during risk-off regimes (gold rising > 3% over 20 days).

    The returned dict includes a "_GOLD_HEDGE" entry with the withheld amount.

Usage:
    from app.components.quant.portfolio import allocate_portfolio

    # Standard (no hedge)
    alloc = allocate_portfolio(
        method="hrp",
        prices_df=prices_df,
        symbols=["NABIL", "GBIME", "PCBL"],
        date=datetime(2025, 6, 1),
        capital=1_000_000.0,
    )

    # With gold hedge overlay
    alloc = allocate_portfolio(
        method="hrp",
        prices_df=prices_df,
        symbols=["NABIL", "GBIME", "PCBL"],
        date=datetime(2025, 6, 1),
        capital=1_000_000.0,
        gold_hedge_db_path="data/nepse_market_data.db",
    )
    # alloc["_GOLD_HEDGE"] → hedge buffer in NPR (cash reserve)
    # alloc["_HEDGE_RESULT"] → HedgeResult object (for display/logging)
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_return_matrix(
    prices_df: pd.DataFrame,
    symbols: List[str],
    date,
    lookback: int = 60,
) -> Optional[pd.DataFrame]:
    """Build an aligned return matrix for *symbols* using data up to *date*.

    Returns None if any symbol has insufficient history (< lookback rows)
    or if the resulting matrix contains NaN/Inf values.
    """
    # Normalise date to string (prices_df["date"] is stored as TEXT)
    if isinstance(date, datetime):
        date_str = date.strftime("%Y-%m-%d")
    else:
        date_str = str(date)[:10]

    returns_dict: Dict[str, np.ndarray] = {}
    for sym in symbols:
        sym_data = prices_df[
            (prices_df["symbol"] == sym) & (prices_df["date"] <= date_str)
        ].sort_values("date").tail(lookback + 1)

        if len(sym_data) < lookback + 1:
            return None

        closes = sym_data["close"].values
        if np.any(closes <= 0):
            return None

        rets = np.diff(closes) / closes[:-1]
        if len(rets) < lookback:
            return None
        returns_dict[sym] = rets[-lookback:]

    if not returns_dict:
        return None

    ret_df = pd.DataFrame(returns_dict)

    # Sanity check: no NaN/Inf
    if ret_df.isnull().any().any() or np.isinf(ret_df.values).any():
        return None

    # Need at least 2 observations per asset (otherwise covariance is degenerate)
    if ret_df.shape[0] < 2:
        return None

    return ret_df


def _equal_weight(symbols: List[str], capital: float) -> Dict[str, float]:
    """Fallback: equal-weight allocation."""
    n = len(symbols)
    if n == 0:
        return {}
    per_sym = capital / n
    return {sym: per_sym for sym in symbols}


# ---------------------------------------------------------------------------
# HRP Allocator
# ---------------------------------------------------------------------------

class HRPAllocator:
    """Hierarchical Risk Parity portfolio allocation using skfolio.

    Parameters
    ----------
    lookback : int
        Number of trading days for rolling return estimation (default 60).
    risk_measure : str
        Risk measure for the HRP algorithm.  One of "CVaR", "Variance".
    """

    def __init__(self, lookback: int = 60, risk_measure: str = "CVaR"):
        self.lookback = lookback
        self.risk_measure = risk_measure

    def allocate(
        self,
        prices_df: pd.DataFrame,
        symbols: List[str],
        date,
        capital: float,
    ) -> Dict[str, float]:
        """Compute HRP-optimal capital allocation.

        Parameters
        ----------
        prices_df : pd.DataFrame
            Full price DataFrame with columns: symbol, date, close, ...
        symbols : list[str]
            Symbols to allocate across (long-only universe).
        date : datetime-like
            Signal date; only data on or before this date is used (no lookahead).
        capital : float
            Total capital (NPR) to distribute.

        Returns
        -------
        dict[str, float]
            Mapping of symbol -> allocated capital in NPR.
        """
        # Edge cases
        if not symbols:
            return {}
        if len(symbols) == 1:
            return {symbols[0]: capital}

        ret_df = _extract_return_matrix(prices_df, symbols, date, self.lookback)
        if ret_df is None:
            logger.debug("HRP: insufficient data, falling back to equal-weight")
            return _equal_weight(symbols, capital)

        try:
            from skfolio.optimization import HierarchicalRiskParity
            from skfolio import RiskMeasure

            risk_map = {
                "CVaR": RiskMeasure.CVAR,
                "Variance": RiskMeasure.VARIANCE,
            }
            rm = risk_map.get(self.risk_measure, RiskMeasure.CVAR)

            hrp = HierarchicalRiskParity(risk_measure=rm)
            hrp.fit(ret_df.values)
            weights = hrp.weights_

            # Map weights back to symbol names
            result: Dict[str, float] = {}
            cols = list(ret_df.columns)
            for i, sym in enumerate(cols):
                w = float(weights[i])
                # Clamp negative weights (shouldn't happen, but defensive)
                w = max(w, 0.0)
                result[sym] = w * capital

            # Ensure total allocated <= capital (floating-point guard)
            total = sum(result.values())
            if total > 0 and abs(total - capital) > 1.0:
                scale = capital / total
                result = {s: v * scale for s, v in result.items()}

            return result

        except Exception as e:
            logger.warning("HRP solver failed (%s), falling back to equal-weight", e)
            return _equal_weight(symbols, capital)


# ---------------------------------------------------------------------------
# CVaR Optimizer
# ---------------------------------------------------------------------------

class CVaROptimizer:
    """Conditional Value-at-Risk portfolio optimization using skfolio.

    Minimises CVaR (Expected Shortfall) subject to:
        * long-only (w >= 0)
        * per-asset max weight
        * fully invested (sum(w) == 1)

    Parameters
    ----------
    alpha : float
        Tail probability for CVaR (default 0.05 = 95th percentile risk).
    max_weight : float
        Maximum weight per single asset (default 0.30).
    lookback : int
        Number of trading days for return estimation (default 60).
    """

    def __init__(
        self,
        alpha: float = 0.05,
        max_weight: float = 0.30,
        lookback: int = 60,
    ):
        self.alpha = alpha
        self.max_weight = max_weight
        self.lookback = lookback

    def optimize(
        self,
        prices_df: pd.DataFrame,
        symbols: List[str],
        date,
        capital: float,
    ) -> Dict[str, float]:
        """Minimise CVaR and return capital allocation.

        Same parameter semantics as HRPAllocator.allocate().
        """
        if not symbols:
            return {}
        if len(symbols) == 1:
            return {symbols[0]: capital}

        ret_df = _extract_return_matrix(prices_df, symbols, date, self.lookback)
        if ret_df is None:
            logger.debug("CVaR: insufficient data, falling back to equal-weight")
            return _equal_weight(symbols, capital)

        try:
            from skfolio.optimization import MeanRisk, ObjectiveFunction
            from skfolio import RiskMeasure

            model = MeanRisk(
                risk_measure=RiskMeasure.CVAR,
                objective_function=ObjectiveFunction.MINIMIZE_RISK,
                max_weights=self.max_weight,
                cvar_beta=1 - self.alpha,  # skfolio: cvar_beta = 1-alpha
            )
            model.fit(ret_df.values)
            weights = model.weights_

            result: Dict[str, float] = {}
            cols = list(ret_df.columns)
            for i, sym in enumerate(cols):
                w = float(weights[i])
                w = max(w, 0.0)
                result[sym] = w * capital

            total = sum(result.values())
            if total > 0 and abs(total - capital) > 1.0:
                scale = capital / total
                result = {s: v * scale for s, v in result.items()}

            return result

        except Exception as e:
            logger.warning("CVaR solver failed (%s), falling back to equal-weight", e)
            return _equal_weight(symbols, capital)


# ---------------------------------------------------------------------------
# Shrinkage Estimator (Model 7 - uses skfolio's built-in LedoitWolf)
# ---------------------------------------------------------------------------

class ShrinkageHRPAllocator(HRPAllocator):
    """HRP with Ledoit-Wolf shrinkage covariance estimator.

    This is Model 7 from the plan: NL Shrinkage.  We use skfolio's
    built-in ``EmpiricalPrior`` with the ``ShrunkCovariance`` estimator
    which implements the Ledoit-Wolf (2004) analytical shrinkage.

    Falls back to plain HRP if the prior estimator is unavailable.
    """

    def allocate(
        self,
        prices_df: pd.DataFrame,
        symbols: List[str],
        date,
        capital: float,
    ) -> Dict[str, float]:
        if not symbols:
            return {}
        if len(symbols) == 1:
            return {symbols[0]: capital}

        ret_df = _extract_return_matrix(prices_df, symbols, date, self.lookback)
        if ret_df is None:
            logger.debug("ShrinkageHRP: insufficient data, falling back to equal-weight")
            return _equal_weight(symbols, capital)

        try:
            from skfolio.optimization import HierarchicalRiskParity
            from skfolio import RiskMeasure
            from skfolio.prior import EmpiricalPrior

            risk_map = {
                "CVaR": RiskMeasure.CVAR,
                "Variance": RiskMeasure.VARIANCE,
            }
            rm = risk_map.get(self.risk_measure, RiskMeasure.CVAR)

            # Use EmpiricalPrior with Ledoit-Wolf shrinkage
            prior = EmpiricalPrior(
                covariance_estimator=None,  # default uses LedoitWolf internally
            )

            hrp = HierarchicalRiskParity(
                risk_measure=rm,
                prior_estimator=prior,
            )
            hrp.fit(ret_df.values)
            weights = hrp.weights_

            result: Dict[str, float] = {}
            cols = list(ret_df.columns)
            for i, sym in enumerate(cols):
                w = float(weights[i])
                w = max(w, 0.0)
                result[sym] = w * capital

            total = sum(result.values())
            if total > 0 and abs(total - capital) > 1.0:
                scale = capital / total
                result = {s: v * scale for s, v in result.items()}

            return result

        except Exception as e:
            logger.warning(
                "ShrinkageHRP failed (%s), falling back to plain HRP", e
            )
            return super().allocate(prices_df, symbols, date, capital)


# ---------------------------------------------------------------------------
# Unified Dispatcher
# ---------------------------------------------------------------------------

def allocate_portfolio(
    method: str,
    prices_df: pd.DataFrame,
    symbols: List[str],
    date,
    capital: float,
    *,
    lookback: int = 60,
    risk_measure: str = "CVaR",
    max_weight: float = 0.30,
    alpha: float = 0.05,
    hrp_cvar_blend: float = 0.6,
    gold_hedge_db_path: Optional[str] = None,
    gold_hedge_kwargs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Unified allocation dispatcher with optional gold/silver hedge overlay.

    Parameters
    ----------
    method : str
        One of ``"equal_weight"``, ``"hrp"``, ``"cvar"``, ``"hrp_cvar"``,
        ``"shrinkage_hrp"``.
    prices_df : pd.DataFrame
        Full price DataFrame (symbol, date, close, ...).
    symbols : list[str]
        Symbols to allocate across.
    date : datetime-like
        Signal date (no lookahead).
    capital : float
        Total capital to allocate (NPR).
    lookback : int
        Rolling window for return estimation.
    risk_measure : str
        HRP risk measure ("CVaR" or "Variance").
    max_weight : float
        Per-asset cap for CVaR optimizer.
    alpha : float
        CVaR tail probability.
    hrp_cvar_blend : float
        HRP weight in the blended HRP+CVaR method (default 0.6 = 60% HRP).
    gold_hedge_db_path : str, optional
        Path to nepse_market_data.db with gold/silver data ingested.
        When provided, activates the gold/silver hedge overlay:
            - Computes rolling MVHR (Ederington 1979) vs XAU/USD
            - In risk-off regimes, withholds hedge_pct × capital as cash buffer
            - Returns ``"_GOLD_HEDGE"`` key with withheld NPR amount
            - Returns ``"_HEDGE_RESULT"`` key with full HedgeResult object
        When None (default), no hedge computation is performed.
    gold_hedge_kwargs : dict, optional
        Extra kwargs forwarded to GoldSilverHedgeOverlay.__init__().
        E.g.: {"max_hedge_pct": 0.15, "risk_off_threshold": 0.025}

    Returns
    -------
    dict[str, float | HedgeResult]
        symbol -> allocated capital (NPR).
        "_GOLD_HEDGE" -> hedge buffer NPR (only when gold_hedge_db_path provided).
        "_HEDGE_RESULT" -> HedgeResult object (only when gold_hedge_db_path provided).
    """
    # Gold-hedge overlay requires the quant-terminal gold_hedge module which
    # depends on external commodity feeds. Not ported to nepse-bot yet — if
    # the caller requests a hedge, fail fast instead of silently skipping.
    try:
        from backend.quant_pro.gold_hedge import GoldSilverHedgeOverlay  # type: ignore[import-not-found]
    except ImportError:
        if gold_hedge_db_path is not None:
            raise NotImplementedError(
                "Gold-hedge overlay is not available in nepse-bot. "
                "Omit gold_hedge_db_path to use plain allocation."
            )
        GoldSilverHedgeOverlay = None  # type: ignore[assignment]

    # Defensive: deduplicate and validate
    symbols = list(dict.fromkeys(symbols))  # preserve order, remove dups
    if not symbols:
        return {}
    if capital <= 0:
        return {sym: 0.0 for sym in symbols}

    method = method.lower().strip()

    # ---- Gold/Silver Hedge Overlay -----------------------------------------
    # Compute BEFORE equity allocation so we use reduced deployable_capital.
    hedge_result = None
    deployable = capital

    if gold_hedge_db_path is not None:
        try:
            overlay = GoldSilverHedgeOverlay(**(gold_hedge_kwargs or {}))
            hedge_result = overlay.compute(
                prices_df=prices_df,
                symbols=symbols,
                date=date,
                capital=capital,
                db_path=gold_hedge_db_path,
            )
            deployable = hedge_result.deployable_capital
            if hedge_result.apply_hedge:
                logger.info(
                    "Gold hedge active: %.1f%% withheld (h*=%.3f, HE=%.1f%%, regime=%s)",
                    hedge_result.hedge_pct * 100,
                    hedge_result.h_star,
                    hedge_result.hedge_effectiveness * 100,
                    hedge_result.gold_regime,
                )
        except Exception as e:
            logger.warning("Gold hedge overlay failed (%s), proceeding without hedge", e)
            deployable = capital

    # ---- Equity Allocation (on deployable_capital) -------------------------
    if method == "hrp":
        alloc = HRPAllocator(
            lookback=lookback, risk_measure=risk_measure
        ).allocate(prices_df, symbols, date, deployable)

    elif method == "cvar":
        alloc = CVaROptimizer(
            alpha=alpha, max_weight=max_weight, lookback=lookback
        ).optimize(prices_df, symbols, date, deployable)

    elif method == "shrinkage_hrp":
        alloc = ShrinkageHRPAllocator(
            lookback=lookback, risk_measure=risk_measure
        ).allocate(prices_df, symbols, date, deployable)

    elif method == "hrp_cvar":
        # Blend: hrp_cvar_blend * HRP + (1 - hrp_cvar_blend) * CVaR
        hrp_alloc = HRPAllocator(
            lookback=lookback, risk_measure=risk_measure
        ).allocate(prices_df, symbols, date, deployable)
        cvar_alloc = CVaROptimizer(
            alpha=alpha, max_weight=max_weight, lookback=lookback
        ).optimize(prices_df, symbols, date, deployable)

        alloc = {}
        for sym in symbols:
            h = hrp_alloc.get(sym, 0.0)
            c = cvar_alloc.get(sym, 0.0)
            alloc[sym] = hrp_cvar_blend * h + (1 - hrp_cvar_blend) * c

    else:
        # equal_weight (default)
        alloc = _equal_weight(symbols, deployable)

    # ---- Attach hedge metadata to result -----------------------------------
    if hedge_result is not None:
        alloc["_GOLD_HEDGE"] = hedge_result.hedge_capital
        alloc["_HEDGE_RESULT"] = hedge_result  # type: ignore[assignment]

    return alloc


__all__ = [
    "HRPAllocator",
    "CVaROptimizer",
    "ShrinkageHRPAllocator",
    "allocate_portfolio",
    # Gold hedge — imported from gold_hedge.py but re-exported for convenience
    "GoldSilverHedgeOverlay",
    "HedgeResult",
    "get_gold_regime",
]


def __getattr__(name: str):
    """Lazy re-export. Gold-hedge symbols are not ported to nepse-bot."""
    if name in ("GoldSilverHedgeOverlay", "HedgeResult", "get_gold_regime"):
        raise ImportError(
            f"{name} is not available in nepse-bot (gold-hedge overlay not ported)."
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
