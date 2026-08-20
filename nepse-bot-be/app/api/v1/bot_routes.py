"""
Bot API Routes
==============
REST endpoints for the paper-trading bot system.

GET  /api/v1/bots/                    → list all bots + their learning state
GET  /api/v1/bots/{bot_id}/state      → single bot RL state
GET  /api/v1/bots/{bot_id}/trades     → open + recent closed trades
GET  /api/v1/bots/{bot_id}/trades/history?limit=50  → closed trade history
POST /api/v1/bots/{bot_id}/run        → trigger one manual bot cycle
POST /api/v1/bots/run-all             → trigger all bots manually
GET  /api/v1/bots/summary             → aggregate stats across all bots
POST /api/v1/bots/{bot_id}/reset      → reset learning state (wipes RL weights)
"""

from __future__ import annotations

import logging
from datetime import timezone as _utc
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.paper_trade import PaperTrade, TradeOutcome
from app.models.bot_learning_state import BotLearningState
from app.components.bots import BOT_REGISTRY
from app.components.rl_engine import get_or_create_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bots", tags=["Paper Trading Bots"])


# ── Bot registry metadata ──────────────────────────────────────────────────

_BOT_META = {
    "smc_bot": {
        "id": "smc_bot",
        "name": "SMC Bot",
        "strategy": "smc",
        "description": "Smart Money Concepts — Order Blocks, Break of Structure, Fair Value Gaps, Liquidity Sweeps",
    },
    "reco_bot": {
        "id": "reco_bot",
        "name": "Recommendation Bot",
        "strategy": "recommendation",
        "description": "Multi-factor scoring — Trend + Momentum + Volume + Volatility + Drawdown",
    },
    "momentum_bot": {
        "id": "momentum_bot",
        "name": "Momentum Bot",
        "strategy": "momentum",
        "description": "RSI crossover + MACD histogram + Bollinger Band breakout + Volume confirmation",
    },
    "ema_crossover_bot": {
        "id": "ema_crossover_bot",
        "name": "EMA Crossover Bot",
        "strategy": "ema_crossover",
        "description": "EMA(9) crosses EMA(21) with price above EMA(50) and 1.5× volume confirmation — trend-following",
    },
    "mean_reversion_bot": {
        "id": "mean_reversion_bot",
        "name": "Mean Reversion Bot",
        "strategy": "mean_reversion",
        "description": "RSI < 38 + rising, near lower Bollinger Band, volume spike — oversold bounce setup",
    },
    "sector_rotation_bot": {
        "id": "sector_rotation_bot",
        "name": "Sector Rotation Bot",
        "strategy": "sector_rotation",
        "description": "Follows live sector index momentum — buys strongest stocks in today's top-gaining NEPSE sectors",
    },
    "volume_breakout_bot": {
        "id": "volume_breakout_bot",
        "name": "Volume Breakout Bot",
        "strategy": "volume_breakout",
        "description": "Detects unusual volume spikes (≥ 2.5×) with price breaking above the 20-day high — smart money signal",
    },
    "quant_composite": {
        "id": "quant_composite",
        "name": "Quant Composite Bot",
        "strategy": "quant_composite",
        "description": "HMM regime + BOCPD changepoint + composite market state + conformal VaR + multi-source signal ranking + Kelly sizing — fully quantitative",
    },
}

# Dynamic universe size — computed once and cached from the historical DB / live market
_dynamic_universe_size: int = 0

def _get_universe_size() -> int:
    global _dynamic_universe_size
    if _dynamic_universe_size > 0:
        return _dynamic_universe_size
    try:
        from app.components.bots.nepse_universe import get_nepse_universe
        _dynamic_universe_size = len(get_nepse_universe())
    except Exception:
        _dynamic_universe_size = 372  # fallback: approximate NEPSE listing count
    return _dynamic_universe_size


# ── GET /bots/ ─────────────────────────────────────────────────────────────

