"""
Smoke + unit tests for the /api/v1/calendar router.

Covers every endpoint under the calendar prefix and verifies that the
Mon–Fri trading calendar is correctly reported (Sun and Sat must be
reported as weekends; Fri must be reported as a trading day).
"""

from __future__ import annotations

import pytest


# ── /api/v1/calendar/status ───────────────────────────────────────────────

class TestCalendarStatus:
    def test_returns_200(self, client):
        r = client.get("/api/v1/calendar/status")
        assert r.status_code == 200

    def test_required_fields_present(self, client):
        body = client.get("/api/v1/calendar/status").json()
        assert "nepal_datetime" in body
        assert "date" in body
        assert "weekday" in body
        assert "is_weekend" in body
        assert "is_known_holiday" in body
        assert "is_trading_day" in body
        assert "session_phase" in body
        assert "schedule" in body

    def test_schedule_shows_mon_fri(self, client):
        """Calendar must report Mon-Fri as the trading week."""
        sched = client.get("/api/v1/calendar/status").json()["schedule"]
        assert "trading_week" in sched
        assert "Mon" in sched["trading_week"] or "Monday" in sched["trading_week"]
        # Weekend must be Sat-Sun, NOT Fri-Sat
        assert "Sat" in sched["weekend"] or "Saturday" in sched["weekend"]

    def test_session_phase_is_valid_string(self, client):
        phase = client.get("/api/v1/calendar/status").json()["session_phase"]
        valid = {"PREMARKET", "PREOPEN", "OPEN", "POSTCLOSE", "WEEKEND", "HOLIDAY"}
        assert phase in valid, f"Unexpected session_phase: {phase!r}"

    def test_is_trading_day_consistent_with_weekend(self, client):
        body = client.get("/api/v1/calendar/status").json()
        if body["is_weekend"] or body["is_known_holiday"]:
            assert body["is_trading_day"] is False
        # If it's a trading day it cannot be a weekend (can still be a holiday edge-case)
        if body["is_trading_day"]:
            assert body["is_weekend"] is False


# ── /api/v1/calendar/is-trading-day ──────────────────────────────────────

class TestIsTradingDay:
    def test_monday_is_trading_day(self, client):
        # 2026-04-20 = Monday (not a holiday)
        r = client.get("/api/v1/calendar/is-trading-day", params={"d": "2026-04-20"})
        assert r.status_code == 200
        body = r.json()
        assert body["is_trading_day"] is True
        assert body["is_weekend"] is False

    def test_friday_is_trading_day(self, client):
        # 2026-04-24 = Friday (now a trading day under Mon-Fri)
        r = client.get("/api/v1/calendar/is-trading-day", params={"d": "2026-04-24"})
        assert r.status_code == 200
        body = r.json()
        assert body["is_trading_day"] is True
        assert body["is_weekend"] is False

    def test_saturday_is_not_trading_day(self, client):
        # 2026-04-25 = Saturday (weekend)
        r = client.get("/api/v1/calendar/is-trading-day", params={"d": "2026-04-25"})
        assert r.status_code == 200
        body = r.json()
        assert body["is_weekend"] is True
        assert body["is_trading_day"] is False

    def test_sunday_is_not_trading_day(self, client):
        # 2026-04-26 = Sunday (weekend under Mon-Fri)
        r = client.get("/api/v1/calendar/is-trading-day", params={"d": "2026-04-26"})
        assert r.status_code == 200
        body = r.json()
        assert body["is_weekend"] is True
        assert body["is_trading_day"] is False

    def test_nepali_new_year_is_holiday(self, client):
        # 2026-04-14 = Nepali New Year (known holiday)
        r = client.get("/api/v1/calendar/is-trading-day", params={"d": "2026-04-14"})
        assert r.status_code == 200
        body = r.json()
        assert body["is_known_holiday"] is True
        assert body["is_trading_day"] is False

    def test_republic_day_is_holiday(self, client):
        # 2026-05-29 = Republic Day (known holiday)
        r = client.get("/api/v1/calendar/is-trading-day", params={"d": "2026-05-29"})
        assert r.status_code == 200
        body = r.json()
        assert body["is_known_holiday"] is True

    def test_date_field_echoed_correctly(self, client):
        r = client.get("/api/v1/calendar/is-trading-day", params={"d": "2026-04-20"})
        assert r.json()["date"] == "2026-04-20"

    def test_missing_d_param_returns_422(self, client):
        r = client.get("/api/v1/calendar/is-trading-day")
        assert r.status_code == 422

    @pytest.mark.parametrize("day", ["2026-04-20", "2026-04-21", "2026-04-22", "2026-04-23", "2026-04-24"])
    def test_all_weekdays_in_mon_fri_week_are_trading(self, client, day):
        """Full week Mon-Fri (with no holidays) must all be trading days."""
        r = client.get("/api/v1/calendar/is-trading-day", params={"d": day})
        assert r.status_code == 200
        assert r.json()["is_trading_day"] is True, f"{day} should be a trading day"

    @pytest.mark.parametrize("day", ["2026-04-25", "2026-04-26"])
    def test_sat_sun_are_not_trading(self, client, day):
        r = client.get("/api/v1/calendar/is-trading-day", params={"d": day})
        assert r.status_code == 200
        assert r.json()["is_trading_day"] is False, f"{day} should NOT be a trading day"


