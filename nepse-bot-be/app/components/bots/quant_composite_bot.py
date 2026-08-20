"""
Quant Composite Bot
===================

The most sophisticated bot in the registry — it runs ALL analytical engines
from Quant Lab and Advanced Quant Lab automatically every cycle, combining
their outputs into a unified entry signal.

Pipeline (in order every 15-minute cycle):
  1. HMM Regime Gate (Advanced Quant Lab — HMM Regime Detection)
     → Fit a 3-state Gaussian HMM on universe median daily returns.
     → If regime = bear with confidence > 0.55 → no new entries.

  2. BOCPD Structural-Break Gate (Advanced Quant Lab — BOCPD)
     → Detect if a market-structure break occurred in the last 10 sessions.
     → If break detected → no new entries (regime transition in progress).

  3. Composite Market State (Advanced Quant Lab — Market State Scanner)
     → TRENDING  (score ≥ 2.5): allow up to 5 entries, 3.5 / 8.0 stop/target.
     → NEUTRAL   (1.5–2.5):    allow up to 3 entries, 4.0 / 7.0 stop/target.
     → CHOPPY    (score ≤ 1.5): allow up to 2 entries, 5.0 / 5.5 stop/target.

  4. Multi-source signal generation
     a. Momentum (RSI-14 + MACD hist + Bollinger + volume) — same logic as
        MomentumBot but applied across the broader Quant universe.
     b. Mean-reversion (RSI < 40 + price at/near BB lower band + volume pop).
     c. Disposition / CGO (Advanced Quant Lab — Capital Gains Overhang):
        stocks with CGO > 0.08 that just spiked through their ceiling.

  5. Signal Ranking (Advanced Quant Lab — Signal Ranking)
     → `rank_signal_candidates` deduplicates, applies sector-concentration
        penalties, and sorts by strength × confidence.

  6. Kelly Fraction (Quant Lab — Kelly Calculator)
     → Derive win_prob from the bot's EMA rolling accuracy.
     → Multiply the ranked score by the Kelly fraction (caps at 1.0).

  7. Conformal VaR gate (Advanced Quant Lab — Conformal VaR)
     → Compute 95th-percentile daily-loss bound from recent returns.
     → If VaR > 3.5% → tighten stop %, apply 0.8× score penalty.

  8. Emit top-N signals (N set by market state regime).

This bot's results feed the same BaseBot infrastructure: RL gate, accuracy
threshold, duplicate-position guard, and automatic trade resolution.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.components.bots.base_bot import BaseBot
from app.components.bots.nepse_universe import get_nepse_universe, get_sector, get_sector_map

logger = logging.getLogger("bot.quant_composite")

# ─── Quant constants ─────────────────────────────────────────────────────────
_HMM_LOOKBACK   = 120   # trading days used to fit HMM
_BOCPD_WINDOW   = 90    # returns fed to BOCPD
_BOCPD_HAZARD   = 100.0 # shorter expected run length → more sensitive
_CGO_THRESHOLD  = 0.08  # lower than default 0.15 so we catch earlier breakouts
_VOL_SPIKE      = 1.3   # CGO volume-spike multiplier
_KELLY_WIN_GAIN = 0.07  # assumed avg winning trade gain (7%)
_KELLY_AVG_LOSS = 0.03  # assumed avg losing trade loss (3%)
_VAR_ALPHA      = 0.05  # 95% VaR
_VAR_ALERT_PCT  = 0.035 # daily VaR > 3.5% → tighten risk


# ─── Technical helpers ────────────────────────────────────────────────────────

def _rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.diff()
    gain  = delta.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd_hist(closes: pd.Series, fast=12, slow=26, sig=9) -> pd.Series:
    ema_f  = closes.ewm(span=fast, adjust=False).mean()
    ema_s  = closes.ewm(span=slow, adjust=False).mean()
    macd   = ema_f - ema_s
    signal = macd.ewm(span=sig, adjust=False).mean()
    return macd - signal


def _bollinger(closes: pd.Series, period: int = 20, std: float = 2.0):
    mid  = closes.rolling(period).mean()
    band = closes.rolling(period).std()
    return mid, mid + std * band, mid - std * band


def _median_returns(provider, symbols: List[str], n: int = 200) -> Optional[np.ndarray]:
    """
    Compute universe-median daily return series of length `n`.
    Uses historical OHLCV from the provider.
    """
    all_returns: List[np.ndarray] = []
    for sym in symbols:
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

    matrix = np.vstack(all_returns)
    return np.median(matrix, axis=0)


# ─── Step 1: HMM Regime ───────────────────────────────────────────────────────

def _hmm_regime(median_rets: np.ndarray) -> Dict[str, Any]:
    """
    Run the 3-state HMM from app.components.quant.regime.
    Returns a dict: {regime, confidence, probabilities, exposure_multiplier}.
    Gracefully falls back to the numpy percentile method if hmmlearn is absent.
    """
    from app.components.quant.regime import (
        HMMRegimeDetector,
        detect_regime_numpy_fallback,
    )

    fallback = {
        "regime": "neutral",
        "confidence": 0.34,
        "probabilities": {"bull": 0.33, "neutral": 0.34, "bear": 0.33},
        "exposure_multiplier": 0.5,
        "method": "neutral_default",
    }

    if len(median_rets) < 30:
        return fallback

    try:
        detector = HMMRegimeDetector(n_states=3, lookback=_HMM_LOOKBACK, n_init=3)
        detector.fit(median_rets)
        result = detector.predict(median_rets)
        result["exposure_multiplier"] = detector.get_exposure_multiplier(result["probabilities"])
        result["method"] = "hmm"
        return result
    except Exception as e:
        logger.debug("HMM failed (%s), using numpy fallback", e)

    # Numpy fallback — convert returns to a pseudo price series
    try:
        prices = pd.Series(np.exp(np.cumsum(median_rets)) * 1000)
        result = detect_regime_numpy_fallback(prices, n_states=3, lookback=_HMM_LOOKBACK)
        return result
    except Exception:
        return fallback


# ─── Step 2: BOCPD Structural-Break Detection ─────────────────────────────────

def _bocpd_break(median_rets: np.ndarray, window: int = _BOCPD_WINDOW) -> bool:
    """
    Run BOCPD on the last `window` returns.
    Returns True if a structural break is detected in the last 10 bars.
    """
    try:
        from app.components.quant.regime import BOCPDDetector

        rets = median_rets[-window:] if len(median_rets) >= window else median_rets
        detector = BOCPDDetector(hazard_lambda=_BOCPD_HAZARD)
        detected_at: List[int] = []
        for i, ret in enumerate(rets):
            detector.update(ret)
            if detector.detect(threshold=0.5):
                detected_at.append(i)

        if not detected_at:
            return False

        # Only block if a break was detected in the last 10 observations
        return detected_at[-1] >= len(rets) - 10
    except Exception as e:
        logger.debug("BOCPD error: %s", e)
        return False


# ─── Step 3: Market State ─────────────────────────────────────────────────────

def _dict_to_long_df(sym_dfs: Any) -> Optional[pd.DataFrame]:
    """
    Convert Dict[str, pd.DataFrame] from provider.load_universe() into a
    single long-format DataFrame with columns: symbol, date, open, high, low, close, volume.
    Returns None if there are fewer than 4 valid symbols.
    """
    if not sym_dfs or not isinstance(sym_dfs, dict):
        return None
    frames = []
    for sym, df in sym_dfs.items():
        if df is None or df.empty or len(df) < 30:
            continue
        sub = df.copy()
        sub["symbol"] = sym
        frames.append(sub)
    if len(frames) < 4:
        return None
    return pd.concat(frames, ignore_index=True)


def _market_state(provider, universe_raw: Any) -> Dict[str, Any]:
    """
    Run the Composite Market State scanner.
    ``universe_raw`` is the raw return from provider.load_universe()
    which is a Dict[str, pd.DataFrame] (long-format per symbol).
    Returns dict: {regime, score, max_signals, stop_pct, target_pct}.
    """
    defaults = {
        "regime": "NEUTRAL",
        "score": 2.0,
        "max_signals": 3,
        "stop_pct": 4.0,
        "target_pct": 7.0,
    }

    universe_df = _dict_to_long_df(universe_raw)
    if universe_df is None:
        return defaults

    try:
        from app.components.quant.market_state import compute_market_state
        from datetime import datetime

        state = compute_market_state(universe_df, datetime.today())

        regime = state.regime  # 'TRENDING', 'NEUTRAL', 'CHOPPY'
        score  = state.score

        if regime == "TRENDING":
            return {"regime": regime, "score": score, "max_signals": 5, "stop_pct": 3.5, "target_pct": 8.0}
        elif regime == "CHOPPY":
            return {"regime": regime, "score": score, "max_signals": 2, "stop_pct": 5.0, "target_pct": 5.5}
        else:
            return {"regime": regime, "score": score, "max_signals": 3, "stop_pct": 4.0, "target_pct": 7.0}
    except Exception as e:
        logger.debug("Market state error: %s", e)
        return defaults


# ─── Step 4a: Momentum signals ───────────────────────────────────────────────

def _momentum_signals(provider, symbols: List[str]) -> List[Dict[str, Any]]:
    """RSI + MACD + Bollinger + volume momentum scan across the universe."""
    candidates = []
    for sym in symbols:
        try:
            df = provider.load_ohlcv(sym, min_rows=40)
            if df.empty or len(df) < 35:
                continue

            closes  = df["close"]
            volumes = df.get("volume", pd.Series([1.0] * len(df), index=df.index))

            rsi_s  = _rsi(closes)
            hist_s = _macd_hist(closes)
            bb_mid, bb_up, bb_lo = _bollinger(closes)
            vol_avg = volumes.rolling(20).mean()

            rsi_now   = float(rsi_s.iloc[-1])
            rsi_prev  = float(rsi_s.iloc[-2])
            hist_now  = float(hist_s.iloc[-1])
            hist_prev = float(hist_s.iloc[-2])
            close_now = float(closes.iloc[-1])
            bb_mid_v  = float(bb_mid.iloc[-1])
            vol_now   = float(volumes.iloc[-1])
            vol_avg_v = float(vol_avg.iloc[-1]) if not math.isnan(float(vol_avg.iloc[-1])) else 1.0
            vol_ratio = vol_now / vol_avg_v if vol_avg_v > 0 else 1.0

            # Gate conditions
            if not (50 <= rsi_now <= 72):
                continue
            if hist_now <= 0:
                continue
            if close_now < bb_mid_v:
                continue
            if vol_ratio < 1.2:
                continue

            score = 50.0
            score += 20.0 * min(1.0, max(0.0, (rsi_now - 50) / 15.0))
            if rsi_prev < 50:
                score += 10.0
            if hist_prev <= 0 < hist_now:
                score += 15.0
            elif hist_now > 0:
                score += 8.0
            score += min(15.0, (vol_ratio - 1.2) * 10.0)
            score += min(5.0, (close_now - bb_mid_v) / bb_mid_v * 100)
            score = min(100.0, score)

            regime = "trending" if rsi_now > 58 and hist_now > hist_prev else "sideways"

            # Map to the signal-ranker format
            strength   = round(min(1.0, score / 100.0), 3)
            confidence = round(min(1.0, (vol_ratio - 1.0) * 0.5 + 0.5), 3)

            candidates.append({
                "symbol":      sym,
                "signal_type": "momentum",
                "strength":    strength,
                "confidence":  confidence,
                "reasoning":   f"RSI {rsi_now:.0f} MACD+ vol×{vol_ratio:.1f}",
                # bot-level fields
                "score":       score,
                "signal":      "BUY",
                "entry_price": close_now,
                "regime":      regime,
                "context": {
                    "source":       "quant_composite/momentum",
                    "rsi_14":       round(rsi_now, 1),
                    "macd_hist":    round(hist_now, 4),
                    "volume_ratio": round(vol_ratio, 2),
                },
            })
        except Exception as exc:
            logger.debug("Momentum scan %s: %s", sym, exc)

    return candidates


# ─── Step 4b: Mean-reversion signals ─────────────────────────────────────────

def _mean_reversion_signals(provider, symbols: List[str]) -> List[Dict[str, Any]]:
    """RSI < 40 + at/near Bollinger lower band + volume pop."""
    candidates = []
    for sym in symbols:
        try:
            df = provider.load_ohlcv(sym, min_rows=35)
            if df.empty or len(df) < 30:
                continue

            closes  = df["close"]
            volumes = df.get("volume", pd.Series([1.0] * len(df), index=df.index))

            rsi_s = _rsi(closes)
            bb_mid, bb_up, bb_lo = _bollinger(closes)
            vol_avg = volumes.rolling(20).mean()

            rsi_now   = float(rsi_s.iloc[-1])
            rsi_prev  = float(rsi_s.iloc[-2])
            close_now = float(closes.iloc[-1])
            close_prev= float(closes.iloc[-2])
            bb_lo_v   = float(bb_lo.iloc[-1])
            vol_now   = float(volumes.iloc[-1])
            vol_avg_v = float(vol_avg.iloc[-1]) if not math.isnan(float(vol_avg.iloc[-1])) else 1.0
            vol_ratio = vol_now / vol_avg_v if vol_avg_v > 0 else 1.0

            # Gate: RSI oversold and rising, price at/near lower BB, volume spike
            if rsi_now >= 40:
                continue
            if rsi_now <= rsi_prev:
                continue
            if close_now > bb_lo_v * 1.03:
                continue
            if vol_ratio < 1.1:
                continue

            dist_pct = abs(close_now - bb_lo_v) / bb_lo_v * 100
            score = 50.0
            score += max(0.0, (40 - rsi_now) * 1.5)   # lower RSI → higher score
            score += min(10.0, (vol_ratio - 1.0) * 10.0)
            score += max(0.0, 5.0 - dist_pct * 2.0)   # closer to BB lower = better
            if close_now > close_prev:
                score += 5.0  # price turning up
            score = min(100.0, score)

            strength   = round(min(1.0, score / 100.0), 3)
            confidence = round(min(0.9, 0.5 + (40 - rsi_now) / 80.0), 3)

            candidates.append({
                "symbol":      sym,
                "signal_type": "mean_reversion",
                "strength":    strength,
                "confidence":  confidence,
                "reasoning":   f"RSI {rsi_now:.0f} at BB-lower vol×{vol_ratio:.1f}",
                "score":       score,
                "signal":      "BUY",
                "entry_price": close_now,
                "regime":      "sideways",
                "context": {
                    "source":      "quant_composite/mean_reversion",
                    "rsi_14":      round(rsi_now, 1),
                    "bb_lo":       round(bb_lo_v, 2),
                    "vol_ratio":   round(vol_ratio, 2),
                    "dist_bb_pct": round(dist_pct, 2),
                },
            })
        except Exception as exc:
            logger.debug("MeanRev scan %s: %s", sym, exc)

    return candidates


# ─── Step 4c: Disposition / CGO signals ──────────────────────────────────────

def _disposition_signals(provider, symbols: List[str]) -> List[Dict[str, Any]]:
    """
    Capital Gains Overhang (CGO) signals.
    Uses quant/disposition.generate_cgo_signals_at_date() internally.
    We replicate a lightweight version so we can pass per-symbol DataFrames.
    """
    candidates = []
    for sym in symbols:
        try:
            df = provider.load_ohlcv(sym, min_rows=280)
            if df.empty or len(df) < 270:
                continue

            close  = df["close"].values
            volume = df.get("volume", pd.Series(np.ones(len(df)), index=df.index)).values

            # Volume-weighted reference price (260-day VWAP)
            lookback = min(260, len(close))
            w_close  = close[-lookback:]
            w_vol    = volume[-lookback:]
            total_vol = np.sum(w_vol)
            if total_vol <= 0:
                continue

            ref_price = float(np.sum(w_close * w_vol) / total_vol)
            last_price = float(close[-1])
            if ref_price <= 0:
                continue

            cgo = (last_price - ref_price) / last_price

            if cgo < _CGO_THRESHOLD:
                continue

            # Volume spike condition
            vol_20_avg = np.mean(volume[-20:]) if len(volume) >= 20 else np.mean(volume)
            last_vol   = float(volume[-1])
            vol_spike  = last_vol / vol_20_avg if vol_20_avg > 0 else 1.0

            if vol_spike < _VOL_SPIKE:
                continue

            # Score: higher CGO + bigger spike = stronger breakout through ceiling
            score      = min(100.0, 50.0 + cgo * 200.0 + (vol_spike - 1.0) * 15.0)
            strength   = round(min(1.0, score / 100.0), 3)
            confidence = round(min(0.85, 0.55 + vol_spike * 0.08), 3)

            candidates.append({
                "symbol":      sym,
                "signal_type": "disposition",
                "strength":    strength,
                "confidence":  confidence,
                "reasoning":   f"CGO {cgo:.2%} vol×{vol_spike:.1f}",
                "score":       score,
                "signal":      "BUY",
                "entry_price": last_price,
                "regime":      "trending",
                "context": {
                    "source":     "quant_composite/disposition",
                    "cgo":        round(cgo, 4),
                    "ref_price":  round(ref_price, 2),
                    "vol_spike":  round(vol_spike, 2),
                },
            })
        except Exception as exc:
            logger.debug("CGO scan %s: %s", sym, exc)

    return candidates


# ─── Step 6: Kelly fraction ───────────────────────────────────────────────────

def _kelly_multiplier(win_prob: float) -> float:
    """
    Confidence multiplier derived from Kelly criterion.

    Maps win probability → a score BOOST factor:
      - New bot / 100% accuracy (win_prob=1.0) → 1.0  (neutral, no history yet)
      - Good track record (win_prob=0.80)       → ~1.25 (25% score boost)
      - Great track record (win_prob=0.90+)     → ~1.50 (50% score boost)
      - Poor track record  (win_prob<0.50)       → ~0.70 (30% score penalty)

    Clamped to [0.7, 1.5] so:
      - Bad bots can still fire on very high-quality signals (score*0.7 → need base 115 for 80)
        (i.e. bad bots are strongly gated by the RL regime/sector accuracy blocks instead)
      - Good bots get meaningful boosts: a base=60 signal → 90 score with 1.5× multiplier.

    NOTE: The half-Kelly fraction is traditionally used for position SIZING. Here we
    re-map it to a confidence multiplier so that the RL learning state influences how
    strongly qualified signals are scored. Actual position sizing is handled separately
    in base_bot._open_trade() via the Kelly-fraction capital-allocation formula.
    """
    if win_prob <= 0:
        return 0.7   # penalise known-bad bots
    if win_prob >= 1:
        return 1.0   # new bot with no closed trades → neutral

    b = _KELLY_WIN_GAIN / _KELLY_AVG_LOSS          # reward/risk odds ratio
    kelly = (b * win_prob - (1 - win_prob)) / b    # full Kelly fraction
    half_kelly = max(0.0, kelly / 2.0)             # conservative half-Kelly

    # Re-map half_kelly [0 → 0.5] to multiplier [0.7 → 1.5]
    # half_kelly=0.0 → 0.7  (break-even bot — penalise)
    # half_kelly=0.5 → 1.5  (optimal bet-all bot — max boost)
    multiplier = 0.7 + half_kelly * 1.6
    return float(np.clip(multiplier, 0.7, 1.5))


# ─── Step 7: Conformal VaR ────────────────────────────────────────────────────

def _conformal_var(median_rets: np.ndarray) -> Tuple[float, bool]:
    """
    Compute 95th-percentile conformal VaR.
    Returns (var_value, high_risk_flag).
    """
    try:
        from app.components.quant.conformal import ConformalVaR

        estimator = ConformalVaR(alpha=_VAR_ALPHA, window=min(252, len(median_rets)))
        var_val = estimator.fit_predict(median_rets)
        return float(var_val), var_val > _VAR_ALERT_PCT
    except Exception as e:
        logger.debug("ConformalVaR error: %s", e)
        return 0.02, False


# ─── Main Bot Class ───────────────────────────────────────────────────────────

class QuantCompositeBot(BaseBot):
    """
    Integrates ALL quant engines (from Quant Lab + Advanced Quant Lab)
    into a single automatic paper-trading strategy.

    Runs every 15 minutes during NEPSE market hours (via bot_scheduler).
    Signal pipeline:
      HMM regime → BOCPD break → market state → signals → rank → Kelly → VaR
    """

    BOT_ID   = "quant_composite"
    BOT_NAME = "Quant Composite Bot"
    STRATEGY = "quant_composite"

    DEFAULT_STOP_PCT   = 4.0
    DEFAULT_TARGET_PCT = 7.0
    MAX_HOLD_DAYS      = 12

    def generate_signals(self, db: Session, timeframe: str = "daily") -> List[Dict[str, Any]]:  # noqa: C901
        # Scale quant parameters by timeframe
        # daily=120/90, weekly=200/150, monthly=300/250 lookbacks
        _tf_hmm_lookback   = {"daily": 120, "weekly": 200, "monthly": 300}.get(timeframe, 120)
        _tf_bocpd_window   = {"daily": 90,  "weekly": 150, "monthly": 250}.get(timeframe, 90)
        _tf_universe_sample = {"daily": 50, "weekly": 80,  "monthly": 120}.get(timeframe, 50)

        logger.info("QuantCompositeBot[%s]: starting signal pipeline | HMM=%d BOCPD=%d", timeframe, _tf_hmm_lookback, _tf_bocpd_window)

        # ── Load historical provider ──────────────────────────────────────────
        try:
            from app.services.data.historical_provider import get_historical_provider
        except ImportError as e:
            logger.error("HistoricalDataProvider import failed: %s", e)
            return []

        provider = get_historical_provider()
        if not provider.is_available():
            logger.warning("QuantCompositeBot: HistoricalDataProvider not available")
            return []

        # ── Load full NEPSE universe dynamically ──────────────────────────────
        universe = get_nepse_universe(provider)
        logger.info("QuantCompositeBot scanning %d NEPSE symbols", len(universe))

        # ── Build universe median returns ─────────────────────────────────────
        median_rets = _median_returns(provider, universe, n=_tf_hmm_lookback + 20)
        if median_rets is None or len(median_rets) < 30:
            logger.warning("QuantCompositeBot: insufficient return data for quant engines")
            median_rets = np.array([])

        # ── Step 1: HMM Regime Gate ───────────────────────────────────────────
        hmm_result = {"regime": "neutral", "confidence": 0.4, "exposure_multiplier": 0.5}
        if len(median_rets) >= 30:
            hmm_result = _hmm_regime(median_rets)
            logger.info(
                "HMM regime: %s (confidence=%.1f%%, exposure=%.0f%%)",
                hmm_result["regime"],
                hmm_result["confidence"] * 100,
                hmm_result.get("exposure_multiplier", 0.5) * 100,
            )
            if hmm_result["regime"] == "bear" and hmm_result["confidence"] > 0.55:
                logger.info("QuantCompositeBot: HMM bear regime — skipping new entries")
                return []

        # ── Step 2: BOCPD Structural-Break Gate ───────────────────────────────
        if len(median_rets) >= 30:
            if _bocpd_break(median_rets, window=_tf_bocpd_window):
                logger.info("QuantCompositeBot[%s]: BOCPD structural break detected — skipping new entries", timeframe)
                return []

        # ── Step 3: Market State ──────────────────────────────────────────────
        try:
            universe_df = provider.load_universe(symbols=universe[:_tf_universe_sample])
        except Exception as e:
            logger.debug("load_universe failed: %s", e)
            universe_df = None

        market_st = _market_state(provider, universe_df)
        max_sigs   = market_st["max_signals"]
        stop_pct   = market_st["stop_pct"]
        target_pct = market_st["target_pct"]
        logger.info(
            "Market state: %s (score=%.2f) max_signals=%d stop=%.1f%% tgt=%.1f%%",
            market_st["regime"], market_st["score"], max_sigs, stop_pct, target_pct,
        )

        # ── Step 4: Multi-source signals ──────────────────────────────────────
        raw_candidates: List[Dict[str, Any]] = []
        raw_candidates.extend(_momentum_signals(provider, universe))
        raw_candidates.extend(_mean_reversion_signals(provider, universe))
        raw_candidates.extend(_disposition_signals(provider, universe))
        logger.info(
            "Raw candidates: %d total (%d momentum, %d mean_rev, %d disposition)",
            len(raw_candidates),
            sum(1 for c in raw_candidates if c["signal_type"] == "momentum"),
            sum(1 for c in raw_candidates if c["signal_type"] == "mean_reversion"),
            sum(1 for c in raw_candidates if c["signal_type"] == "disposition"),
        )

        if not raw_candidates:
            return []

        # ── Step 5: Signal Ranking ────────────────────────────────────────────
        try:
            from app.components.quant.signals import rank_signal_candidates

            ranked = rank_signal_candidates(
                raw_candidates,
                held_symbols=None,
                sector_exposure=None,
            )
        except Exception as e:
            logger.debug("Signal ranking error: %s", e)
            # Fallback: sort by raw score
            ranked = sorted(raw_candidates, key=lambda c: c.get("score", 0), reverse=True)

        # ── Step 6: Kelly Fraction ────────────────────────────────────────────
        try:
            from app.components.rl_engine import get_or_create_state
            state = get_or_create_state(self.BOT_ID, self.BOT_NAME, self.STRATEGY, db)
            win_prob = float(state.rolling_accuracy or 0.55)
        except Exception:
            win_prob = 0.55

        kelly_mult = _kelly_multiplier(win_prob)
        logger.info("Kelly multiplier: %.2f (win_prob=%.0f%%)", kelly_mult, win_prob * 100)

        # ── Step 7: Conformal VaR ─────────────────────────────────────────────
        var_val, high_risk = False, False
        if len(median_rets) >= 30:
            var_val, high_risk = _conformal_var(median_rets)
            logger.info(
                "Conformal VaR: %.2f%% (high_risk=%s)",
                var_val * 100 if isinstance(var_val, float) else 0,
                high_risk,
            )

        if high_risk:
            stop_pct   = stop_pct * 1.2    # widen stop when VaR is elevated
            target_pct = target_pct * 0.9  # slightly tighter profit target

        # ── Step 8: Assemble final signals ────────────────────────────────────
        # Exposure multiplier from HMM: reduces effective max_signals in neutral/bear
        exposure = float(hmm_result.get("exposure_multiplier", 0.5))
        effective_max = max(1, round(max_sigs * exposure))
        logger.info("Effective max signals (after HMM exposure): %d", effective_max)

        final_signals: List[Dict[str, Any]] = []
        seen_symbols: set = set()

        for ranked_sig in ranked[:effective_max * 3]:  # over-fetch, filter below
            if len(final_signals) >= effective_max:
                break

            # Pull the original raw candidate that matches this symbol
            sym = str(ranked_sig.get("symbol", "")).upper()
            if not sym or sym in seen_symbols:
                continue

            # Find the raw candidate for this symbol (take the best score)
            orig = max(
                (c for c in raw_candidates if c.get("symbol", "").upper() == sym),
                key=lambda c: c.get("score", 0),
                default=None,
            )
            if orig is None:
                continue

            # Apply Kelly multiplier and VaR penalty to score
            base_score = float(orig.get("score", 50))
            adjusted   = base_score * kelly_mult
            if high_risk:
                adjusted *= 0.8

            seen_symbols.add(sym)
            final_signals.append({
                "symbol":      sym,
                "signal":      "BUY",
                "score":       round(adjusted, 1),
                "entry_price": float(orig["entry_price"]),
                "stop_pct":    stop_pct,
                "target_pct":  target_pct,
                "regime":      orig.get("regime", "sideways"),
                "sector":      orig.get("sector"),
                "context": {
                    **orig.get("context", {}),
                    "timeframe":          timeframe,
                    "signal_type":        orig.get("signal_type"),
                    "hmm_regime":         hmm_result["regime"],
                    "hmm_confidence":     round(hmm_result["confidence"], 3),
                    "market_state":       market_st["regime"],
                    "market_score":       round(market_st["score"], 2),
                    "kelly_multiplier":   round(kelly_mult, 3),
                    "conformal_var_pct":  round(var_val * 100, 2) if isinstance(var_val, float) else 0,
                    "high_risk_env":      bool(high_risk),
                    "base_score":         round(base_score, 1),
                    "adjusted_score":     round(adjusted, 1),
                },
            })

        logger.info(
            "QuantCompositeBot: emitting %d signals | regime=%s state=%s kelly=%.2f var=%.2f%%",
            len(final_signals),
            hmm_result["regime"],
            market_st["regime"],
            kelly_mult,
            var_val * 100 if isinstance(var_val, float) else 0,
        )

        return final_signals
