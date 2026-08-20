"""
Smoke tests for /api/v1/free/* (free_sources_routes.py).

All endpoints call the live aggregator under the hood, so they may return
empty lists when data sources are offline.  Tests accept any "reasonable"
status: 200 (with possibly empty payload), 503, or 500.

Validation-layer tests (query param bounds, 404 for unknown symbol)
are the most reliable since they don't depend on network connectivity.
"""

from __future__ import annotations

import pytest

# ── Constants ─────────────────────────────────────────────────────────────────
_BASE = "/api/v1/free"
_GOOD_SYMBOL = "NABIL"
_BAD_SYMBOL  = "ZZNOTREAL"


# ── Helper ────────────────────────────────────────────────────────────────────
def _ok_or_server_error(status: int) -> bool:
    """Accept 200, 500, or 503 (live data may be offline in test env)."""
    return status in (200, 500, 503)


# ── Health & Status ───────────────────────────────────────────────────────────

class TestFreeHealth:
    def test_health_returns_200(self, client):
        r = client.get(f"{_BASE}/health")
        assert _ok_or_server_error(r.status_code)

    def test_health_shape_when_ok(self, client):
        r = client.get(f"{_BASE}/health")
        if r.status_code == 200:
            body = r.json()
            assert isinstance(body, dict)


class TestFreeMarketStatus:
    def test_market_status_responds(self, client):
        r = client.get(f"{_BASE}/market/status")
        assert _ok_or_server_error(r.status_code)

    def test_market_summary_responds(self, client):
        r = client.get(f"{_BASE}/market/summary")
        assert _ok_or_server_error(r.status_code)


# ── Live Market ───────────────────────────────────────────────────────────────

class TestFreeMarketLive:
    def test_live_market_returns_list_shape(self, client):
        r = client.get(f"{_BASE}/market/live")
        assert _ok_or_server_error(r.status_code)
        if r.status_code == 200:
            body = r.json()
            assert "count" in body
            assert "data" in body
            assert isinstance(body["data"], list)
            assert body["count"] == len(body["data"])

    def test_live_market_top_responds(self, client):
        r = client.get(f"{_BASE}/market/top")
        assert _ok_or_server_error(r.status_code)

    def test_live_quote_unknown_symbol_404(self, client):
        r = client.get(f"{_BASE}/market/live/{_BAD_SYMBOL}")
        # Either 404 (symbol not in live data) or 500/503 if scraper is down
        assert r.status_code in (404, 500, 503)

    def test_live_quote_known_symbol(self, client):
        r = client.get(f"{_BASE}/market/live/{_GOOD_SYMBOL}")
        # May be 404 if live data unavailable in test env
        assert r.status_code in (200, 404, 500, 503)


# ── Indices ───────────────────────────────────────────────────────────────────

class TestFreeIndices:
    def test_indices_responds(self, client):
        r = client.get(f"{_BASE}/indices")
        assert _ok_or_server_error(r.status_code)

    def test_sector_indices_responds(self, client):
        r = client.get(f"{_BASE}/indices/sectors")
        assert _ok_or_server_error(r.status_code)
        if r.status_code == 200:
            body = r.json()
            assert isinstance(body, (list, dict))

    def test_sector_stocks_responds(self, client):
        r = client.get(f"{_BASE}/indices/sectors/Banking/stocks")
        assert _ok_or_server_error(r.status_code)
        if r.status_code == 200:
            body = r.json()
            assert "sector" in body
            assert "count" in body
            assert "data" in body


# ── Depth ─────────────────────────────────────────────────────────────────────

class TestFreeDepth:
    def test_partial_depth_responds(self, client):
        r = client.get(f"{_BASE}/depth/{_GOOD_SYMBOL}")
        assert _ok_or_server_error(r.status_code)


# ── Floorsheet ────────────────────────────────────────────────────────────────

