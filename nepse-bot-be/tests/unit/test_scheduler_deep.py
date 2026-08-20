"""
Deep unit tests for app/services/bot_scheduler.py

Covers the three gating predicates with deterministic frozen-time:
  - _is_market_window()           : full NEPSE scan window (04:45–09:30 UTC, Mon–Fri)
  - _is_best_entry_window()       : prime intraday entry band (05:45–08:45 UTC)
  - _is_first_nepse_day_of_month(): first Mon-Fri of each month

No APScheduler, no network, no DB.  All datetime.now() calls are monkey-patched
at the module level using the same frozen-time pattern used in test_base_bot_logic.
"""

from __future__ import annotations

import datetime as _dt
from contextlib import contextmanager
from typing import Generator

import pytest

import app.services.bot_scheduler as _sched_module
from app.services.bot_scheduler import (
    _is_market_window,
    _is_best_entry_window,
    _is_first_nepse_day_of_month,
    _MARKET_START_UTC_HOUR,
    _MARKET_START_UTC_MINUTE,
    _MARKET_END_UTC_HOUR,
    _MARKET_END_UTC_MINUTE,
    _BEST_ENTRY_START_HOUR,
    _BEST_ENTRY_START_MINUTE,
    _BEST_ENTRY_END_HOUR,
    _BEST_ENTRY_END_MINUTE,
    _NEPSE_DAYS,
)


# ── Frozen-time helper ────────────────────────────────────────────────────────

class _FakeDatetime:
    _frozen: _dt.datetime | None = None

    @classmethod
    def now(cls, tz=None) -> _dt.datetime:
        assert cls._frozen is not None, "freeze_utc must be active"
        return cls._frozen

    def __new__(cls, *args, **kwargs):
        return _dt.datetime(*args, **kwargs)


@contextmanager
def freeze_utc(
    year: int, month: int, day: int,
    hour: int = 7, minute: int = 0,
) -> Generator:
    """Replace datetime.now() inside bot_scheduler to return a fixed UTC instant."""
    _FakeDatetime._frozen = _dt.datetime(year, month, day, hour, minute,
                                          tzinfo=_dt.timezone.utc)
    orig = _sched_module.datetime
    _sched_module.datetime = _FakeDatetime  # type: ignore[assignment]
    try:
        yield _FakeDatetime._frozen
    finally:
        _sched_module.datetime = orig
        _FakeDatetime._frozen = None


# ── Helper: reference week (all 7 days needed) ──────────────────────────────
# Week of 2026-05-04 (Mon) … 2026-05-10 (Sun)
_MON = (2026, 5, 4)
_TUE = (2026, 5, 5)
_WED = (2026, 5, 6)
_THU = (2026, 5, 7)
_FRI = (2026, 5, 8)
_SAT = (2026, 5, 9)
_SUN = (2026, 5, 10)


# ── Module-level constant assertions ─────────────────────────────────────────

class TestSchedulerConstants:
    def test_market_start_before_market_end(self):
        start_min = _MARKET_START_UTC_HOUR * 60 + _MARKET_START_UTC_MINUTE
        end_min   = _MARKET_END_UTC_HOUR   * 60 + _MARKET_END_UTC_MINUTE
        assert start_min < end_min

    def test_best_entry_window_inside_market_window(self):
        """Prime entry band must be fully contained within the full market window."""
        mkt_start  = _MARKET_START_UTC_HOUR * 60 + _MARKET_START_UTC_MINUTE
        mkt_end    = _MARKET_END_UTC_HOUR   * 60 + _MARKET_END_UTC_MINUTE
        best_start = _BEST_ENTRY_START_HOUR * 60 + _BEST_ENTRY_START_MINUTE
        best_end   = _BEST_ENTRY_END_HOUR   * 60 + _BEST_ENTRY_END_MINUTE
        assert mkt_start <= best_start, "prime entry must start on/after market open"
        assert best_end  <= mkt_end,    "prime entry must end on/before market close"

    def test_nepse_days_is_monday_to_friday(self):
        assert _NEPSE_DAYS == {0, 1, 2, 3, 4}

    def test_market_window_nst_mapping(self):
        """04:45 UTC = 10:30 NST (pre-market scan start)."""
        assert _MARKET_START_UTC_HOUR   == 4
        assert _MARKET_START_UTC_MINUTE == 45

    def test_best_entry_window_nst_mapping(self):
        """05:45 UTC = 11:30 NST; 08:45 UTC = 14:30 NST."""
        assert _BEST_ENTRY_START_HOUR   == 5
        assert _BEST_ENTRY_START_MINUTE == 45
        assert _BEST_ENTRY_END_HOUR     == 8
        assert _BEST_ENTRY_END_MINUTE   == 45


