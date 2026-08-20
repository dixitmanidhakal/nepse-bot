"""
Base Bot
========
Abstract base class that all strategy bots inherit from.

Responsibilities:
- Load / persist bot learning state from DB.
- Enforce the 80% accuracy gate before entering any trade.
- Open / close paper trades with NPR money management.
- Delegate to RL engine after each trade is resolved.
- Expose a standard `run_cycle()` method called by the scheduler.

Money Management (per bot):
    CAPITAL_NRS      = 10,00,000 NPR  (10 lakhs per bot)
    MAX_POSITION_PCT = 20%             (max 2L per single trade)
    CASH_RESERVE_PCT = 20%             (always keep 2L free)
    MAX_CONCURRENT   = 5               (max 5 open positions)
    Deployable       = 80%  = 8L       (spread across ≤5 positions)

Signal Selection (when > 5 signals qualify):
    Signals are pre-sorted by score (highest first). The bot takes the top
    N that fit within open slots and available capital. Low-scoring signals
    are skipped automatically — no manual filtering needed.

T+2 Settlement (Nepal / NEPSE rule):
    After buying a stock, you CANNOT sell for 2 NEPSE trading days.
    NEPSE trading days: Monday(0) Tuesday(1) Wednesday(2) Thursday(3) Friday(4).
    Example: buy Friday → can exit from Wednesday (Sat+Sun are not trading days).
    The bot enforces this via MIN_HOLD_TRADING_DAYS = 2 — no target/stop/timeout
    resolution fires until at least 2 NEPSE trading days have elapsed since entry.

Entry Timing (daily timeframe only):
    New trades are only opened during the prime intraday window:
        11:30–14:30 NST  (05:45–08:45 UTC)
    This avoids the volatile first 30 min (gap fills, auction prints) and
    the last 30 min (closing auction pressure) of the NEPSE session.
    Open position resolution (target / stop / timeout checks) runs every
    cycle regardless of the entry window — you can always close a trade.

Timeframes:
    daily   — max_hold_days=10  (default, run every 15 min during market)
    weekly  — max_hold_days=25  (run on Monday, ~5-week hold)
    monthly — max_hold_days=60  (run on first trading day of month)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.paper_trade import PaperTrade, TradeOutcome, TradeDirection
from app.models.bot_learning_state import BotLearningState
from app.components.rl_engine import get_or_create_state, process_closed_trade, evaluate_signal

logger = logging.getLogger(__name__)

# ── Timeframe → max hold days mapping ────────────────────────────────────────
_TIMEFRAME_HOLD_DAYS = {
    "daily":   10,
    "weekly":  25,
    "monthly": 60,
}

# ── NEPSE market rules ────────────────────────────────────────────────────────
# Python weekday(): Mon=0 Tue=1 Wed=2 Thu=3 Fri=4 Sat=5 Sun=6
_NEPSE_WEEKDAYS: frozenset = frozenset({0, 1, 2, 3, 4})  # Mon Tue Wed Thu Fri

# T+2 settlement: you cannot exit a position until at least 2 NEPSE trading
# days have passed since entry (SEBON/CDS rule).
MIN_HOLD_TRADING_DAYS: int = 2

# Prime entry window for DAILY signals: 11:30–14:30 NST = 05:45–08:45 UTC.
# Avoids the volatile first/last 30 min of the NEPSE session.
_ENTRY_WIN_START_MIN: int = 5 * 60 + 45   # 05:45 UTC in minutes
_ENTRY_WIN_END_MIN:   int = 8 * 60 + 45   # 08:45 UTC in minutes


def _nepse_trading_days_since(entry_date: datetime) -> int:
    """
    Count full NEPSE trading days (Mon–Fri) that have *completed* since
    entry_date up to — but not including — today (UTC).

    Example:
        Entry Friday  → Sat(not) Sun(not) → Mon counts as day 1
        Entry Friday  → Tue counts as day 2 → T+2 cleared from Tuesday.
    """
    now  = datetime.now(timezone.utc)
    # Normalise entry_date to UTC-aware
    if entry_date.tzinfo is None:
        entry_date = entry_date.replace(tzinfo=timezone.utc)
    start_date = entry_date.date()
    end_date   = now.date()  # don't count today — trade could be intraday
    count = 0
    day = start_date
    while day < end_date:
        if day.weekday() in _NEPSE_WEEKDAYS:
            count += 1
        day += timedelta(days=1)
    return count


def _is_daily_entry_window() -> bool:
    """
    True during the prime DAILY entry band: 11:30–14:30 NST (05:45–08:45 UTC).
    Weekly and monthly entries are not gated by this window — their scheduler
    already targets specific time slots.
    """
    now_min = datetime.now(timezone.utc).hour * 60 + datetime.now(timezone.utc).minute
    return _ENTRY_WIN_START_MIN <= now_min <= _ENTRY_WIN_END_MIN


class BaseBot(ABC):
    """Abstract base for all paper-trading strategy bots."""

    # Subclasses must set these
    BOT_ID:   str = "base"
    BOT_NAME: str = "Base Bot"
    STRATEGY: str = "base"

    # Default risk / reward parameters (overridden per bot)
    DEFAULT_STOP_PCT:   float = 3.0    # stop-loss % below entry
    DEFAULT_TARGET_PCT: float = 6.0    # take-profit % above entry
    MAX_HOLD_DAYS:      int   = 10     # TIMEOUT after N trading days (daily default)

    # ── Money management (Nepal market) ──────────────────────────────────────
    CAPITAL_NRS:      float = 1_000_000.0   # 10 lakhs per bot
    MAX_POSITION_PCT: float = 0.20          # max 20% per position = 2L
    CASH_RESERVE_PCT: float = 0.20          # always keep 20% = 2L liquid
    MAX_CONCURRENT:   int   = 5             # never hold > 5 positions at once

    def __init__(self):
        self.logger = logging.getLogger(f"bot.{self.BOT_ID}")

    # ── Abstract interface ─────────────────────────────────────────────────

    @abstractmethod
    def generate_signals(self, db: Session, timeframe: str = "daily") -> List[Dict[str, Any]]:
        """
        Return a list of candidate signals for the given timeframe.

        Each signal dict must contain:
            symbol        str   stock symbol
            score         float 0-100 confidence score
            signal        str   "BUY" | "SELL" | "WATCH"
            entry_price   float current price
            context       dict  any extra info for RL post-mortem
        Optional keys:
            stop_pct      float override default stop %
            target_pct    float override default target %
            sector        str
            regime        str   "trending" | "sideways" | "volatile"

        Timeframe guidance:
            daily   — intraday / short-swing signals; use fast indicators
            weekly  — multi-week swing; use medium-term lookbacks
            monthly — positional; use long-term moving averages & wider stops
        """
        ...

    # ── Core cycle ────────────────────────────────────────────────────────

    def run_cycle(self, db: Session, timeframe: str = "daily") -> Dict[str, Any]:
        """
        One full bot cycle:
        1. Resolve any open positions that hit target / stop / timeout.
        2. Generate new signals.
        3. Filter by accuracy gate + money management limits.
        4. Open new paper trades for qualifying signals.
        Returns a summary dict.
        """
        state = get_or_create_state(self.BOT_ID, self.BOT_NAME, self.STRATEGY, db)

        # Ensure capital fields are initialised (for rows created before this feature)
        if state.capital_nrs is None:
            state.capital_nrs = self.CAPITAL_NRS
        if state.capital_deployed is None:
            state.capital_deployed = 0.0
        if state.total_pnl_nrs is None:
            state.total_pnl_nrs = 0.0
        if state.peak_capital_nrs is None:
            state.peak_capital_nrs = state.capital_nrs
        if state.max_drawdown_pct is None:
            state.max_drawdown_pct = 0.0

        summary: Dict[str, Any] = {
            "bot_id":           self.BOT_ID,
            "strategy":         self.STRATEGY,
            "timeframe":        timeframe,
            "resolved":         [],
            "opened":           [],
            "skipped":          [],
            "threshold":        state.current_threshold,
            "rolling_accuracy": state.rolling_accuracy,
            "capital_nrs":      state.capital_nrs,
            "capital_deployed": state.capital_deployed,
            "total_pnl_nrs":    state.total_pnl_nrs,
        }

        # Step 1 — resolve open trades (all timeframes checked every cycle)
        resolved = self._resolve_open_trades(db, state)
        summary["resolved"] = resolved

        # Track symbols closed this cycle to prevent immediate re-entry
        just_closed = {r["symbol"] for r in resolved}

        # Step 2 — count open positions for money management gate
        open_count = (
            db.query(PaperTrade)
            .filter(PaperTrade.bot_id == self.BOT_ID, PaperTrade.is_open == True)
            .count()
        )

        if open_count >= self.MAX_CONCURRENT:
            summary["skipped"].append({
                "symbol": "ALL",
                "reason": f"max concurrent positions reached ({open_count}/{self.MAX_CONCURRENT})",
                "score": 0,
            })
            db.commit()
            return summary

        # Available capital gate
        current_capital = (state.capital_nrs or self.CAPITAL_NRS) + (state.total_pnl_nrs or 0.0)
        deployable      = current_capital * (1 - self.CASH_RESERVE_PCT)  # 80% deployable
        deployed        = state.capital_deployed or 0.0
        available       = max(0.0, deployable - deployed)
        min_position    = current_capital * 0.04  # need at least 4% to open a trade

        if available < min_position:
            summary["skipped"].append({
                "symbol": "ALL",
                "reason": f"insufficient available capital (NPR {available:,.0f} < {min_position:,.0f})",
                "score": 0,
            })
            db.commit()
            return summary

        # Step 3 — entry window gate (daily only)
        # For daily signals, only open new trades during 11:30–14:30 NST.
        # Resolution (target/stop/timeout checks) always runs — you can always exit.
        # Weekly and monthly cycles are already targeted to specific scheduler windows.
        if timeframe == "daily" and not _is_daily_entry_window():
            self.logger.debug(
                "Bot[%s][daily]: outside prime entry window (05:45–08:45 UTC) — "
                "resolve-only cycle, no new entries.",
                self.BOT_ID,
            )
            db.commit()
            return summary

        # Step 4 — generate candidates (pass timeframe so each bot can adapt)
        try:
            candidates = self.generate_signals(db, timeframe)
        except Exception as exc:
            self.logger.error("Signal generation error: %s", exc, exc_info=True)
            candidates = []

        # Slots remaining before hitting MAX_CONCURRENT
        slots_remaining = self.MAX_CONCURRENT - open_count

        # Step 5 — filter and open
        for sig in candidates:
            if slots_remaining <= 0:
                break

            if sig.get("signal") not in ("BUY",):
                continue

            sym = sig["symbol"]

            # Never re-enter a symbol closed in this same cycle
            if sym in just_closed:
                summary["skipped"].append({
                    "symbol": sym,
                    "reason": "just closed this cycle — no immediate re-entry",
                    "score": float(sig.get("score", 0)),
                })
                continue

            score     = float(sig.get("score", 0))
            threshold = state.current_threshold

            # RL gate: rolling accuracy + sector/regime accuracy
            allowed, reason = evaluate_signal(sig, state)
            if not allowed:
                summary["skipped"].append({"symbol": sym, "reason": reason, "score": score})
                continue

            # Score threshold gate
            if score < threshold:
                summary["skipped"].append({
                    "symbol": sym,
                    "reason": f"score={score:.0f} < threshold={threshold:.0f}",
                    "score": score,
                })
                continue

            # Avoid duplicate open position for same symbol
            existing = (
                db.query(PaperTrade)
                .filter(
                    PaperTrade.bot_id == self.BOT_ID,
                    PaperTrade.symbol == sym,
                    PaperTrade.is_open == True,
                )
                .first()
            )
            if existing:
                continue

            # Re-check available capital (positions opened earlier in this loop)
            deployed        = state.capital_deployed or 0.0
            available       = max(0.0, deployable - deployed)
            if available < min_position:
                break

            trade = self._open_trade(sig, db, state, timeframe, available, current_capital)
            if trade is None:
                continue  # position sizing returned zero shares

            state.capital_deployed = (state.capital_deployed or 0.0) + (trade.capital_allocated or 0.0)
            slots_remaining -= 1

            summary["opened"].append({
                "symbol":           trade.symbol,
                "entry_price":      trade.entry_price,
                "target_price":     trade.target_price,
                "stop_price":       trade.stop_price,
                "score":            trade.signal_score,
                "capital_deployed": trade.capital_allocated,
                "shares_qty":       trade.shares_qty,
                "timeframe":        trade.timeframe,
            })

        summary["capital_deployed"] = state.capital_deployed
        db.commit()
        self.logger.info(
            "Cycle[%s] done | resolved=%d opened=%d skipped=%d deployed=NPR %.0f",
            timeframe,
            len(summary["resolved"]), len(summary["opened"]), len(summary["skipped"]),
            state.capital_deployed or 0,
        )
        return summary

    # ── Helpers ───────────────────────────────────────────────────────────

    def _open_trade(
        self,
        sig: Dict[str, Any],
        db: Session,
        state: BotLearningState,
        timeframe: str = "daily",
        available_nrs: float = 200_000.0,
        current_capital: float = 1_000_000.0,
    ) -> Optional[PaperTrade]:
        """Open a new paper trade with Kelly-based position sizing."""
        entry   = float(sig["entry_price"])
        stop_p  = float(sig.get("stop_pct",   self.DEFAULT_STOP_PCT))
        tgt_p   = float(sig.get("target_pct", self.DEFAULT_TARGET_PCT))

        # ── Kelly-based position sizing ───────────────────────────────────
        # Score (0-100) acts as confidence proxy; map to 0-MAX_POSITION_PCT
        score_norm    = min(1.0, float(sig.get("score", 50)) / 100.0)
        # Kelly-inspired fraction: use half-Kelly scaled by signal confidence
        kelly_frac    = 0.5 * score_norm                              # 0→0, 100→0.5 of max
        pos_pct       = self.MAX_POSITION_PCT * kelly_frac            # 0→0%, 100→10% of capital
        pos_pct       = max(0.05, min(pos_pct, self.MAX_POSITION_PCT))  # clamp 5-20%
        position_nrs  = current_capital * pos_pct                     # NPR amount for position
        position_nrs  = min(position_nrs, available_nrs)             # never exceed available

        # Compute shares — round down to whole shares
        if entry <= 0:
            return None
        shares = int(position_nrs / entry)
        if shares <= 0:
            return None
        capital_allocated = round(shares * entry, 2)

        # Adjust hold days per timeframe
        hold_days = _TIMEFRAME_HOLD_DAYS.get(timeframe, self.MAX_HOLD_DAYS)

        trade = PaperTrade(
            bot_id            = self.BOT_ID,
            bot_name          = self.BOT_NAME,
            strategy          = self.STRATEGY,
            symbol            = sig["symbol"].upper(),
            direction         = TradeDirection.LONG,
            entry_price       = entry,
            target_price      = round(entry * (1 + tgt_p / 100), 2),
            stop_price        = round(entry * (1 - stop_p / 100), 2),
            signal_score      = float(sig.get("score", 0)),
            signal_context    = sig.get("context"),
            regime_at_entry   = sig.get("regime"),
            sector            = sig.get("sector"),
            max_hold_days     = hold_days,
            is_open           = True,
            outcome           = TradeOutcome.OPEN,
            capital_allocated = capital_allocated,
            shares_qty        = shares,
            timeframe         = timeframe,
        )
        db.add(trade)
        db.flush()
        self.logger.info(
            "Opened[%s] | %s %s entry=%.2f tgt=%.2f stop=%.2f shares=%d capital=NPR %.0f score=%.0f",
            timeframe, self.BOT_ID, trade.symbol, entry,
            trade.target_price, trade.stop_price,
            shares, capital_allocated, trade.signal_score,
        )
        return trade

    def _resolve_open_trades(self, db: Session, state: BotLearningState) -> List[Dict]:
        """
        Check all open trades for this bot against latest prices.
        Resolves trades that hit target, stop, or max-hold-days.
        Updates NPR P&L and capital tracking on the learning state.
        """
        open_trades: List[PaperTrade] = (
            db.query(PaperTrade)
            .filter(PaperTrade.bot_id == self.BOT_ID, PaperTrade.is_open == True)
            .all()
        )
        if not open_trades:
            return []

        symbols = [t.symbol for t in open_trades]
        prices  = self._fetch_current_prices(symbols)

        capital_nrs = state.capital_nrs or self.CAPITAL_NRS

        resolved = []
        for trade in open_trades:
            current = prices.get(trade.symbol)
            if current is None:
                continue

            outcome = None
            # Calendar days held (for timeout counting)
            days_held = (datetime.now(timezone.utc) - trade.entry_date.replace(tzinfo=timezone.utc)).days

            # ── Stop-loss: ALWAYS overrides T+2 ──────────────────────────────
            # A stop-loss can be executed at any time — the T+2 rule governs
            # settlement, not the ability to place a sell order to cut losses.
            # Letting a stop breach linger would cause unbounded drawdown.
            if current <= trade.stop_price:
                outcome = TradeOutcome.LOSS

            else:
                # ── T+2 Settlement rule (SEBON / CDS) ────────────────────────
                # Target exits and timeouts require T+2 NEPSE trading days to
                # have elapsed since entry before the position can be closed.
                trading_days_held = _nepse_trading_days_since(trade.entry_date)
                if trading_days_held < MIN_HOLD_TRADING_DAYS:
                    self.logger.debug(
                        "T+2 hold: %s — only %d NEPSE trading day(s) since entry, "
                        "need %d before target/timeout exit is allowed.",
                        trade.symbol, trading_days_held, MIN_HOLD_TRADING_DAYS,
                    )
                    continue

                if current >= trade.target_price:
                    outcome = TradeOutcome.WIN
                elif days_held >= trade.max_hold_days:
                    outcome = TradeOutcome.TIMEOUT

            if outcome is not None:
                pnl_pct = (current - trade.entry_price) / trade.entry_price * 100.0

                # NPR P&L — based on actual allocated capital
                allocated = trade.capital_allocated or 0.0
                pnl_nrs   = round(allocated * pnl_pct / 100.0, 2)

                trade.close_price = current
                trade.close_date  = datetime.now(timezone.utc)
                trade.outcome     = outcome
                trade.pnl_pct     = round(pnl_pct, 2)
                trade.pnl_nrs     = pnl_nrs
                trade.is_open     = False
                db.add(trade)
                db.flush()

                # ── Update capital tracking on state ──────────────────────
                state.capital_deployed = max(0.0, (state.capital_deployed or 0.0) - allocated)
                state.total_pnl_nrs    = (state.total_pnl_nrs or 0.0) + pnl_nrs
                current_capital        = capital_nrs + state.total_pnl_nrs

                # High-water mark and drawdown
                if current_capital > (state.peak_capital_nrs or capital_nrs):
                    state.peak_capital_nrs = current_capital
                peak = state.peak_capital_nrs or capital_nrs
                if peak > 0:
                    dd = (peak - current_capital) / peak * 100.0
                    if dd > (state.max_drawdown_pct or 0.0):
                        state.max_drawdown_pct = round(dd, 2)

                # Feed into RL engine
                updated_state = process_closed_trade(trade, db)
                state.rolling_accuracy  = updated_state.rolling_accuracy
                state.current_threshold = updated_state.current_threshold

                resolved.append({
                    "symbol":      trade.symbol,
                    "outcome":     outcome.value,
                    "pnl_pct":     round(pnl_pct, 2),
                    "pnl_nrs":     round(pnl_nrs, 0),
                    "days":        days_held,
                    "capital":     allocated,
                    "timeframe":   trade.timeframe or "daily",
                })
                self.logger.info(
                    "Resolved | %s %s outcome=%s pnl=%.1f%% NPR %+.0f",
                    self.BOT_ID, trade.symbol, outcome.value, pnl_pct, pnl_nrs,
                )

        return resolved

    def _fetch_current_prices(self, symbols: List[str]) -> Dict[str, float]:
        """
        Fetch latest LTP for the given symbols.

        Priority (fastest / freshest first):
          1. live_market_cache PostgreSQL table — filled every 5 min by the
             market scraper (merolagani/nepsealpha/sharesansar/yonepse).
             Accepted when data is < 10 minutes old.
          2. Async aggregator — cascades through all 4 live scrapers.
             Used on startup before the first scrape cycle completes.
          3. Yonepse GitHub raw JSON — always reachable, ~15 min lag.
        """
        prices: Dict[str, float] = {}
        remaining = list(symbols)

        # ── 1. DB live cache (most recent scrape) ─────────────────────────────
        try:
            from app.services.data.market_scraper import get_cached_prices
            cached = get_cached_prices(remaining, max_age_seconds=600)
            prices.update(cached)
            remaining = [s for s in remaining if s not in prices]
            if not remaining:
                return prices
        except Exception as exc:
            self.logger.debug("DB price cache read failed: %s", exc)

        # ── 2. Async aggregator (cascades all sources in real time) ──────────
        def _parse_rows(rows: list) -> None:
            sym_set = {s.upper() for s in remaining}
            for row in rows:
                sym = str(row.get("symbol") or row.get("Symbol") or "").upper()
                ltp = (
                    row.get("ltp") or row.get("LTP") or row.get("close")
                    or row.get("lastTradedPrice") or row.get("lastTradePrice")
                )
                if sym and ltp and sym in sym_set:
                    try:
                        prices[sym] = float(ltp)
                    except (TypeError, ValueError):
                        pass

        try:
            from app.services.data.free_sources import aggregator
            from app.components.bots.nepse_universe import run_async
            rows = run_async(aggregator.live_market())
            _parse_rows(rows)
            remaining = [s for s in remaining if s not in prices]
            if not remaining:
                return prices
        except Exception as exc:
            self.logger.warning("Aggregator live_market failed: %s — using yonepse fallback", exc)

        # ── 3. Yonepse GitHub raw JSON (always reachable, ~15 min lag) ────────
        try:
            import requests as req
            r = req.get(
                "https://raw.githubusercontent.com/Shubhamnpk/yonepse/main/data/nepse_data.json",
                timeout=10,
            )
            if r.status_code == 200:
                raw = r.json()
                _parse_rows(raw if isinstance(raw, list) else raw.get("data", []))
        except Exception as exc2:
            self.logger.warning("Yonepse fallback also failed: %s", exc2)

        return prices