# ── /api/v1/calendar/next-trading-day ────────────────────────────────────

class TestNextTradingDay:
    def test_from_friday_gives_monday(self, client):
        # After Friday (non-holiday) the next day is Monday.
        r = client.get("/api/v1/calendar/next-trading-day", params={"d": "2026-04-24"})
        assert r.status_code == 200
        body = r.json()
        assert body["from"] == "2026-04-24"
        assert body["next_trading_day"] == "2026-04-27"   # Monday

    def test_from_saturday_gives_monday(self, client):
        r = client.get("/api/v1/calendar/next-trading-day", params={"d": "2026-04-25"})
        assert r.status_code == 200
        assert r.json()["next_trading_day"] == "2026-04-27"  # Monday

    def test_from_sunday_gives_monday(self, client):
        r = client.get("/api/v1/calendar/next-trading-day", params={"d": "2026-04-26"})
        assert r.status_code == 200
        assert r.json()["next_trading_day"] == "2026-04-27"  # Monday

    def test_from_monday_gives_tuesday(self, client):
        # Monday 20 April, no holiday → next day is Tuesday 21 April.
        r = client.get("/api/v1/calendar/next-trading-day", params={"d": "2026-04-20"})
        assert r.status_code == 200
        assert r.json()["next_trading_day"] == "2026-04-21"

    def test_skips_holiday(self, client):
        # From Thursday 2025-04-10, next should skip Friday 2025-04-11 (not holiday)
        # then Saturday 2025-04-12 and Sunday 2025-04-13, landing on Monday 2025-04-14
        # BUT 2025-04-14 is Nepali New Year (holiday) → should land on Tuesday 2025-04-15.
        r = client.get("/api/v1/calendar/next-trading-day", params={"d": "2025-04-12"})
        assert r.status_code == 200
        assert r.json()["next_trading_day"] == "2025-04-15"  # skips holiday Apr-14

    def test_missing_param_returns_422(self, client):
        r = client.get("/api/v1/calendar/next-trading-day")
        assert r.status_code == 422


# ── /api/v1/calendar/trading-days-between ────────────────────────────────

