"""
RL Engine (Contextual Bandit + EMA Accuracy Tracker)
=====================================================

After each trade closes, the engine:
1. Computes the outcome (WIN / LOSS / TIMEOUT).
2. Updates rolling_accuracy via EMA (alpha=0.2).
3. Adjusts current_threshold:
       accuracy ≥ 85% → relax threshold (min 75)
       accuracy < 80% → tighten threshold (max 92)
4. Updates sector_accuracy, regime_accuracy, sector_counts, regime_counts.
   Sector names are NORMALISED (lowercase, stripped) before storage so that
   "Hydro Power" and "Hydropower" collapse to the same key.
5. Updates signal_weights: tracks win-rate per score bracket (high/med/low).
   This allows the RL to learn that, e.g., high-score signals in this bot
   actually lose more often — and gate future signals in that bracket.
6. If the trade was a LOSS / TIMEOUT:
       - runs post-mortem to identify likely mistake
       - appends entry to mistakes_log (cap at 20)
       - sets last_lesson
7. Persists updated BotLearningState to DB.

Signal gating (evaluate_signal):
---------------------------------
Before opening any new trade, call evaluate_signal(sig, state).
It checks:
  a. Rolling accuracy gate (< 80% → block, requires ≥ 5 total trades)
  b. Sector accuracy gate  (< 60% AND ≥ 3 trades in sector → block)
  c. Regime accuracy gate  (< 60% AND ≥ 3 trades in regime → block)
  d. Score-bracket win-rate gate (< 40% AND ≥ 4 trades in bracket → block)
     Brackets: "high" (score ≥ 90), "med" (80–89), "low" (< 80)
Returns (allowed: bool, reason: str).

Sector normalisation:
---------------------
All sector names are normalised before storage and lookup:
  _norm_sector("Hydro Power")  → "hydro power"
  _norm_sector("Hydropower")   → "hydropower"

  These remain distinct after strip+lower — the API may return either form.
  To collapse them fully use _canon_sector() which applies common aliases
  (e.g. any "hydro*" variant maps to "Hydro Power" canonical form).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.paper_trade import PaperTrade, TradeOutcome
from app.models.bot_learning_state import BotLearningState

logger = logging.getLogger(__name__)

# EMA smoothing factor — alpha=0.2 → ~5-trade memory horizon
_EMA_ALPHA = 0.2

# Accuracy thresholds for adaptive score threshold
_ACCURACY_RELAX   = 0.85   # above this: allow lower-confidence signals
_ACCURACY_TIGHTEN = 0.80   # below this: demand higher-confidence signals
_THRESHOLD_MIN    = 75.0
_THRESHOLD_MAX    = 92.0
_THRESHOLD_STEP   =  2.0

# Minimum trade count before sector/regime/bracket accuracy is trusted
_MIN_SECTOR_SAMPLES  = 3
_MIN_REGIME_SAMPLES  = 3
_MIN_BRACKET_SAMPLES = 4   # need at least 4 trades in a score bracket to gate

# Accuracy floors below which we skip signals
_SECTOR_FLOOR  = 0.60
_REGIME_FLOOR  = 0.60
_BRACKET_FLOOR = 0.40   # lower floor — only block if genuinely terrible

# Score bracket definitions
_BRACKET_HIGH = 90.0
_BRACKET_MED  = 80.0

# ── Canonical sector aliases ─────────────────────────────────────────────────
# Maps any raw sector string (after lower-casing) to a canonical display name.
# Add more as new inconsistencies appear.
_SECTOR_ALIASES: Dict[str, str] = {
    "hydropower":                   "Hydro Power",
    "hydro power":                  "Hydro Power",
    "hydro":                        "Hydro Power",
    "hydroelectric":                "Hydro Power",
    "life insurance":               "Life Insurance",
    "lifeinsurance":                "Life Insurance",
    "non life insurance":           "Non Life Insurance",
    "non-life insurance":           "Non Life Insurance",
    "nonlife insurance":            "Non Life Insurance",
    "commercial bank":              "Commercial Banks",
    "commercial banks":             "Commercial Banks",
    "commercialbank":               "Commercial Banks",
    "development bank":             "Development Banks",
    "development banks":            "Development Banks",
    "developmentbank":              "Development Banks",
    "microfinance":                 "Microfinance",
    "micro finance":                "Microfinance",
    "mutual fund":                  "Mutual Fund",
    "mutualfund":                   "Mutual Fund",
    "finance":                      "Finance",
    "finance company":              "Finance",
    "hotel and tourism":            "Hotel And Tourism",
    "hotel & tourism":              "Hotel And Tourism",
    "hotels and tourism":           "Hotel And Tourism",
    "manufacturing and processing": "Manufacturing And Processing",
    "manufacturing":                "Manufacturing And Processing",
    "trading":                      "Trading",
    "others":                       "Other",
    "other":                        "Other",
}


def _canon_sector(raw: Optional[str]) -> str:
    """
    Normalise a raw sector name to a canonical form.

    Steps:
      1. Strip whitespace, lower-case.
      2. Look up in _SECTOR_ALIASES (common variant→canonical mapping).
      3. If not found, title-case the stripped-lower string as fallback.

    Examples:
      "Hydropower"   → "Hydro Power"
      "Hydro Power"  → "Hydro Power"
      "mutual fund"  → "Mutual Fund"
      "MICROFINANCE" → "Microfinance"
      "Unknown XYZ"  → "Unknown Xyz"  (title-case fallback)
    """
    if not raw:
        return "Other"
    key = raw.strip().lower()
    # Remove redundant internal whitespace
    key = re.sub(r"\s+", " ", key)
    canonical = _SECTOR_ALIASES.get(key)
    if canonical:
        return canonical
    # Title-case fallback — at least consistent capitalisation
    return raw.strip().title()


def _score_bracket(score: Optional[float]) -> str:
    """Return 'high', 'med', or 'low' based on signal score."""
    if score is None:
        return "low"
    if score >= _BRACKET_HIGH:
        return "high"
    if score >= _BRACKET_MED:
        return "med"
    return "low"


def _ema(old: float, new_val: float, alpha: float = _EMA_ALPHA) -> float:
    return alpha * new_val + (1.0 - alpha) * old


def get_or_create_state(bot_id: str, bot_name: str, strategy: str, db: Session) -> BotLearningState:
    state = db.query(BotLearningState).filter(BotLearningState.bot_id == bot_id).first()
    if state is None:
        state = BotLearningState(
            bot_id=bot_id,
            bot_name=bot_name,
            strategy=strategy,
            rolling_accuracy=1.0,
            current_threshold=80.0,
        )
        db.add(state)
        db.flush()
    return state


def evaluate_signal(sig: Dict[str, Any], state: BotLearningState) -> Tuple[bool, str]:
    """
    Decide whether a signal should be traded given the bot's current learning state.

    Returns (allowed, reason_string).

    Gates (in order):
      1. Rolling accuracy < 80% with ≥ 5 total trades → block.
      2. Sector accuracy < 60% with ≥ 3 trades in that sector → block.
      3. Regime accuracy < 60% with ≥ 3 trades in that regime → block.
      4. Score-bracket win rate < 40% with ≥ 4 trades in bracket → block.
    """
    # ── Gate 1: overall rolling accuracy ──────────────────────────────────
    if state.total_trades >= 5 and state.rolling_accuracy < _ACCURACY_TIGHTEN:
        return False, f"accuracy={state.rolling_accuracy:.0%} < 80% (overall)"

    # ── Gate 2: sector accuracy ────────────────────────────────────────────
    raw_sector = sig.get("sector")
    sector = _canon_sector(raw_sector) if raw_sector else None
    if sector and sector != "Other":
        sec_acc: Dict[str, float] = dict(state.sector_accuracy or {})
        sec_cnt: Dict[str, int]   = dict(state.sector_counts  or {})
        sec_n   = sec_cnt.get(sector, 0)
        sec_val = sec_acc.get(sector)
        if sec_val is not None and sec_n >= _MIN_SECTOR_SAMPLES and sec_val < _SECTOR_FLOOR:
            return False, (
                f"sector '{sector}' accuracy={sec_val:.0%} < 60% "
                f"over {sec_n} trades — skipping until sector improves"
            )

    # ── Gate 3: regime accuracy ────────────────────────────────────────────
    regime = sig.get("regime")
    if regime and regime != "unknown":
        reg_acc: Dict[str, float] = dict(state.regime_accuracy or {})
        reg_cnt: Dict[str, int]   = dict(state.regime_counts   or {})
        reg_n   = reg_cnt.get(regime, 0)
        reg_val = reg_acc.get(regime)
        if reg_val is not None and reg_n >= _MIN_REGIME_SAMPLES and reg_val < _REGIME_FLOOR:
            return False, (
                f"regime '{regime}' accuracy={reg_val:.0%} < 60% "
                f"over {reg_n} trades — avoiding this market condition"
            )

    # ── Gate 4: score-bracket win rate ────────────────────────────────────
    bracket = _score_bracket(sig.get("score"))
    weights: Dict[str, Any] = dict(state.signal_weights or {})
    bkt_acc = weights.get(f"{bracket}_acc")
    bkt_cnt = weights.get(f"{bracket}_cnt", 0)
    if (
        bkt_acc is not None
        and bkt_cnt >= _MIN_BRACKET_SAMPLES
        and bkt_acc < _BRACKET_FLOOR
    ):
        return False, (
            f"score bracket '{bracket}' (score≥"
            f"{'90' if bracket=='high' else '80' if bracket=='med' else '0'}) "
            f"win rate={bkt_acc:.0%} < 40% over {bkt_cnt} trades — "
            f"signals in this confidence range are underperforming"
        )

    return True, "ok"


def _post_mortem(trade: PaperTrade) -> str:
    """Generate a human-readable analysis of why the trade failed."""
    ctx: Dict[str, Any] = trade.signal_context or {}
    reasons = []

    outcome = trade.outcome
    if outcome == TradeOutcome.TIMEOUT:
        reasons.append("Trade did not reach target within the hold period — price moved sideways.")

    if trade.signal_score and trade.signal_score < 82:
        reasons.append(
            f"Signal score ({trade.signal_score:.1f}) was close to the entry threshold — "
            "marginal confidence signals have higher failure rates."
        )

    regime = trade.regime_at_entry or ctx.get("regime", "unknown")
    if regime == "sideways":
        reasons.append(
            "Market was in a sideways / ranging regime at entry — "
            "trend-following signals have lower accuracy in ranging markets."
        )
    elif regime == "volatile":
        reasons.append(
            "High volatility at entry caused wide spreads and stop-hunts — "
            "consider wider stops or waiting for regime to stabilise."
        )

    zone = ctx.get("zone")
    trend = ctx.get("trend")
    if zone == "equilibrium":
        reasons.append(
            "Price was at equilibrium (42–58% of range) — "
            "entries at equilibrium carry more risk than discount/premium zone entries."
        )
    if trend == "sideways":
        reasons.append(
            "No clear BOS trend at entry — sideways BOS means institutional direction was ambiguous."
        )

    pnl = trade.pnl_pct or 0.0
    if outcome == TradeOutcome.LOSS and pnl < -3.0:
        reasons.append(
            f"Large loss ({pnl:.1f}%) — stop-loss may have been too wide, "
            "or a sudden gap-down overrode the stop."
        )

    if not reasons:
        reasons.append("No specific pattern identified — trade was within normal variance.")

    return " | ".join(reasons)


def process_closed_trade(trade: PaperTrade, db: Session) -> BotLearningState:
    """
    Called after a paper trade is closed.
    Updates the bot's learning state based on the outcome.
    Returns the updated BotLearningState.
    """
    state = get_or_create_state(trade.bot_id, trade.bot_name, trade.strategy, db)

    # ── 1. Update counters ────────────────────────────────────────────────
    state.total_trades += 1
    is_win = trade.outcome == TradeOutcome.WIN
    if trade.outcome == TradeOutcome.WIN:
        state.wins += 1
    elif trade.outcome == TradeOutcome.LOSS:
        state.losses += 1
    else:
        state.timeouts += 1

    state.last_trade_at = datetime.now(timezone.utc)

    # ── 2. Update rolling accuracy (EMA) ─────────────────────────────────
    outcome_val = 1.0 if is_win else 0.0
    state.rolling_accuracy = _ema(state.rolling_accuracy, outcome_val)

    # ── 3. Adjust entry threshold adaptively ─────────────────────────────
    acc = state.rolling_accuracy
    if acc >= _ACCURACY_RELAX:
        # Doing well → relax slightly to capture more trades
        state.current_threshold = max(_THRESHOLD_MIN, state.current_threshold - _THRESHOLD_STEP)
    elif acc < _ACCURACY_TIGHTEN:
        # Below target → be more selective
        state.current_threshold = min(_THRESHOLD_MAX, state.current_threshold + _THRESHOLD_STEP)

    # ── 4. Sector accuracy + count update (with canonical normalisation) ──
    raw_sector = trade.sector
    sector = _canon_sector(raw_sector) if raw_sector else None
    if sector and sector != "Other":
        sec_acc: Dict[str, Any] = dict(state.sector_accuracy or {})
        sec_cnt: Dict[str, int] = dict(state.sector_counts   or {})

        old_sec = sec_acc.get(sector, 0.75)
        sec_acc[sector] = round(_ema(old_sec, outcome_val), 3)
        sec_cnt[sector] = sec_cnt.get(sector, 0) + 1

        state.sector_accuracy = sec_acc
        state.sector_counts   = sec_cnt

    # ── 5. Regime accuracy + count update ────────────────────────────────
    regime = trade.regime_at_entry or (trade.signal_context or {}).get("regime", "unknown")
    if regime and regime != "unknown":
        reg_acc: Dict[str, Any] = dict(state.regime_accuracy or {})
        reg_cnt: Dict[str, int] = dict(state.regime_counts   or {})

        old_reg = reg_acc.get(regime, 0.75)
        reg_acc[regime] = round(_ema(old_reg, outcome_val), 3)
        reg_cnt[regime] = reg_cnt.get(regime, 0) + 1

        state.regime_accuracy = reg_acc
        state.regime_counts   = reg_cnt

    # ── 6. Signal-weights: track win rate per score bracket ───────────────
    #
    # Brackets: "high" (score ≥ 90), "med" (score 80–89), "low" (score < 80)
    # Stored as flat keys in signal_weights dict:
    #   {high_acc: 0.72, high_cnt: 5, med_acc: 0.60, med_cnt: 3, ...}
    #
    # evaluate_signal() uses these to gate signals in badly-performing brackets.
    bracket = _score_bracket(trade.signal_score)
    weights: Dict[str, Any] = dict(state.signal_weights or {})
    acc_key = f"{bracket}_acc"
    cnt_key = f"{bracket}_cnt"

    old_bkt = weights.get(acc_key, 0.75)
    weights[acc_key] = round(_ema(old_bkt, outcome_val), 3)
    weights[cnt_key] = weights.get(cnt_key, 0) + 1

    state.signal_weights = weights

    # ── 7. Post-mortem for failures ───────────────────────────────────────
    if not is_win:
        analysis = _post_mortem(trade)
        lesson = (
            f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d')}] "
            f"{trade.symbol} {trade.strategy} {trade.outcome.value}: {analysis}"
        )
        state.last_lesson = lesson

        # Update trade with analysis
        trade.mistake_analysis = analysis

        # Prepend to mistakes_log (keep last 20)
        log: list = list(state.mistakes_log or [])
        log.insert(0, {
            "date":     datetime.now(timezone.utc).isoformat(),
            "symbol":   trade.symbol,
            "strategy": trade.strategy,
            "outcome":  trade.outcome.value,
            "pnl_pct":  trade.pnl_pct,
            "score":    trade.signal_score,
            "bracket":  bracket,
            "sector":   sector or trade.sector,
            "regime":   regime,
            "analysis": analysis,
        })
        state.mistakes_log = log[:20]
    else:
        state.last_lesson = (
            f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d')}] "
            f"{trade.symbol} WIN +{trade.pnl_pct:.1f}% — signal score {trade.signal_score:.0f}, "
            f"regime: {regime}. Accuracy: {acc:.0%}"
        )

    logger.info(
        "RL update | bot=%s sym=%s outcome=%s acc=%.2f threshold=%.0f bracket=%s bkt_acc=%.2f",
        trade.bot_id, trade.symbol, trade.outcome.value,
        state.rolling_accuracy, state.current_threshold,
        bracket, weights.get(acc_key, 0.75),
    )

    db.add(state)
    db.commit()
    db.refresh(state)
    return state


def rebuild_sector_accuracy_from_history(bot_id: str, db: Session) -> BotLearningState:
    """
    Utility: recompute sector_accuracy / sector_counts from closed trade history
    using canonical sector normalisation.

    Call this once to fix bots that have split sector keys (e.g. "Hydropower"
    vs "Hydro Power") due to inconsistent API responses.

    Returns the updated BotLearningState.
    """
    from app.models.paper_trade import PaperTrade

    state = db.query(BotLearningState).filter(BotLearningState.bot_id == bot_id).first()
    if state is None:
        logger.warning("rebuild_sector_accuracy: no state found for %s", bot_id)
        return state

    closed_trades = (
        db.query(PaperTrade)
        .filter(PaperTrade.bot_id == bot_id, PaperTrade.is_open == False)
        .order_by(PaperTrade.close_date.asc())
        .all()
    )

    # Reset and replay
    sec_acc: Dict[str, float] = {}
    sec_cnt: Dict[str, int]   = {}

    for t in closed_trades:
        if not t.sector:
            continue
        sector = _canon_sector(t.sector)
        if sector == "Other":
            continue
        is_win = t.outcome == TradeOutcome.WIN
        val = 1.0 if is_win else 0.0
        old = sec_acc.get(sector, 0.75)
        sec_acc[sector] = round(_ema(old, val), 3)
        sec_cnt[sector] = sec_cnt.get(sector, 0) + 1

    state.sector_accuracy = sec_acc
    state.sector_counts   = sec_cnt
    db.add(state)
    db.commit()
    db.refresh(state)
    logger.info(
        "rebuild_sector_accuracy[%s]: rebuilt from %d closed trades → %d sectors: %s",
        bot_id, len(closed_trades), len(sec_acc), sec_acc,
    )
    return state