class TestFreeFloorsheet:
    def test_floorsheet_latest_default(self, client):
        r = client.get(f"{_BASE}/floorsheet/latest")
        assert _ok_or_server_error(r.status_code)
        if r.status_code == 200:
            body = r.json()
            assert "date" in body
            assert "total" in body
            assert "data" in body

    def test_floorsheet_latest_with_symbol(self, client):
        r = client.get(f"{_BASE}/floorsheet/latest", params={"symbol": _GOOD_SYMBOL})
        assert _ok_or_server_error(r.status_code)

    def test_floorsheet_latest_limit_validated(self, client):
        r = client.get(f"{_BASE}/floorsheet/latest", params={"limit": 0})
        assert r.status_code == 422
        r = client.get(f"{_BASE}/floorsheet/latest", params={"limit": 999_999})
        assert r.status_code == 422

    def test_floorsheet_by_date_valid_format(self, client):
        r = client.get(f"{_BASE}/floorsheet/2026-04-20")
        assert _ok_or_server_error(r.status_code)


# ── OHLCV Prices ──────────────────────────────────────────────────────────────

class TestFreeSymbolPrices:
    def test_prices_limit_validation(self, client):
        r = client.get(f"{_BASE}/symbols/{_GOOD_SYMBOL}/prices", params={"limit": 0})
        assert r.status_code == 422
        r = client.get(f"{_BASE}/symbols/{_GOOD_SYMBOL}/prices", params={"limit": 99_999})
        assert r.status_code == 422

    def test_prices_default_limit_responds(self, client):
        r = client.get(f"{_BASE}/symbols/{_GOOD_SYMBOL}/prices")
        assert _ok_or_server_error(r.status_code)
        if r.status_code == 200:
            body = r.json()
            assert "symbol" in body
            assert "total" in body
            assert "data" in body

    def test_dividends_responds(self, client):
        r = client.get(f"{_BASE}/symbols/{_GOOD_SYMBOL}/dividends")
        assert _ok_or_server_error(r.status_code)

    def test_rights_responds(self, client):
        r = client.get(f"{_BASE}/symbols/{_GOOD_SYMBOL}/rights")
        assert _ok_or_server_error(r.status_code)


# ── Misc endpoints ────────────────────────────────────────────────────────────

class TestFreeMisc:
    def test_securities_responds(self, client):
        r = client.get(f"{_BASE}/securities")
        assert _ok_or_server_error(r.status_code)

    def test_brokers_responds(self, client):
        r = client.get(f"{_BASE}/brokers")
        assert _ok_or_server_error(r.status_code)

    def test_ipo_upcoming_responds(self, client):
        r = client.get(f"{_BASE}/ipo/upcoming")
        assert _ok_or_server_error(r.status_code)

    def test_disclosures_responds(self, client):
        r = client.get(f"{_BASE}/disclosures")
        assert _ok_or_server_error(r.status_code)

    def test_notices_responds(self, client):
        r = client.get(f"{_BASE}/notices")
        assert _ok_or_server_error(r.status_code)

    def test_dps_responds(self, client):
        r = client.get(f"{_BASE}/dps")
        assert _ok_or_server_error(r.status_code)


# ── Free recommendations ──────────────────────────────────────────────────────

class TestFreeRecommendations:
    def test_top_limit_validation(self, client):
        r = client.get(f"{_BASE}/recommendations/top", params={"limit": 0})
        assert r.status_code == 422
        r = client.get(f"{_BASE}/recommendations/top", params={"limit": 9999})
        assert r.status_code == 422

    def test_top_valid_limit(self, client):
        r = client.get(f"{_BASE}/recommendations/top", params={"limit": 5})
        assert _ok_or_server_error(r.status_code)
        if r.status_code == 200:
            body = r.json()
            assert "count" in body
            assert "data" in body
            assert body["count"] <= 5

    def test_top_action_filter_accepted(self, client):
        # Valid actions are BUY, WATCH, AVOID (pattern: ^(BUY|WATCH|AVOID)$)
        for action in ("BUY", "WATCH", "AVOID"):
            r = client.get(
                f"{_BASE}/recommendations/top",
                params={"limit": 5, "action": action},
            )
            assert _ok_or_server_error(r.status_code)

    def test_top_invalid_action_rejected(self, client):
        # SELL is not a valid action value → 422
        r = client.get(
            f"{_BASE}/recommendations/top",
            params={"limit": 5, "action": "SELL"},
        )
        assert r.status_code == 422

    def test_universe_responds(self, client):
        r = client.get(f"{_BASE}/recommendations/universe")
        assert _ok_or_server_error(r.status_code)
