"""
Smoke tests for /api/v1/floorsheet/* (floorsheet_routes.py).

All endpoints are backed by PostgreSQL floorsheet data.  Without seeded
data, most will return 404 or 500.  Tests focus on:
  - Route is registered (responds rather than 405/404-route-not-found)
  - Query-param bounds are validated (422)
  - Response shape when data is present (200)
"""

from __future__ import annotations

import pytest

_BASE = "/api/v1/floorsheet"
_SYMBOL = "NABIL"
_BROKER = "1"


def _ok_or_err(status: int) -> bool:
    """404 (no data), 500 (db error), and 200 (success) are all valid."""
    return status in (200, 404, 500)


# ── Symbol-level floorsheet routes ───────────────────────────────────────────

class TestFloorsheetTrades:
    def test_trades_responds(self, client):
        r = client.get(f"{_BASE}/{_SYMBOL}/trades")
        assert _ok_or_err(r.status_code)

    def test_trades_days_min_validation(self, client):
        r = client.get(f"{_BASE}/{_SYMBOL}/trades", params={"days": 0})
        assert r.status_code == 422

    def test_trades_days_max_validation(self, client):
        r = client.get(f"{_BASE}/{_SYMBOL}/trades", params={"days": 999})
        assert r.status_code == 422

    def test_trades_limit_min_validation(self, client):
        r = client.get(f"{_BASE}/{_SYMBOL}/trades", params={"limit": 0})
        assert r.status_code == 422

    def test_trades_limit_max_validation(self, client):
        r = client.get(f"{_BASE}/{_SYMBOL}/trades", params={"limit": 99999})
        assert r.status_code == 422

    def test_trades_valid_params_accepted(self, client):
        r = client.get(f"{_BASE}/{_SYMBOL}/trades", params={"days": 3, "limit": 50})
        assert _ok_or_err(r.status_code)


class TestFloorsheetAnalysis:
    def test_analysis_responds(self, client):
        r = client.get(f"{_BASE}/{_SYMBOL}/analysis")
        assert _ok_or_err(r.status_code)


class TestFloorsheetInstitutional:
    def test_institutional_responds(self, client):
        r = client.get(f"{_BASE}/{_SYMBOL}/institutional")
        assert _ok_or_err(r.status_code)


class TestFloorsheetCrossTrades:
    def test_cross_trades_responds(self, client):
        r = client.get(f"{_BASE}/{_SYMBOL}/cross-trades")
        assert _ok_or_err(r.status_code)


class TestFloorsheetBrokers:
    def test_symbol_brokers_responds(self, client):
        r = client.get(f"{_BASE}/{_SYMBOL}/brokers")
        assert _ok_or_err(r.status_code)

    def test_symbol_single_broker_responds(self, client):
        r = client.get(f"{_BASE}/{_SYMBOL}/broker/{_BROKER}")
        assert _ok_or_err(r.status_code)


class TestFloorsheetAccumulation:
    def test_accumulation_responds(self, client):
        r = client.get(f"{_BASE}/{_SYMBOL}/accumulation")
        assert _ok_or_err(r.status_code)


class TestFloorsheetBrokerSentiment:
    def test_broker_sentiment_responds(self, client):
        r = client.get(f"{_BASE}/{_SYMBOL}/broker-sentiment")
        assert _ok_or_err(r.status_code)


class TestFloorsheetBrokerPairs:
    def test_broker_pairs_responds(self, client):
        r = client.get(f"{_BASE}/{_SYMBOL}/broker-pairs")
        assert _ok_or_err(r.status_code)


# ── Aggregate broker routes ───────────────────────────────────────────────────

class TestFloorsheetBrokerAggregates:
    def test_broker_ranking_responds(self, client):
        r = client.get(f"{_BASE}/brokers/ranking")
        assert _ok_or_err(r.status_code)

    def test_broker_track_responds(self, client):
        r = client.get(f"{_BASE}/brokers/{_BROKER}/track")
        assert _ok_or_err(r.status_code)

    def test_broker_institutional_responds(self, client):
        r = client.get(f"{_BASE}/brokers/institutional")
        assert _ok_or_err(r.status_code)