class TestTradingDaysBetween:
    def test_full_week_is_5_days(self, client):
        # Mon 2026-04-20 to Fri 2026-04-24 = 5 trading days (no holidays in range)
        r = client.get(
            "/api/v1/calendar/trading-days-between",
            params={"start": "2026-04-20", "end": "2026-04-24"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["trading_days"] == 5

    def test_includes_friday(self, client):
        # Single day: Friday should count as 1.
        r = client.get(
            "/api/v1/calendar/trading-days-between",
            params={"start": "2026-04-24", "end": "2026-04-24"},
        )
        assert r.status_code == 200
        assert r.json()["trading_days"] == 1

    def test_weekend_only_range_is_zero(self, client):
        # Sat + Sun = 0 trading days.
        r = client.get(
            "/api/v1/calendar/trading-days-between",
            params={"start": "2026-04-25", "end": "2026-04-26"},
        )
        assert r.status_code == 200
        assert r.json()["trading_days"] == 0

    def test_holiday_excluded_from_count(self, client):
        # 2026-04-13 Mon to 2026-04-17 Fri (4 days excluding Apr-14 holiday = 4 days)
        r = client.get(
            "/api/v1/calendar/trading-days-between",
            params={"start": "2026-04-13", "end": "2026-04-17"},
        )
        assert r.status_code == 200
        assert r.json()["trading_days"] == 4   # Mon + Wed + Thu + Fri (Apr-14 excluded)

    def test_response_echoes_dates(self, client):
        r = client.get(
            "/api/v1/calendar/trading-days-between",
            params={"start": "2026-04-20", "end": "2026-04-24"},
        )
        body = r.json()
        assert body["start"] == "2026-04-20"
        assert body["end"] == "2026-04-24"

    def test_missing_params_returns_422(self, client):
        r = client.get("/api/v1/calendar/trading-days-between")
        assert r.status_code == 422


# ── /api/v1/calendar/holidays ─────────────────────────────────────────────

class TestHolidays:
    def test_2025_holidays_returned(self, client):
        r = client.get("/api/v1/calendar/holidays", params={"year": 2025})
        assert r.status_code == 200
        body = r.json()
        assert body["year"] == 2025
        assert body["count"] > 0
        assert isinstance(body["holidays"], list)

    def test_2026_holidays_include_new_year(self, client):
        r = client.get("/api/v1/calendar/holidays", params={"year": 2026})
        assert r.status_code == 200
        holidays = r.json()["holidays"]
        assert "2026-04-14" in holidays, "Nepali New Year must be in 2026 holiday list"

    def test_2026_holidays_include_republic_day(self, client):
        r = client.get("/api/v1/calendar/holidays", params={"year": 2026})
        holidays = r.json()["holidays"]
        assert "2026-05-29" in holidays, "Republic Day must be in 2026 holiday list"

    def test_2026_holidays_include_dashain_block(self, client):
        r = client.get("/api/v1/calendar/holidays", params={"year": 2026})
        holidays = r.json()["holidays"]
        dashain_days = [h for h in holidays if h.startswith("2026-10")]
        assert len(dashain_days) >= 4, "At least 4 Dashain holiday days expected in Oct 2026"

    def test_2026_holidays_include_tihar_block(self, client):
        r = client.get("/api/v1/calendar/holidays", params={"year": 2026})
        holidays = r.json()["holidays"]
        tihar_days = [h for h in holidays if h.startswith("2026-11")]
        assert len(tihar_days) >= 3, "At least 3 Tihar holiday days expected in Nov 2026"

    def test_year_out_of_range_returns_422(self, client):
        r = client.get("/api/v1/calendar/holidays", params={"year": 2099})
        assert r.status_code == 422
        r = client.get("/api/v1/calendar/holidays", params={"year": 2020})
        assert r.status_code == 422

    def test_holidays_sorted(self, client):
        r = client.get("/api/v1/calendar/holidays", params={"year": 2025})
        holidays = r.json()["holidays"]
        assert holidays == sorted(holidays)

    def test_all_holidays_in_requested_year(self, client):
        for year in (2025, 2026):
            r = client.get("/api/v1/calendar/holidays", params={"year": year})
            for h in r.json()["holidays"]:
                assert h.startswith(str(year)), f"Holiday {h} not in year {year}"


# ── /api/v1/calendar/festival-windows ────────────────────────────────────

class TestFestivalWindows:
    def test_pre_dashain_detected(self, client):
        # 3 days before Dashain 2025 start (Sep 22 → Sep 19 is in the 21-day pre window)
        r = client.get("/api/v1/calendar/festival-windows", params={"d": "2025-09-15"})
        assert r.status_code == 200
        body = r.json()
        assert body["is_dashain_period"] is True

    def test_tihar_detected(self, client):
        r = client.get("/api/v1/calendar/festival-windows", params={"d": "2025-10-20"})
        assert r.status_code == 200
        assert r.json()["is_tihar_period"] is True

    def test_non_festival_period(self, client):
        r = client.get("/api/v1/calendar/festival-windows", params={"d": "2026-04-20"})
        assert r.status_code == 200
        body = r.json()
        assert body["is_dashain_period"] is False
        assert body["is_tihar_period"] is False

    def test_days_until_dashain_non_negative(self, client):
        r = client.get("/api/v1/calendar/festival-windows", params={"d": "2025-06-01"})
        body = r.json()
        if body["days_until_dashain"] is not None:
            assert body["days_until_dashain"] >= 0

    def test_date_field_echoed(self, client):
        r = client.get("/api/v1/calendar/festival-windows", params={"d": "2025-09-15"})
        assert r.json()["date"] == "2025-09-15"

    def test_missing_param_returns_422(self, client):
        r = client.get("/api/v1/calendar/festival-windows")
        assert r.status_code == 422