# ── _is_market_window ─────────────────────────────────────────────────────────

class TestIsMarketWindow:
    """Full NEPSE scan window: 04:45–09:30 UTC on Mon–Fri."""

    def _check(self, ymd, hour, minute=0):
        y, m, d = ymd
        with freeze_utc(y, m, d, hour, minute):
            return _is_market_window()

    # ── Inside window — weekday ────────────────────────────────────────────

    def test_midday_monday_inside(self):
        assert self._check(_MON, 7, 0) is True

    def test_exact_market_start_inside(self):
        """04:45 UTC = window start → inclusive."""
        assert self._check(_WED, 4, 45) is True

    def test_exact_market_end_inside(self):
        """09:30 UTC = window end → inclusive."""
        assert self._check(_THU, 9, 30) is True

    def test_one_minute_after_start_inside(self):
        assert self._check(_TUE, 4, 46) is True

    def test_one_minute_before_end_inside(self):
        assert self._check(_FRI, 9, 29) is True

    def test_prime_entry_window_also_inside_market(self):
        """07:00 UTC = 12:45 NST — inside both windows."""
        assert self._check(_MON, 7, 0) is True

    # ── Outside window — time ─────────────────────────────────────────────

    def test_one_minute_before_start_outside(self):
        """04:44 UTC → market not yet open."""
        assert self._check(_MON, 4, 44) is False

    def test_one_minute_after_end_outside(self):
        """09:31 UTC → market closed."""
        assert self._check(_MON, 9, 31) is False

    def test_midnight_outside(self):
        assert self._check(_MON, 0, 0) is False

    def test_end_of_day_outside(self):
        assert self._check(_MON, 23, 59) is False

    # ── Outside window — weekend ──────────────────────────────────────────

    def test_saturday_midday_outside(self):
        """Saturday is not a NEPSE trading day."""
        assert self._check(_SAT, 7, 0) is False

    def test_sunday_midday_outside(self):
        assert self._check(_SUN, 7, 0) is False

    def test_saturday_at_exact_market_start_outside(self):
        assert self._check(_SAT, 4, 45) is False

    # ── All 5 weekdays inside ─────────────────────────────────────────────

    @pytest.mark.parametrize("ymd", [_MON, _TUE, _WED, _THU, _FRI])
    def test_all_weekdays_inside_at_noon(self, ymd):
        assert self._check(ymd, 7, 0) is True


# ── _is_best_entry_window ─────────────────────────────────────────────────────

class TestIsBestEntryWindow:
    """Prime intraday entry band: 05:45–08:45 UTC on Mon–Fri."""

    def _check(self, ymd, hour, minute=0):
        y, m, d = ymd
        with freeze_utc(y, m, d, hour, minute):
            return _is_best_entry_window()

    # ── Inside window ─────────────────────────────────────────────────────

    def test_midpoint_inside(self):
        """07:15 UTC = 13:00 NST → inside."""
        assert self._check(_MON, 7, 15) is True

    def test_exact_start_inside(self):
        """05:45 UTC → inclusive start."""
        assert self._check(_TUE, 5, 45) is True

    def test_exact_end_inside(self):
        """08:45 UTC → inclusive end."""
        assert self._check(_WED, 8, 45) is True

    def test_one_minute_after_start_inside(self):
        assert self._check(_THU, 5, 46) is True

    def test_one_minute_before_end_inside(self):
        assert self._check(_FRI, 8, 44) is True

    def test_peak_trading_hours_inside(self):
        """06:15, 07:00, 08:15 UTC (12:00, 12:45, 14:00 NST) all inside."""
        for h, m in [(6, 15), (7, 0), (8, 15)]:
            assert self._check(_MON, h, m) is True, f"Expected True at {h:02d}:{m:02d} UTC"

    # ── Outside window — time ─────────────────────────────────────────────

    def test_one_minute_before_start_outside(self):
        """05:44 UTC → one minute before window."""
        assert self._check(_MON, 5, 44) is False

    def test_one_minute_after_end_outside(self):
        """08:46 UTC → one minute after window."""
        assert self._check(_MON, 8, 46) is False

    def test_market_pre_open_outside(self):
        """04:50 UTC = inside market window but outside prime entry."""
        assert self._check(_MON, 4, 50) is False

    def test_market_post_close_outside(self):
        """09:15 UTC = inside market window but past prime entry."""
        assert self._check(_MON, 9, 15) is False

    def test_midnight_outside(self):
        assert self._check(_MON, 0, 0) is False

    # ── Outside window — weekend ──────────────────────────────────────────

    def test_saturday_outside(self):
        assert self._check(_SAT, 7, 0) is False

    def test_sunday_outside(self):
        assert self._check(_SUN, 7, 0) is False


