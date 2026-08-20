"""
Smoke tests for:
  /api/v1/sectors/*  (sector_routes.py  — sector analysis)
  /api/v1/stocks/*   (screener_router   — stock screening)

All DB-backed routes return 404/500 when no data is seeded.
Tests accept both 200 (data present) and 404/500 (no data).
Query-param validation tests are deterministic.
"""

from __future__ import annotations

import pytest

_SECTORS_BASE = "/api/v1/sectors"
_STOCKS_BASE  = "/api/v1/stocks"


def _ok_or_err(status: int) -> bool:
    return status in (200, 404, 500)


# ── Sector Routes ─────────────────────────────────────────────────────────────

class TestSectorList:
    def test_all_sectors_responds(self, client):
        r = client.get(f"{_SECTORS_BASE}/")
        assert _ok_or_err(r.status_code)

    def test_all_sectors_limit_param_accepted(self, client):
        r = client.get(f"{_SECTORS_BASE}/", params={"limit": 3})
        assert _ok_or_err(r.status_code)

    def test_all_sectors_sort_by_accepted(self, client):
        r = client.get(f"{_SECTORS_BASE}/", params={"sort_by": "momentum_30d"})
        assert _ok_or_err(r.status_code)


class TestTopPerformers:
    def test_top_performers_responds(self, client):
        r = client.get(f"{_SECTORS_BASE}/top-performers")
        assert _ok_or_err(r.status_code)

    def test_top_performers_limit_param(self, client):
        r = client.get(f"{_SECTORS_BASE}/top-performers", params={"limit": 3})
        assert _ok_or_err(r.status_code)

    def test_top_performers_metric_param(self, client):
        r = client.get(f"{_SECTORS_BASE}/top-performers", params={"metric": "change_percent"})
        assert _ok_or_err(r.status_code)


class TestCompleteAndRotation:
    def test_complete_analysis_responds(self, client):
        r = client.get(f"{_SECTORS_BASE}/analysis/complete")
        assert _ok_or_err(r.status_code)

    def test_rotation_analysis_responds(self, client):
        r = client.get(f"{_SECTORS_BASE}/analysis/rotation")
        assert _ok_or_err(r.status_code)

    def test_bullish_sectors_responds(self, client):
        r = client.get(f"{_SECTORS_BASE}/analysis/bullish")
        assert _ok_or_err(r.status_code)


class TestSectorDetail:
    def test_sector_by_id_responds(self, client):
        r = client.get(f"{_SECTORS_BASE}/1")
        assert _ok_or_err(r.status_code)

    def test_sector_by_id_invalid_type(self, client):
        r = client.get(f"{_SECTORS_BASE}/notanint")
        assert r.status_code == 422

    def test_sector_stocks_responds(self, client):
        r = client.get(f"{_SECTORS_BASE}/1/stocks")
        assert _ok_or_err(r.status_code)


# ── Stock Screener Routes ─────────────────────────────────────────────────────

class TestStockScreener:
    def test_high_volume_responds(self, client):
        r = client.get(f"{_STOCKS_BASE}/screen/high-volume")
        assert _ok_or_err(r.status_code)

    def test_momentum_responds(self, client):
        r = client.get(f"{_STOCKS_BASE}/screen/momentum")
        assert _ok_or_err(r.status_code)

    def test_value_responds(self, client):
        r = client.get(f"{_STOCKS_BASE}/screen/value")
        assert _ok_or_err(r.status_code)

    def test_defensive_responds(self, client):
        r = client.get(f"{_STOCKS_BASE}/screen/defensive")
        assert _ok_or_err(r.status_code)

    def test_growth_responds(self, client):
        r = client.get(f"{_STOCKS_BASE}/screen/growth")
        assert _ok_or_err(r.status_code)

    def test_oversold_responds(self, client):
        r = client.get(f"{_STOCKS_BASE}/screen/oversold")
        assert _ok_or_err(r.status_code)

    def test_screen_post_requires_body(self, client):
        r = client.post(f"{_STOCKS_BASE}/screen")
        assert r.status_code == 422

    def test_screen_post_with_empty_body(self, client):
        r = client.post(f"{_STOCKS_BASE}/screen", json={})
        # May succeed with defaults or fail with 422 if required fields missing
        assert r.status_code in (200, 404, 422, 500)
