"""
NEPSE Calendar Routes

Exposes trading-day / holiday helpers from app.utils.nepse_calendar.
"""

from datetime import date as date_cls, datetime
from fastapi import APIRouter, Query

from app.utils import nepse_calendar as cal

router = APIRouter(prefix="/calendar", tags=["Calendar"])


@router.get("/status")
def calendar_status():
    """Return today's market status, current Nepal time, and session phase."""
    now = cal.current_nepal_datetime()
    today = now.date()
    return {
        "nepal_datetime": now.isoformat(),
        "date": today.isoformat(),
        "weekday": today.strftime("%A"),
        "is_weekend": cal.is_nepal_weekend(today),
        "is_known_holiday": cal.is_known_holiday(today),
        "is_trading_day": cal.is_trading_day(today),
        "session_phase": cal.market_session_phase(now),
        "schedule": cal.get_market_schedule(),
    }


@router.get("/is-trading-day")
def is_trading_day(d: str = Query(..., description="YYYY-MM-DD")):
    """Check whether a specific date is a NEPSE trading day."""
    parsed = datetime.strptime(d, "%Y-%m-%d").date()
    return {
        "date": parsed.isoformat(),
        "is_trading_day": cal.is_trading_day(parsed),
        "is_weekend": cal.is_nepal_weekend(parsed),
        "is_known_holiday": cal.is_known_holiday(parsed),
    }


@router.get("/next-trading-day")
def next_trading_day(d: str = Query(..., description="YYYY-MM-DD")):
    """Find the next NEPSE trading day after the given date."""
    parsed = datetime.strptime(d, "%Y-%m-%d").date()
    nxt = cal.next_trading_day(parsed)
    return {"from": parsed.isoformat(), "next_trading_day": nxt.isoformat()}


@router.get("/trading-days-between")
def trading_days_between(
    start: str = Query(..., description="YYYY-MM-DD"),
    end: str = Query(..., description="YYYY-MM-DD"),
):
    """Count NEPSE trading days in an inclusive range."""
    s = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end, "%Y-%m-%d").date()
    return {
        "start": s.isoformat(),
        "end": e.isoformat(),
        "trading_days": cal.count_trading_days(s, e),
    }


@router.get("/holidays")
def holidays(year: int = Query(..., ge=2024, le=2030)):
    """List known NEPSE holidays for a year."""
    hols = sorted(h for h in cal.KNOWN_HOLIDAYS if h.year == year)
    return {
        "year": year,
        "count": len(hols),
        "holidays": [h.isoformat() for h in hols],
    }


@router.get("/festival-windows")
def festival_windows(d: str = Query(..., description="YYYY-MM-DD")):
    """Is the given date inside the Dashain or Tihar rally windows?"""
    parsed = datetime.strptime(d, "%Y-%m-%d").date()
    return {
        "date": parsed.isoformat(),
        "is_dashain_period": cal.is_dashain_period(parsed),
        "is_tihar_period": cal.is_tihar_period(parsed),
        "days_until_dashain": cal.days_until_dashain(parsed),
    }
