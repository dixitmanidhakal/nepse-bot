"""
Quant Snapshot Service
======================

Runs ALL Quant Lab + Advanced Quant Lab analyses automatically every 30
minutes during NEPSE market hours and caches the results in memory.

The snapshot is served via GET /api/v1/quant/snapshot so the frontend
can display live quant analysis without the user manually clicking "Run".

Analyses computed:
  1. HMM Regime Detection        — bull / neutral / bear + probabilities
  2. BOCPD Changepoint Detection — structural break flag + last break index
  3. Composite Market State      — TRENDING / NEUTRAL / CHOPPY + NMS/RB/VR/MP
  4. Conformal VaR               — 95th-percentile daily-loss bound
  5. Signal Ranking              — top-10 ranked candidates from all sources
  6. Kelly Fraction              — optimal sizing from current market win_rate
  7. Portfolio Allocation hint   — HRP weights for liquid universe

The `_snapshot` module-level dict is updated in-place by `run_snapshot()`.
The endpoint layer just reads it — no locking needed since Python GIL
protects dict-level updates on CPython.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ─── In-memory snapshot store ─────────────────────────────────────────────────

_EMPTY: Dict[str, Any] = {
    "computed_at": None,
    "status": "pending",
    "hmm": None,
    "bocpd": None,
    "market_state": None,
    "conformal_var": None,
    "top_signals": [],
    "kelly": None,
    "portfolio": None,
    "errors": [],
}

_snapshot: Dict[str, Any] = dict(_EMPTY)

# Universe of liquid symbols used for all analyses
_UNIVERSE = [
    "NABIL", "EBL", "GBIME", "SBI", "NICA", "ADBL", "NBL",
    "JBNL", "MEGA", "NMB",
    "NLIC", "LICN", "PRIN",
    "NHPC", "UPPER", "CHCL", "BARUN", "NPCL",
    "NTC", "SHIVM",
    "CIT", "HIDCL",
    "PCBL", "SCB",
]

_HMM_LOOKBACK = 120
_BOCPD_WINDOW = 90
_BOCPD_HAZARD = 100.0


def get_snapshot() -> Dict[str, Any]:
    """Return the most recent cached snapshot (read-only)."""
    return dict(_snapshot)


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _load_median_returns(provider) -> Optional[np.ndarray]:
    """Build universe-median daily return series from historical OHLCV."""
    all_returns: List[np.ndarray] = []
    n = _HMM_LOOKBACK + 20
    for sym in _UNIVERSE:
        try:
            df = provider.load_ohlcv(sym, min_rows=n + 10)
            if df.empty or len(df) < n + 1:
                continue
            closes = df["close"].values[-n - 1:]
            rets   = np.diff(np.log(np.clip(closes, 1e-8, None)))
            all_returns.append(rets[-n:])
        except Exception:
            continue

    if len(all_returns) < 5:
        return None

    return np.median(np.vstack(all_returns), axis=0)


def _run_hmm(median_rets: np.ndarray) -> Dict[str, Any]:
    try:
        from app.components.quant.regime import HMMRegimeDetector
        detector = HMMRegimeDetector(n_states=3, lookback=_HMM_LOOKBACK, n_init=3)
        detector.fit(median_rets)
        result = detector.predict(median_rets)
        result["exposure_multiplier"] = detector.get_exposure_multiplier(result["probabilities"])
        result["method"] = "hmm"
        return result
    except Exception as e:
        logger.debug("HMM failed in snapshot: %s", e)

    try:
        import pandas as pd
        from app.components.quant.regime import detect_regime_numpy_fallback
        prices = pd.Series(np.exp(np.cumsum(median_rets)) * 1000)
        return detect_regime_numpy_fallback(prices, n_states=3, lookback=_HMM_LOOKBACK)
    except Exception:
        return {"regime": "neutral", "confidence": 0.34,
                "probabilities": {"bull": 0.33, "neutral": 0.34, "bear": 0.33},
                "exposure_multiplier": 0.5, "method": "fallback"}


def _run_bocpd(median_rets: np.ndarray) -> Dict[str, Any]:
    try:
        from app.components.quant.regime import BOCPDDetector
        rets = median_rets[-_BOCPD_WINDOW:]
        detector = BOCPDDetector(hazard_lambda=_BOCPD_HAZARD)
        detected_at: List[int] = []
        cp_probs: List[float] = []
        for i, ret in enumerate(rets):
            cp_probs.append(detector.update(ret))
            if detector.detect(threshold=0.5):
                detected_at.append(i)

        recent_break = bool(detected_at and detected_at[-1] >= len(rets) - 10)
        return {
            "n_changepoints": len(detected_at),
            "n_observations": len(rets),
            "recent_break": recent_break,
            "last_break_index": detected_at[-1] if detected_at else None,
            "last_cp_prob": round(cp_probs[-1], 4) if cp_probs else 0.0,
        }
    except Exception as e:
        logger.debug("BOCPD snapshot error: %s", e)
        return {"error": str(e), "recent_break": False, "n_changepoints": 0}


def _run_market_state(provider) -> Dict[str, Any]:
    import pandas as pd
    try:
        sym_dfs = provider.load_universe(symbols=_UNIVERSE[:15])
        if not sym_dfs:
            raise ValueError("empty universe")

        # Convert Dict[str, pd.DataFrame] → long-format DataFrame
        frames = []
        for sym, df in sym_dfs.items():
            if df is None or df.empty or len(df) < 30:
                continue
            sub = df.copy()
            sub["symbol"] = sym
            frames.append(sub)
        if len(frames) < 4:
            raise ValueError(f"only {len(frames)} valid symbols for market state")

        universe_df = pd.concat(frames, ignore_index=True)

        from app.components.quant.market_state import compute_market_state
        from datetime import datetime as dt
        state = compute_market_state(universe_df, dt.today())
        return {
            "regime": state.regime,
            "score": round(state.score, 3),
            "engine": state.engine,
            "nms": round(state.nms, 4),
            "rb": round(state.rb, 4),
            "vr": round(state.vr, 4),
            "mp": round(state.mp, 4),
            "summary": state.summary(),
        }
    except Exception as e:
        logger.debug("Market state snapshot error: %s", e)
        return {"error": str(e), "regime": "NEUTRAL", "score": 2.0}


def _run_conformal_var(median_rets: np.ndarray) -> Dict[str, Any]:
    try:
        from app.components.quant.conformal import ConformalVaR
        estimator = ConformalVaR(alpha=0.05, window=min(252, len(median_rets)))
        var_val = estimator.fit_predict(median_rets)
        return {
            "var_95": round(float(var_val) * 100, 3),
            "high_risk": float(var_val) > 0.035,
            "alpha": 0.05,
            "window": min(252, len(median_rets)),
        }
    except Exception as e:
        logger.debug("Conformal VaR snapshot error: %s", e)
        return {"error": str(e), "var_95": 2.0, "high_risk": False}


def _run_top_signals(provider) -> List[Dict[str, Any]]:
    """Collect and rank signals from momentum + mean_reversion + disposition."""
    from app.components.bots.quant_composite_bot import (
        _momentum_signals,
        _mean_reversion_signals,
        _disposition_signals,
    )
    try:
        raw: List[Dict[str, Any]] = []
        raw.extend(_momentum_signals(provider, _UNIVERSE))
        raw.extend(_mean_reversion_signals(provider, _UNIVERSE))
        raw.extend(_disposition_signals(provider, _UNIVERSE))

        from app.components.quant.signals import rank_signal_candidates
        ranked = rank_signal_candidates(raw)

        result = []
        for r in ranked[:10]:
            sym = str(r.get("symbol", ""))
            orig = next(
                (c for c in raw if c.get("symbol", "").upper() == sym.upper()), {}
            )
            result.append({
                "symbol":      sym,
                "signal_type": r.get("signal_type", "unknown"),
                "score":       round(float(r.get("score", 0)), 3),
                "strength":    round(float(r.get("strength", 0)), 3),
                "confidence":  round(float(r.get("confidence", 0)), 3),
                "reasoning":   r.get("reasoning", ""),
                "entry_price": orig.get("entry_price"),
            })
        return result
    except Exception as e:
        logger.debug("Top-signals snapshot error: %s", e)
        return []


def _run_kelly(win_prob: float = 0.55) -> Dict[str, Any]:
    """Compute Kelly fraction for a typical NEPSE trade."""
    try:
        b = 0.07 / 0.03  # avg_win / avg_loss
        kelly_full = max(0.0, (b * win_prob - (1 - win_prob)) / b)
        half_kelly = kelly_full / 2.0
        return {
            "win_prob": round(win_prob, 3),
            "full_kelly": round(kelly_full, 4),
            "half_kelly": round(min(half_kelly, 1.0), 4),
            "recommended_fraction": round(float(np.clip(half_kelly, 0.3, 1.0)), 4),
        }
    except Exception as e:
        return {"error": str(e)}


def _run_portfolio_hints(provider) -> Dict[str, Any]:
    """Compute HRP weights for a subset of the universe using live OHLCV data."""
    import pandas as pd
    try:
        from app.components.quant.portfolio import allocate_portfolio
        from datetime import datetime as dt

        syms = _UNIVERSE[:10]
        # load_universe returns Dict[str, pd.DataFrame] (per-symbol DataFrames)
        sym_dfs: Dict[str, Any] = provider.load_universe(symbols=syms)
        if not sym_dfs:
            return {"error": "insufficient universe data", "weights": {}}

        # Build long-format DataFrame required by allocate_portfolio
        frames = []
        valid_syms = []
        for sym, df in sym_dfs.items():
            if df is None or df.empty or len(df) < 30:
                continue
            sub = df.copy()
            sub["symbol"] = sym
            cols = [c for c in ["symbol", "date", "open", "high", "low", "close", "volume"] if c in sub.columns]
            frames.append(sub[cols])
            valid_syms.append(sym)

        if len(valid_syms) < 4:
            return {"error": f"only {len(valid_syms)} valid symbols", "weights": {}}

        prices_df = pd.concat(frames, ignore_index=True)
        as_of = prices_df["date"].max()

        alloc = allocate_portfolio(
            method="hrp",
            prices_df=prices_df,
            symbols=valid_syms,
            date=as_of,
            capital=1_000_000,
        )
        weights = {
            k: round(float(v) / 1_000_000, 4)
            for k, v in alloc.items()
            if not k.startswith("_") and isinstance(v, (int, float))
        }
        return {
            "method": "hrp",
            "symbols": valid_syms,
            "weights": weights,
        }
    except Exception as e:
        logger.debug("Portfolio hints error: %s", e)
        return {"error": str(e), "weights": {}}


# ─── Main entry point ─────────────────────────────────────────────────────────

def run_snapshot() -> None:
    """
    Compute all quant analyses and update the in-memory snapshot.
    Called by the scheduler every 30 minutes during market hours.
    Also callable manually via POST /api/v1/quant/snapshot/refresh.
    """
    global _snapshot
    errors: List[str] = []
    logger.info("Quant snapshot: starting full analysis")

    try:
        from app.services.data.historical_provider import get_historical_provider
        provider = get_historical_provider()
        if not provider.is_available():
            _snapshot = {**_EMPTY, "status": "unavailable",
                         "computed_at": datetime.now(timezone.utc).isoformat(),
                         "errors": ["HistoricalDataProvider not available"]}
            return
    except Exception as e:
        _snapshot = {**_EMPTY, "status": "error",
                     "computed_at": datetime.now(timezone.utc).isoformat(),
                     "errors": [str(e)]}
        return

    # Median returns (used by several analyses)
    median_rets = _load_median_returns(provider)

    # 1. HMM
    hmm_result = None
    if median_rets is not None and len(median_rets) >= 30:
        try:
            hmm_result = _run_hmm(median_rets)
            logger.info("Snapshot HMM: %s (conf=%.1f%%)", hmm_result["regime"], hmm_result["confidence"] * 100)
        except Exception as e:
            errors.append(f"hmm: {e}")

    # 2. BOCPD
    bocpd_result = None
    if median_rets is not None and len(median_rets) >= 30:
        try:
            bocpd_result = _run_bocpd(median_rets)
            logger.info("Snapshot BOCPD: %d changepoints, recent_break=%s",
                        bocpd_result.get("n_changepoints", 0), bocpd_result.get("recent_break"))
        except Exception as e:
            errors.append(f"bocpd: {e}")

    # 3. Market State
    ms_result = None
    try:
        ms_result = _run_market_state(provider)
        logger.info("Snapshot market_state: %s (score=%.2f)", ms_result.get("regime"), ms_result.get("score", 0))
    except Exception as e:
        errors.append(f"market_state: {e}")

    # 4. Conformal VaR
    var_result = None
    if median_rets is not None and len(median_rets) >= 30:
        try:
            var_result = _run_conformal_var(median_rets)
            logger.info("Snapshot VaR: %.2f%% (high_risk=%s)", var_result.get("var_95", 0), var_result.get("high_risk"))
        except Exception as e:
            errors.append(f"conformal_var: {e}")

    # 5. Top signals
    top_signals = []
    try:
        top_signals = _run_top_signals(provider)
        logger.info("Snapshot top_signals: %d ranked", len(top_signals))
    except Exception as e:
        errors.append(f"top_signals: {e}")

    # 6. Kelly
    win_prob = 0.55
    try:
        # Use quant_composite bot's RL state if DB is available
        from app.database import SessionLocal
        from app.components.rl_engine import get_or_create_state
        db = SessionLocal()
        try:
            state = get_or_create_state("quant_composite", "Quant Composite Bot", "quant_composite", db)
            win_prob = float(state.rolling_accuracy or 0.55)
        finally:
            db.close()
    except Exception:
        pass
    kelly_result = _run_kelly(win_prob)

    # 7. Portfolio hints
    portfolio_result = None
    try:
        portfolio_result = _run_portfolio_hints(provider)
    except Exception as e:
        errors.append(f"portfolio: {e}")

    _snapshot = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok" if not errors else "partial",
        "hmm": hmm_result,
        "bocpd": bocpd_result,
        "market_state": ms_result,
        "conformal_var": var_result,
        "top_signals": top_signals,
        "kelly": kelly_result,
        "portfolio": portfolio_result,
        "errors": errors,
    }
    logger.info("Quant snapshot: complete (status=%s, errors=%d)", _snapshot["status"], len(errors))
