"""
Unit tests for the NEPSE market-hours helper.

Updated for the Mon–Fri trading calendar (changed from legacy Sun–Thu).
All test dates use unambiguous Mon-Fri examples so results are deterministic.

Reference dates (April 2026):
  Mon 2026-04-20  → valid trading day
  Fri 2026-04-24  → valid trading day  (was closed under old Sun-Thu calendar)
  Sat 2026-04-25  → closed (weekend)
  Sun 2026-04-26  → closed (weekend)  (was trading under old Sun-Thu calendar)
  Mon 2026-04-27  → valid trading day
"""
from __future__ import annotations

from datetime import datetime

from app.services.data.market_hours import NPT, SESSION_OPEN, SESSION_CLOSE, session_status


def _npt(y, m, d, hh, mm=0):
    """Return a Nepal-timezone aware datetime for testing."""
    return datetime(y, m, d, hh, mm, tzinfo=NPT)


class TestSessionStatusMonFri:
    # ── Monday (trading day) ──────────────────────────────────────────────

    def test_monday_open_midsession(self):
        # 2026-04-20 is a Monday — trading day under Mon-Fri schedule.
        s = session_status(_npt(2026, 4, 20, 12, 30))
        assert s.is_open is True
        assert s.is_poll_window is True
        assert s.reason == "market open"

    def test_monday_preopen_buffer(self):
        # 10:57 Mon = inside the pre-open poll buffer (10:55–11:00).
        s = session_status(_npt(2026, 4, 20, 10, 57))
        assert s.is_open is False
        assert s.is_poll_window is True
        assert "pre" in s.reason.lower()

    def test_monday_postclose_buffer(self):
        # 15:03 Mon = just past market close, inside the post-close buffer.
        s = session_status(_npt(2026, 4, 20, 15, 3))
        assert s.is_open is False
        assert s.is_poll_window is True
        assert "post" in s.reason.lower()

    def test_monday_after_postclose_buffer(self):
        # 16:00 Mon = well outside both session and poll window.
        s = session_status(_npt(2026, 4, 20, 16, 0))
        assert s.is_open is False
        assert s.is_poll_window is False

    def test_monday_before_preopen(self):
        # 08:00 Mon = before pre-open buffer.
        s = session_status(_npt(2026, 4, 20, 8, 0))
        assert s.is_open is False
        assert s.is_poll_window is False

    # ── Friday (NOW a trading day under Mon-Fri) ──────────────────────────

    def test_friday_is_trading_day(self):
        # 2026-04-24 is a Friday — OPEN under Mon-Fri calendar.
        s = session_status(_npt(2026, 4, 24, 12, 0))
        assert s.is_open is True
        assert s.is_poll_window is True
        assert s.reason == "market open"

    def test_friday_session_open_matches_constants(self):
        # Exactly at SESSION_OPEN on Friday → open.
        s = session_status(_npt(2026, 4, 24, SESSION_OPEN.hour, SESSION_OPEN.minute))
        assert s.is_open is True

    def test_friday_exactly_at_close_is_post_close(self):
        # Exactly at SESSION_CLOSE on Friday → post-close (half-open interval).
        s = session_status(_npt(2026, 4, 24, SESSION_CLOSE.hour, SESSION_CLOSE.minute))
        assert s.is_open is False

    # ── Saturday (weekend) ───────────────────────────────────────────────

    def test_saturday_always_closed(self):
        # 2026-04-25 is a Saturday.
        s = session_status(_npt(2026, 4, 25, 12, 0))
        assert s.is_open is False
        assert s.is_poll_window is False
        assert "saturday" in s.reason.lower()

    def test_saturday_seconds_to_open_positive(self):
        s = session_status(_npt(2026, 4, 25, 12, 0))
        assert s.seconds_to_open is not None
        assert s.seconds_to_open > 0

    # ── Sunday (NOW weekend under Mon-Fri) ───────────────────────────────

    def test_sunday_now_closed(self):
        # 2026-04-26 is a Sunday — CLOSED under Mon-Fri calendar.
        s = session_status(_npt(2026, 4, 26, 12, 0))
        assert s.is_open is False
        assert s.is_poll_window is False
        assert "sunday" in s.reason.lower()

    def test_sunday_next_open_is_monday(self):
        # Sunday 16:00 → next trading session = Monday 11:00.
        s = session_status(_npt(2026, 4, 26, 16, 0))
        assert s.seconds_to_open is not None
        # Monday 11:00 is about 19 hours away from Sunday 16:00.
        assert 60 * 60 * 17 < s.seconds_to_open < 60 * 60 * 21

    # ── Holiday override ──────────────────────────────────────────────────

    def test_holiday_on_monday_closes_market(self):
        # Even on a Monday, a holiday closes the market.
        s = session_status(_npt(2026, 4, 20, 12, 0), holidays=["2026-04-20"])
        assert s.is_open is False
        assert s.is_poll_window is False
        assert s.reason == "holiday"

    def test_nepali_new_year_holiday(self):
        # 2026-04-14 is Nepali New Year — always a NEPSE holiday.
        # It's a Tuesday in 2026 (normally a trading day).
        s = session_status(_npt(2026, 4, 14, 12, 0), holidays=["2026-04-14"])
        assert s.is_open is False
        assert s.reason == "holiday"

    def test_holiday_does_not_affect_other_days(self):
        # Holiday declared only for Monday; Tuesday should still trade.
        s = session_status(_npt(2026, 4, 21, 12, 0), holidays=["2026-04-20"])
        assert s.is_open is True

    # ── Weekday boundaries ────────────────────────────────────────────────

    def test_all_five_trading_days_open(self):
        """Verify Mon–Fri are all trading days during session hours."""
        # Week of 2026-04-20 (Mon) to 2026-04-24 (Fri)
        expected_trading = [20, 21, 22, 23, 24]   # Mon–Fri
        for day in expected_trading:
            s = session_status(_npt(2026, 4, day, 13, 0))
            assert s.is_open is True, f"Expected 2026-04-{day} to be open"

    def test_both_weekend_days_closed(self):
        """Sat and Sun are both closed under Mon-Fri calendar."""
        s_sat = session_status(_npt(2026, 4, 25, 13, 0))
        s_sun = session_status(_npt(2026, 4, 26, 13, 0))
        assert s_sat.is_open is False
        assert s_sun.is_open is False

    def test_saturday_and_sunday_reason_labels(self):
        """Closed-day reason must mention the correct day name."""
        s_sat = session_status(_npt(2026, 4, 25, 13, 0))
        s_sun = session_status(_npt(2026, 4, 26, 13, 0))
        assert "saturday" in s_sat.reason.lower()
        assert "sunday" in s_sun.reason.lower()

    # ── Seconds-to-open helpers ───────────────────────────────────────────

    def test_saturday_next_open_within_48h(self):
        # Saturday noon → next open is Monday 11:00, well within 48 h.
        s = session_status(_npt(2026, 4, 25, 12, 0))
        assert s.seconds_to_open is not None
        assert 0 < s.seconds_to_open < 60 * 60 * 50

    def test_seconds_to_close_set_during_session(self):
        s = session_status(_npt(2026, 4, 20, 13, 0))
        assert s.seconds_to_close is not None
        assert s.seconds_to_close > 0
        # At 13:00 there are 2 hours left = 7200 s.
        assert abs(s.seconds_to_close - 7200) < 120

    def test_seconds_to_close_none_outside_session(self):
        s = session_status(_npt(2026, 4, 26, 12, 0))  # Sunday
        assert s.seconds_to_close is None