# ── _is_first_nepse_day_of_month ──────────────────────────────────────────────

class TestIsFirstNepseDayOfMonth:
    """Fires on the first Mon–Fri of each month, false all other days."""

    def _check(self, ymd, hour=7):
        y, m, d = ymd
        with freeze_utc(y, m, d, hour):
            return _is_first_nepse_day_of_month()

    # ── May 2026: first NEPSE day is Friday, 1st ──────────────────────────
    # (May 1, 2026 is a Friday — verified by calendar)

    def test_may_2026_first_is_friday_1st(self):
        """2026-05-01 is a Friday → first NEPSE day."""
        # Verify: May 1 2026 weekday = 4 (Friday) — confirmed
        assert _dt.date(2026, 5, 1).weekday() == 4  # Friday
        assert self._check((2026, 5, 1)) is True

    def test_may_2026_monday_4th_is_not_first(self):
        """May 4 is Monday, but May 1 (Fri) was already the first NEPSE day."""
        assert self._check(_MON) is False

    def test_may_2026_tuesday_5th_is_not_first(self):
        assert self._check(_TUE) is False

    # ── June 2026: first NEPSE day is Monday, 1st ─────────────────────────
    # (June 1, 2026 is a Monday — verified by calendar)

    def test_june_2026_first_is_monday_1st(self):
        assert _dt.date(2026, 6, 1).weekday() == 0  # Monday
        assert self._check((2026, 6, 1)) is True

    def test_june_2026_tuesday_2nd_is_not_first(self):
        assert self._check((2026, 6, 2)) is False

    def test_june_2026_wednesday_3rd_is_not_first(self):
        assert self._check((2026, 6, 3)) is False

    # ── Month whose 1st is Saturday: first NEPSE day is Monday, 3rd ─────
    # January 2026: Jan 1 is Thursday → first NEPSE day IS Jan 1

    def test_jan_2026_first_is_thursday_1st(self):
        assert _dt.date(2026, 1, 1).weekday() == 3  # Thursday
        assert self._check((2026, 1, 1)) is True

    def test_jan_2026_2nd_is_not_first_nepse(self):
        assert self._check((2026, 1, 2)) is False

    # ── Month whose 1st is Sunday: first NEPSE day is Monday, 2nd ────────
    # March 2026: March 1 is Sunday → first NEPSE day is Mon March 2

    def test_march_2026_first_sunday_skipped(self):
        assert _dt.date(2026, 3, 1).weekday() == 6  # Sunday
        assert self._check((2026, 3, 1)) is False

    def test_march_2026_monday_2nd_is_first(self):
        assert _dt.date(2026, 3, 2).weekday() == 0  # Monday
        assert self._check((2026, 3, 2)) is True

    def test_march_2026_tuesday_3rd_is_not_first(self):
        assert self._check((2026, 3, 3)) is False

    # ── Weekend guard ─────────────────────────────────────────────────────

    def test_returns_false_on_saturday(self):
        """Even if logic picks a Sat as first day, the weekday guard fires."""
        assert self._check(_SAT) is False

    def test_returns_false_on_sunday(self):
        assert self._check(_SUN) is False

    # ── Return type ───────────────────────────────────────────────────────

    def test_return_type_is_bool(self):
        with freeze_utc(*_MON, 7):
            result = _is_first_nepse_day_of_month()
        assert isinstance(result, bool)
