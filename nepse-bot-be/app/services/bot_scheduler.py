"""
Bot Scheduler
=============
Uses APScheduler to run paper-trading bots at three timeframes:

  DAILY   — every 15 min during NEPSE market hours (all bots, timeframe="daily")
  WEEKLY  — Monday morning 11:15 NST / 05:30 UTC  (all bots, timeframe="weekly")
  MONTHLY — first NEPSE trading day of each month   (all bots, timeframe="monthly")

Nepal market timing heuristics:
  Best intraday window  → 11:30–14:30 NST (05:45–08:45 UTC)  — avoid open/close whipsaw
  Weekly entry window   → Monday 11:15–13:00 NST (05:30–07:15 UTC) — catch Mon momentum
  Monthly entry window  → first Mon/Tue/Wed/Thu of month, morning session

NST = UTC+5:45
NEPSE trading hours: 11:00–15:00 NST (05:15–09:15 UTC)
Pre-market scan: 10:30 NST (04:45 UTC)
Trading days: Monday(0) Tuesday(1) Wednesday(2) Thursday(3) Friday(4)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_BOT_SCHEDULER_ENABLED = os.getenv("BOT_SCHEDULER_ENABLED", "true").lower() == "true"

# NEPSE trading window in UTC (NST = UTC+5:45)
# 10:30 NST → 04:45 UTC  (pre-market)
# 15:15 NST → 09:30 UTC  (post-market flush)
_MARKET_START_UTC_HOUR   = 4
_MARKET_START_UTC_MINUTE = 45
_MARKET_END_UTC_HOUR     = 9
_MARKET_END_UTC_MINUTE   = 30

# Best intraday entry band: 11:30–14:30 NST = 05:45–08:45 UTC
_BEST_ENTRY_START_HOUR   = 5
_BEST_ENTRY_START_MINUTE = 45
_BEST_ENTRY_END_HOUR     = 8
_BEST_ENTRY_END_MINUTE   = 45

# Monday=0, Tuesday=1, Wednesday=2, Thursday=3, Friday=4
_NEPSE_DAYS = {0, 1, 2, 3, 4}


def _is_market_window() -> bool:
    """Return True if we are currently within the full NEPSE scanning window."""
    now = datetime.now(timezone.utc)
    if now.weekday() not in _NEPSE_DAYS:
        return False
    start = now.replace(hour=_MARKET_START_UTC_HOUR, minute=_MARKET_START_UTC_MINUTE, second=0, microsecond=0)
    end   = now.replace(hour=_MARKET_END_UTC_HOUR,   minute=_MARKET_END_UTC_MINUTE,   second=0, microsecond=0)
    return start <= now <= end


def _is_best_entry_window() -> bool:
    """
    Return True during the prime intraday entry window:
    11:30–14:30 NST  (05:45–08:45 UTC)
    This avoids the volatile first/last 30 minutes of the NEPSE session.
    """
    now = datetime.now(timezone.utc)
    if now.weekday() not in _NEPSE_DAYS:
        return False
    start = now.replace(hour=_BEST_ENTRY_START_HOUR, minute=_BEST_ENTRY_START_MINUTE, second=0, microsecond=0)
    end   = now.replace(hour=_BEST_ENTRY_END_HOUR,   minute=_BEST_ENTRY_END_MINUTE,   second=0, microsecond=0)
    return start <= now <= end


def _is_first_nepse_day_of_month() -> bool:
    """
    Return True if today is the first NEPSE trading day of the current month.
    Nepal market is open Mon–Fri; we find the first such day in days 1-7.
    """
    now = datetime.now(timezone.utc)
    if now.weekday() not in _NEPSE_DAYS:
        return False
    # Find the lowest calendar day (1-7) that is a NEPSE trading day
    year, month = now.year, now.month
    for day in range(1, 8):
        try:
            candidate = datetime(year, month, day, tzinfo=timezone.utc)
            if candidate.weekday() in _NEPSE_DAYS:
                return now.day == day
        except ValueError:
            break
    return False


def _run_bots_with_timeframe(timeframe: str) -> None:
    """Shared logic: run every registered bot with the given timeframe."""
    logger.info("Bot scheduler[%s]: starting cycle at %s UTC", timeframe, datetime.now(timezone.utc).strftime("%H:%M"))
    try:
        from app.database import SessionLocal
        from app.components.bots import BOT_REGISTRY

        db = SessionLocal()
        try:
            for bot_id, BotClass in BOT_REGISTRY.items():
                try:
                    bot     = BotClass()
                    summary = bot.run_cycle(db, timeframe=timeframe)
                    logger.info(
                        "Bot[%s][%s] cycle: opened=%d resolved=%d skipped=%d acc=%.0f%% threshold=%.0f",
                        bot_id, timeframe,
                        len(summary.get("opened", [])),
                        len(summary.get("resolved", [])),
                        len(summary.get("skipped", [])),
                        summary.get("rolling_accuracy", 0) * 100,
                        summary.get("threshold", 80),
                    )
                except Exception as bot_exc:
                    logger.error("Bot[%s][%s] cycle error: %s", bot_id, timeframe, bot_exc, exc_info=True)
        finally:
            db.close()

    except Exception as exc:
        logger.error("Bot scheduler[%s] top-level error: %s", timeframe, exc, exc_info=True)


def run_market_scraper() -> None:
    """Run one market scrape cycle. Gated by the NEPSE market window."""
    if not _is_market_window():
        logger.debug("Market scraper: outside market window, skipping.")
        return
    try:
        from app.services.data.market_scraper import run_scraper_sync
        run_scraper_sync()
    except Exception as exc:
        logger.error("Market scraper error: %s", exc, exc_info=True)


def run_quant_snapshot() -> None:
    """
    Run full Quant Lab + Advanced Quant Lab analysis and cache the result.
    Called by APScheduler every 30 minutes during NEPSE market hours.
    The snapshot is served via GET /api/v1/quant/snapshot.
    """
    if not _is_market_window():
        logger.debug("Quant snapshot: outside market window, skipping.")
        return
    try:
        from app.services.quant_snapshot import run_snapshot
        run_snapshot()
    except Exception as exc:
        logger.error("Quant snapshot error: %s", exc, exc_info=True)


def run_all_bots() -> None:
    """
    Daily bot cycle — every 15 min during NEPSE hours.
    Only enters new trades in the prime entry window (11:30–14:30 NST).
    Open position resolution runs always within the full market window.
    """
    if not _is_market_window():
        logger.debug("Bot scheduler[daily]: outside market window, skipping.")
        return
    _run_bots_with_timeframe("daily")


def run_weekly_bots() -> None:
    """
    Weekly bot cycle — runs on Monday during the first 2 hours of trading.
    Scans for weekly swing setups; trades hold up to 25 trading days (~5 weeks).

    Nepal timing rationale:
      Monday morning often carries carry-over sentiment from weekend news and
      global markets. Early-week entry captures Mon–Wed momentum before
      institutional mid-week rebalancing.
    """
    now = datetime.now(timezone.utc)
    # Only run on Monday (weekday 0) and only during 05:30–07:30 UTC (11:15–13:15 NST)
    if now.weekday() != 0:
        return
    if not (5 <= now.hour <= 7):
        return
    if not _is_market_window():
        return
    _run_bots_with_timeframe("weekly")


def run_monthly_bots() -> None:
    """
    Monthly bot cycle — runs on the first NEPSE trading day of each month.
    Trades hold up to 60 trading days (~3 months) — captures medium-term trends.

    Nepal timing rationale:
      Month-start sees fresh institutional allocation flows and budget-cycle
      buying. NEPSE historically shows stronger momentum in the first week of
      a new fiscal/calendar month. Monthly bots hold through earnings season
      and seasonal sector rotations (banking rallies in Poush-Magh, hydropower
      in summer, etc.).
    """
    if not _is_market_window():
        return
    if not _is_first_nepse_day_of_month():
        logger.debug("Monthly bot cycle: not the first NEPSE day of month, skipping.")
        return
    _run_bots_with_timeframe("monthly")


# ── APScheduler integration ───────────────────────────────────────────────────

_scheduler: Optional[object] = None


def get_scheduler():
    global _scheduler
    if _scheduler is None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger

        _scheduler = AsyncIOScheduler(timezone="UTC")

        # ── Market data scraper: every 5 min during NEPSE hours ──────────────
        # Clock-aligned: fires at :00,:05,:10,...,:55 on weekdays only.
        # Internal _is_market_window() gate skips fires outside 04:45-09:30 UTC.
        _scheduler.add_job(
            run_market_scraper,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour="4-9",
                minute="0,5,10,15,20,25,30,35,40,45,50,55",
                timezone="UTC",
            ),
            id="market_scraper",
            name="Live Market Data Scraper",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=120,    # 2-min grace; run even if slightly late
        )

        # ── Daily bot cycle: every 15 min during NEPSE hours ─────────────────
        # Clock-aligned to wall-clock :00/:15/:30/:45 on weekdays.
        # Fires at hour=4-9 so the internal _is_market_window() guard (04:45-09:30)
        # handles the exact boundary filtering cleanly.
        # misfire_grace_time=5min: if the scheduler was briefly down, run on resume.
        _scheduler.add_job(
            run_all_bots,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour="4-9",
                minute="0,15,30,45",
                timezone="UTC",
            ),
            id="bot_cycle_daily",
            name="Daily Paper Trading Bot Cycle",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,    # 5-min grace; run missed fire on resume
        )

        # ── Weekly bot cycle: Monday 05:30 & 06:30 UTC ───────────────────────
        # Runs twice in the Monday morning window to catch different signal windows
        for hour, minute, job_id in [
            (5, 30, "bot_cycle_weekly_1"),
            (6, 30, "bot_cycle_weekly_2"),
        ]:
            _scheduler.add_job(
                run_weekly_bots,
                trigger=CronTrigger(
                    day_of_week="mon",
                    hour=hour,
                    minute=minute,
                    timezone="UTC",
                ),
                id=job_id,
                name=f"Weekly Paper Trading Bot Cycle ({hour:02d}:{minute:02d} UTC)",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=600,
            )

        # ── Monthly bot cycle: days 1-7 at 06:00 UTC (11:45 NST) ─────────────
        # The _is_first_nepse_day_of_month() guard inside filters to only the first NEPSE day
        _scheduler.add_job(
            run_monthly_bots,
            trigger=CronTrigger(
                day="1-7",
                hour=6,
                minute=0,
                timezone="UTC",
            ),
            id="bot_cycle_monthly",
            name="Monthly Paper Trading Bot Cycle",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=900,
        )

        # ── Quant snapshot: every 30 min during NEPSE hours ──────────────────
        # Clock-aligned to :00 and :30 on weekdays.
        _scheduler.add_job(
            run_quant_snapshot,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour="4-9",
                minute="0,30",
                timezone="UTC",
            ),
            id="quant_snapshot",
            name="Quant Lab Auto-Analysis Snapshot",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )

    return _scheduler


async def start_bot_scheduler() -> None:
    if not _BOT_SCHEDULER_ENABLED:
        logger.info("Bot scheduler disabled (BOT_SCHEDULER_ENABLED=false)")
        return
    sched = get_scheduler()
    if not sched.running:
        sched.start()
        logger.info(
            "Bot scheduler started — daily(clock-aligned :00/:15/:30/:45), "
            "weekly(Mon 05:30/06:30 UTC), monthly(1st NEPSE day)"
        )

    # ── Immediate startup fire ────────────────────────────────────────────────
    # If the server starts (or restarts) during NEPSE market hours, run one
    # bot cycle immediately so we don't wait up to 15 min for the next clock mark.
    if _is_market_window():
        import asyncio
        import threading

        def _run_startup_cycle():
            logger.info(
                "Bot scheduler: server started inside market hours — "
                "running immediate startup bot cycle"
            )
            run_all_bots()
            run_market_scraper()

        t = threading.Thread(target=_run_startup_cycle, daemon=True, name="bot_startup_cycle")
        t.start()
        logger.info("Bot scheduler: startup immediate cycle dispatched (background thread)")


async def stop_bot_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Bot scheduler stopped")