@router.get("/")
def list_bots(db: Session = Depends(get_db)):
    """Return all bots with their current RL learning state."""
    bots = []
    for bot_id, meta in _BOT_META.items():
        BotClass = BOT_REGISTRY.get(meta["strategy"])
        if BotClass is None:
            continue
        state = get_or_create_state(bot_id, meta["name"], meta["strategy"], db)
        db.commit()

        # Open trade count
        open_count = db.query(PaperTrade).filter(
            PaperTrade.bot_id == bot_id,
            PaperTrade.is_open == True,
        ).count()

        bots.append({
            **meta,
            "universe_size": _get_universe_size(),
            "learning_state": state.to_dict(),
            "open_positions": open_count,
        })

    return {"count": len(bots), "bots": bots}


# ── GET /bots/{bot_id}/state ───────────────────────────────────────────────

@router.get("/{bot_id}/state")
def get_bot_state(bot_id: str, db: Session = Depends(get_db)):
    """Detailed RL state for a single bot."""
    meta = _BOT_META.get(bot_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Bot '{bot_id}' not found")

    state = get_or_create_state(bot_id, meta["name"], meta["strategy"], db)
    db.commit()
    return {"bot_id": bot_id, "state": state.to_dict()}


# ── GET /bots/{bot_id}/trades ──────────────────────────────────────────────

@router.get("/{bot_id}/trades")
def get_bot_trades(
    bot_id: str,
    include_closed: bool = Query(True),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Open positions + recent closed trades for a bot."""
    if bot_id not in _BOT_META:
        raise HTTPException(status_code=404, detail=f"Bot '{bot_id}' not found")

    q = db.query(PaperTrade).filter(PaperTrade.bot_id == bot_id)
    if not include_closed:
        q = q.filter(PaperTrade.is_open == True)

    trades = q.order_by(PaperTrade.created_at.desc()).limit(limit).all()

    return {
        "bot_id": bot_id,
        "count": len(trades),
        "trades": [t.to_dict() for t in trades],
    }


# ── GET /bots/{bot_id}/trades/history ─────────────────────────────────────

@router.get("/{bot_id}/trades/history")
def get_trade_history(
    bot_id: str,
    limit: int = Query(50, ge=1, le=200),
    timeframe: Optional[str] = Query(None, description="Filter: daily | weekly | monthly"),
    db: Session = Depends(get_db),
):
    """Closed trade history with full P&L analytics (% and NPR)."""
    if bot_id not in _BOT_META:
        raise HTTPException(status_code=404, detail=f"Bot '{bot_id}' not found")

    q = db.query(PaperTrade).filter(
        PaperTrade.bot_id == bot_id,
        PaperTrade.is_open == False,
    )
    if timeframe:
        q = q.filter(PaperTrade.timeframe == timeframe)

    trades = q.order_by(PaperTrade.close_date.desc()).limit(limit).all()

    wins     = [t for t in trades if t.outcome == TradeOutcome.WIN]
    losses   = [t for t in trades if t.outcome == TradeOutcome.LOSS]
    timeouts = [t for t in trades if t.outcome == TradeOutcome.TIMEOUT]
    total    = len(trades)

    avg_win_pct  = sum(t.pnl_pct or 0 for t in wins)   / len(wins)   if wins   else 0.0
    avg_loss_pct = sum(t.pnl_pct or 0 for t in losses) / len(losses) if losses else 0.0
    total_pnl_pct = sum(t.pnl_pct or 0 for t in trades)
    total_pnl_nrs = sum(t.pnl_nrs or 0 for t in trades)
    total_win_nrs  = sum(t.pnl_nrs or 0 for t in wins)
    total_loss_nrs = sum(t.pnl_nrs or 0 for t in losses)

    # ── Derived quality / risk metrics ────────────────────────────────────────
    abs_loss_nrs  = abs(total_loss_nrs)
    abs_loss_pct  = abs(avg_loss_pct)
    # Profit factor: how many rupees won per rupee lost (> 1 = profitable)
    profit_factor = round(total_win_nrs / abs_loss_nrs, 2) if abs_loss_nrs > 0 else None
    # Risk-reward ratio: avg win size vs avg loss size
    rr_ratio      = round(avg_win_pct / abs_loss_pct, 2) if abs_loss_pct > 0 else None
    # Expectancy: expected gain per trade in %
    win_rate_dec  = len(wins) / total if total else 0.0
    expectancy_pct = round(
        win_rate_dec * avg_win_pct + (1 - win_rate_dec) * avg_loss_pct, 2
    ) if total else 0.0

    # Average hold days (calendar) for wins vs losses
    def _days(t) -> int:
        if t.close_date and t.entry_date:
            cd = t.close_date if t.close_date.tzinfo else t.close_date.replace(tzinfo=_utc.utc)
            ed = t.entry_date if t.entry_date.tzinfo  else t.entry_date.replace(tzinfo=_utc.utc)
            return max(0, (cd - ed).days)
        return 0
    avg_hold_days_win  = round(sum(_days(t) for t in wins)   / len(wins),   1) if wins   else 0.0
    avg_hold_days_loss = round(sum(_days(t) for t in losses) / len(losses), 1) if losses else 0.0

    # Per-timeframe breakdown
    tf_groups: dict = {}
    for t in trades:
        tf = t.timeframe or "daily"
        if tf not in tf_groups:
            tf_groups[tf] = {"trades": 0, "wins": 0, "pnl_nrs": 0.0}
        tf_groups[tf]["trades"] += 1
        if t.outcome == TradeOutcome.WIN:
            tf_groups[tf]["wins"] += 1
        tf_groups[tf]["pnl_nrs"] = round(tf_groups[tf]["pnl_nrs"] + (t.pnl_nrs or 0), 0)

    return {
        "bot_id": bot_id,
        "timeframe_filter": timeframe,
        "analytics": {
            "total_trades":      total,
            "wins":              len(wins),
            "losses":            len(losses),
            "timeouts":          len(timeouts),
            "win_rate_pct":      round(len(wins) / total * 100, 1) if total else 0,
            "avg_win_pct":       round(avg_win_pct, 2),
            "avg_loss_pct":      round(avg_loss_pct, 2),
            "total_pnl_pct":     round(total_pnl_pct, 2),
            # NPR-based analytics
            "total_pnl_nrs":     round(total_pnl_nrs, 0),
            "total_win_nrs":     round(total_win_nrs, 0),
            "total_loss_nrs":    round(total_loss_nrs, 0),
            # Risk / quality metrics
            "profit_factor":     profit_factor,       # None if no losses
            "rr_ratio":          rr_ratio,            # None if no losses
            "expectancy_pct":    expectancy_pct,      # expected % per trade
            "avg_hold_days_win":  avg_hold_days_win,
            "avg_hold_days_loss": avg_hold_days_loss,
            "timeframe_breakdown": tf_groups,
        },
        "trades": [t.to_dict() for t in trades],
    }


# ── POST /bots/{bot_id}/run ────────────────────────────────────────────────

@router.post("/{bot_id}/run")
def run_bot(bot_id: str, timeframe: str = "daily", db: Session = Depends(get_db)):
    """
    Manually trigger one bot cycle (resolve + generate + open trades).

    Query param `timeframe` controls which parameter set the bot uses:
      - daily   (default) — fast signals, tight stops, ≤10 day hold
      - weekly  — medium lookbacks, wider stops, ≤25 day hold
      - monthly — long lookbacks, wide stops, ≤60 day hold
    """
    if timeframe not in ("daily", "weekly", "monthly"):
        raise HTTPException(status_code=422, detail="timeframe must be daily, weekly, or monthly")

    meta = _BOT_META.get(bot_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Bot '{bot_id}' not found")

    BotClass = BOT_REGISTRY.get(meta["strategy"])
    if BotClass is None:
        raise HTTPException(status_code=500, detail="Bot class not found in registry")

    try:
        bot = BotClass()
        summary = bot.run_cycle(db, timeframe=timeframe)
        return {"status": "ok", "summary": summary}
    except Exception as exc:
        logger.error("Manual bot run error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ── POST /bots/run-all ─────────────────────────────────────────────────────

@router.post("/run-all")
def run_all_bots(timeframe: str = "daily", db: Session = Depends(get_db)):
    """
    Manually trigger all bots simultaneously.

    Query param `timeframe`: daily (default) | weekly | monthly
    """
    if timeframe not in ("daily", "weekly", "monthly"):
        raise HTTPException(status_code=422, detail="timeframe must be daily, weekly, or monthly")

    results = {}
    for bot_id, meta in _BOT_META.items():
        BotClass = BOT_REGISTRY.get(meta["strategy"])
        if BotClass is None:
            continue
        try:
            bot = BotClass()
            results[bot_id] = bot.run_cycle(db, timeframe=timeframe)
        except Exception as exc:
            logger.error("run-all[%s] error for %s: %s", timeframe, bot_id, exc)
            results[bot_id] = {"error": str(exc)}

    return {"status": "ok", "timeframe": timeframe, "results": results}


# ── GET /bots/summary ─────────────────────────────────────────────────────

@router.get("/summary")
def get_summary(db: Session = Depends(get_db)):
    """Aggregate performance summary across all bots."""
    total_trades = 0
    total_wins   = 0
    total_pnl    = 0.0
    open_count   = 0

    total_pnl_nrs   = 0.0
    total_capital   = 0.0
    total_deployed  = 0.0

    bot_rows = []
    for bot_id, meta in _BOT_META.items():
        state = db.query(BotLearningState).filter(BotLearningState.bot_id == bot_id).first()
        if state:
            total_trades  += state.total_trades
            total_wins    += state.wins
            cap_nrs        = state.capital_nrs or 1_000_000.0
            pnl_nrs        = state.total_pnl_nrs or 0.0
            deployed       = state.capital_deployed or 0.0
            total_capital  += cap_nrs + pnl_nrs
            total_deployed += deployed
            total_pnl_nrs  += pnl_nrs
            bot_rows.append({
                "bot_id":            bot_id,
                "wins":              state.wins,
                "losses":            state.losses,
                "accuracy":          round(state.rolling_accuracy * 100, 1),
                "threshold":         round(state.current_threshold, 1),
                "capital_nrs":       round(cap_nrs, 0),
                "total_pnl_nrs":     round(pnl_nrs, 0),
                "capital_deployed":  round(deployed, 0),
                "max_drawdown_pct":  round(state.max_drawdown_pct or 0.0, 2),
            })

        # Sum P&L from closed trades
        closed = db.query(PaperTrade).filter(
            PaperTrade.bot_id == bot_id, PaperTrade.is_open == False
        ).all()
        total_pnl += sum(t.pnl_pct or 0 for t in closed)

        open_count += db.query(PaperTrade).filter(
            PaperTrade.bot_id == bot_id, PaperTrade.is_open == True
        ).count()

    return {
        "total_trades":         total_trades,
        "total_wins":           total_wins,
        "overall_win_rate":     round(total_wins / total_trades * 100, 1) if total_trades else 0,
        "total_paper_pnl_pct":  round(total_pnl, 2),
        "total_pnl_nrs":        round(total_pnl_nrs, 0),
        "total_capital_nrs":    round(total_capital, 0),
        "total_deployed_nrs":   round(total_deployed, 0),
        "open_positions":       open_count,
        "bots": bot_rows,
    }


# ── POST /bots/{bot_id}/reset ─────────────────────────────────────────────

@router.post("/{bot_id}/reset")
def reset_bot(bot_id: str, db: Session = Depends(get_db)):
    """Reset a bot's RL learning state (keeps trade history)."""
    state = db.query(BotLearningState).filter(BotLearningState.bot_id == bot_id).first()
    if state is None:
        raise HTTPException(status_code=404, detail="No learning state found — bot has never run")

    state.total_trades      = 0
    state.wins              = 0
    state.losses            = 0
    state.timeouts          = 0
    state.rolling_accuracy  = 1.0
    state.current_threshold = 80.0
    state.signal_weights    = None
    state.sector_accuracy   = None
    state.regime_accuracy   = None
    state.mistakes_log      = None
    state.last_lesson       = None
    # Reset capital to fresh 10 lakhs
    state.capital_nrs       = 1_000_000.0
    state.capital_deployed  = 0.0
    state.total_pnl_nrs     = 0.0
    state.peak_capital_nrs  = 1_000_000.0
    state.max_drawdown_pct  = 0.0

    db.add(state)
    db.commit()
    return {"status": "reset", "bot_id": bot_id}


# ── POST /bots/{bot_id}/rebuild-rl ─────────────────────────────────────────
# Maintenance endpoint: replays closed trade history to rebuild sector_accuracy,
# regime_accuracy, and signal_weights with canonical sector normalisation.
# Safe to call at any time — does NOT touch open trades or capital fields.
# Fixes the "Hydropower" vs "Hydro Power" split-key bug and wires signal_weights.

@router.post("/{bot_id}/rebuild-rl")
def rebuild_rl_state(bot_id: str, db: Session = Depends(get_db)):
    """
    Replay closed trade history to rebuild RL accuracy stats with:
      - Canonical sector names (fixes Hydropower/Hydro Power split)
      - signal_weights tracking (score-bracket win rates)
      - regime_accuracy and rolling_accuracy recalculated from scratch

    Safe: does not reset capital, deployed capital, or P&L.
    """
    if bot_id not in _BOT_META:
        raise HTTPException(status_code=404, detail=f"Bot '{bot_id}' not found")

    try:
        from app.components.rl_engine import (
            _ema, _canon_sector, _score_bracket, _EMA_ALPHA,
            get_or_create_state,
        )
        from app.models.paper_trade import PaperTrade, TradeOutcome
        from datetime import datetime, timezone

        state = get_or_create_state(bot_id, _BOT_META[bot_id]["name"], _BOT_META[bot_id]["strategy"], db)

        closed_trades = (
            db.query(PaperTrade)
            .filter(PaperTrade.bot_id == bot_id, PaperTrade.is_open == False)
            .order_by(PaperTrade.close_date.asc())
            .all()
        )

        if not closed_trades:
            return {"status": "ok", "bot_id": bot_id, "message": "no closed trades to replay", "trades_replayed": 0}

        # Reset only accuracy-related fields (keep capital intact)
        state.total_trades    = 0
        state.wins            = 0
        state.losses          = 0
        state.timeouts        = 0
        state.rolling_accuracy  = 1.0
        state.current_threshold = 80.0
        state.sector_accuracy   = {}
        state.sector_counts     = {}
        state.regime_accuracy   = {}
        state.regime_counts     = {}
        state.signal_weights    = {}
        state.mistakes_log      = []

        # Replay each closed trade through the RL update logic
        sec_acc: dict = {}
        sec_cnt: dict = {}
        reg_acc: dict = {}
        reg_cnt: dict = {}
        weights: dict = {}
        acc = 1.0
        threshold = 80.0
        wins = losses = timeouts = 0

        for t in closed_trades:
            is_win = t.outcome == TradeOutcome.WIN
            val = 1.0 if is_win else 0.0

            if is_win:
                wins += 1
            elif t.outcome == TradeOutcome.LOSS:
                losses += 1
            else:
                timeouts += 1

            acc = _EMA_ALPHA * val + (1.0 - _EMA_ALPHA) * acc

            if acc >= 0.85:
                threshold = max(75.0, threshold - 2.0)
            elif acc < 0.80:
                threshold = min(92.0, threshold + 2.0)

            # Sector (canonical)
            sector = _canon_sector(t.sector) if t.sector else None
            if sector and sector != "Other":
                old_s = sec_acc.get(sector, 0.75)
                sec_acc[sector] = round(_EMA_ALPHA * val + (1.0 - _EMA_ALPHA) * old_s, 3)
                sec_cnt[sector] = sec_cnt.get(sector, 0) + 1

            # Regime
            regime = t.regime_at_entry or (t.signal_context or {}).get("regime", "unknown")
            if regime and regime != "unknown":
                old_r = reg_acc.get(regime, 0.75)
                reg_acc[regime] = round(_EMA_ALPHA * val + (1.0 - _EMA_ALPHA) * old_r, 3)
                reg_cnt[regime] = reg_cnt.get(regime, 0) + 1

            # Signal weights (score bracket)
            bracket = _score_bracket(t.signal_score)
            acc_key = f"{bracket}_acc"
            cnt_key = f"{bracket}_cnt"
            old_bkt = weights.get(acc_key, 0.75)
            weights[acc_key] = round(_EMA_ALPHA * val + (1.0 - _EMA_ALPHA) * old_bkt, 3)
            weights[cnt_key] = weights.get(cnt_key, 0) + 1

        total = len(closed_trades)

        state.total_trades      = total
        state.wins              = wins
        state.losses            = losses
        state.timeouts          = timeouts
        state.rolling_accuracy  = round(acc, 4)
        state.current_threshold = round(threshold, 1)
        state.sector_accuracy   = sec_acc
        state.sector_counts     = sec_cnt
        state.regime_accuracy   = reg_acc
        state.regime_counts     = reg_cnt
        state.signal_weights    = weights
        state.last_trade_at     = datetime.now(timezone.utc)

        db.add(state)
        db.commit()

        logger.info(
            "rebuild-rl[%s]: replayed %d trades | acc=%.0f%% threshold=%.0f "
            "sectors=%s regimes=%s brackets=%s",
            bot_id, total, acc * 100, threshold,
            list(sec_acc.keys()), list(reg_acc.keys()),
            {k: v for k, v in weights.items() if "_acc" in k},
        )

        return {
            "status": "ok",
            "bot_id": bot_id,
            "trades_replayed": total,
            "rolling_accuracy": round(acc * 100, 1),
            "threshold": round(threshold, 1),
            "sector_accuracy": sec_acc,
            "regime_accuracy": reg_acc,
            "signal_weights":  {k: v for k, v in weights.items() if "_acc" in k},
        }

    except Exception as exc:
        logger.error("rebuild-rl[%s] error: %s", bot_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Scheduler status ────────────────────────────────────────────────────────

@router.get("/scheduler/status")
def scheduler_status():
    """
    Return the live APScheduler state: running flag, next fire times, and
    current NEPSE market-window flags.
    """
    from datetime import datetime, timezone as _tz
    from app.services.bot_scheduler import (
        get_scheduler, _is_market_window, _is_best_entry_window,
    )

    sched = get_scheduler()
    now   = datetime.now(_tz.utc)

    jobs_info = []
    for j in sched.get_jobs():
        # APScheduler 3.x uses next_run_time; 4.x uses next_fire_time
        nrt = getattr(j, "next_run_time", None) or getattr(j, "next_fire_time", None)
        jobs_info.append({
            "id":            j.id,
            "name":          j.name,
            "next_run_utc":  nrt.isoformat() if nrt else None,
            "trigger":       str(j.trigger),
        })

    return {
        "scheduler_running":     sched.running,
        "current_utc":           now.isoformat(),
        "in_market_window":      _is_market_window(),
        "in_best_entry_window":  _is_best_entry_window(),
        "weekday":               now.weekday(),   # 0=Mon, 4=Fri
        "jobs":                  jobs_info,
    }


@router.post("/scheduler/trigger-now")
def scheduler_trigger_now(timeframe: str = Query("daily", enum=["daily", "weekly", "monthly"])):
    """
    Force an immediate bot cycle outside the normal APScheduler interval.
    Useful during testing when the server is running outside NEPSE hours.
    Bypasses the market-window guard — runs regardless of time.
    """
    from app.services.bot_scheduler import _run_bots_with_timeframe
    _run_bots_with_timeframe(timeframe)
    return {"status": "ok", "message": f"Bot cycle [{timeframe}] triggered immediately"}
