"""
NEPSE market-hours helper.

The Nepal Stock Exchange runs continuous trading Mon–Fri 11:00–15:00 NPT
(UTC+05:45). This module centralises every timezone-aware check so the
depth poller, recommendation engine, and UI agree on what "market is open"
means.

No external dependencies beyond stdlib.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Iterable, Optional, Set

# Fixed offset avoids depending on a tz database being present.
NPT = timezone(timedelta(hours=5, minutes=45), name="NPT")

# Monday=0 … Friday=4 in Python's datetime.weekday(). NEPSE trades Mon–Fri.
_TRADING_WEEKDAYS: Set[int] = {0, 1, 2, 3, 4}  # Mon, Tue, Wed, Thu, Fri

# Continuous-trading session.
SESSION_OPEN = time(11, 0, 0)
SESSION_CLOSE = time(15, 0, 0)

# Poller runs a little before/after the session so we catch the open-auction
# and don't leave late traders without a fresh snapshot.
PREOPEN_BUFFER_MINUTES = 5
POSTCLOSE_BUFFER_MINUTES = 5


def _parse_date(s: str) -> Optional[datetime]:
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").replace(tzinfo=NPT)
    except Exception:
        return None


def _holidays_from_env() -> Set[str]:
    """NEPSE_HOLIDAYS=2026-04-14,2026-05-01  (YYYY-MM-DD, NPT)."""
    raw = os.getenv("NEPSE_HOLIDAYS", "").strip()
    if not raw:
        return set()
    out: Set[str] = set()
    for item in raw.split(","):
        d = _parse_date(item)
        if d:
            out.add(d.strftime("%Y-%m-%d"))
    return out


@dataclass(frozen=True)
class SessionStatus:
    is_open: bool
    is_poll_window: bool     # slightly wider — inside pre-open / post-close buffer
    reason: str              # human-readable state
    now_npt: datetime
    seconds_to_open: Optional[float]
    seconds_to_close: Optional[float]


def _next_session_open(now_npt: datetime, holidays: Set[str]) -> datetime:
    """Find the next session-open datetime strictly after `now_npt`."""
    candidate = now_npt.replace(
        hour=SESSION_OPEN.hour, minute=SESSION_OPEN.minute, second=0, microsecond=0
    )
    if candidate <= now_npt:
        candidate += timedelta(days=1)
    for _ in range(14):  # at most two weeks lookahead
        if (
            candidate.weekday() in _TRADING_WEEKDAYS
            and candidate.strftime("%Y-%m-%d") not in holidays
        ):
            return candidate
        candidate += timedelta(days=1)
    return candidate  # fallback; should never hit


def session_status(
    now: Optional[datetime] = None,
    holidays: Optional[Iterable[str]] = None,
) -> SessionStatus:
    """
    Return the current market-session status.

    Parameters
    ----------
    now       : optional datetime (aware or naive UTC). Defaults to wall clock.
    holidays  : optional iterable of "YYYY-MM-DD" NPT strings. Defaults to
                ``NEPSE_HOLIDAYS`` env var.
    """
    if now is None:
        now_npt = datetime.now(tz=NPT)
    elif now.tzinfo is None:
        now_npt = now.replace(tzinfo=timezone.utc).astimezone(NPT)
    else:
        now_npt = now.astimezone(NPT)

    holiday_set = set(holidays) if holidays is not None else _holidays_from_env()
    today_str = now_npt.strftime("%Y-%m-%d")

    is_trading_day = (
        now_npt.weekday() in _TRADING_WEEKDAYS and today_str not in holiday_set
    )

    open_dt = now_npt.replace(
        hour=SESSION_OPEN.hour, minute=SESSION_OPEN.minute, second=0, microsecond=0
    )
    close_dt = now_npt.replace(
        hour=SESSION_CLOSE.hour, minute=SESSION_CLOSE.minute, second=0, microsecond=0
    )
    poll_open_dt = open_dt - timedelta(minutes=PREOPEN_BUFFER_MINUTES)
    poll_close_dt = close_dt + timedelta(minutes=POSTCLOSE_BUFFER_MINUTES)

    is_open = is_trading_day and open_dt <= now_npt < close_dt
    is_poll_window = (
        is_trading_day and poll_open_dt <= now_npt < poll_close_dt
    )

    if is_open:
        reason = "market open"
        secs_to_open: Optional[float] = 0.0
        secs_to_close: Optional[float] = (close_dt - now_npt).total_seconds()
    elif is_trading_day and now_npt < open_dt:
        reason = "pre-open"
        secs_to_open = (open_dt - now_npt).total_seconds()
        secs_to_close = (close_dt - now_npt).total_seconds()
    elif is_trading_day and now_npt >= close_dt:
        reason = "post-close"
        nxt = _next_session_open(now_npt, holiday_set)
        secs_to_open = (nxt - now_npt).total_seconds()
        secs_to_close = None
    else:
        # Sat / Sun / holiday
        if now_npt.weekday() == 5:
            reason = "saturday (market closed)"
        elif now_npt.weekday() == 6:
            reason = "sunday (market closed)"
        elif today_str in holiday_set:
            reason = "holiday"
        else:
            reason = "non-trading day"
        nxt = _next_session_open(now_npt, holiday_set)
        secs_to_open = (nxt - now_npt).total_seconds()
        secs_to_close = None

    return SessionStatus(
        is_open=is_open,
        is_poll_window=is_poll_window,
        reason=reason,
        now_npt=now_npt,
        seconds_to_open=secs_to_open,
        seconds_to_close=secs_to_close,
    )


def is_market_open(now: Optional[datetime] = None) -> bool:
    return session_status(now).is_open


def is_poll_window(now: Optional[datetime] = None) -> bool:
    return session_status(now).is_poll_window


__all__ = [
    "NPT",
    "SESSION_OPEN",
    "SESSION_CLOSE",
    "PREOPEN_BUFFER_MINUTES",
    "POSTCLOSE_BUFFER_MINUTES",
    "SessionStatus",
    "session_status",
    "is_market_open",
    "is_poll_window",
]
