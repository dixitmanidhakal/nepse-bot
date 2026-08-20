"""
API tests for the /api/v1/recommendations router.

Uses the TestClient against the real FastAPI app. Tests that hit
the SQLite snapshot skip themselves gracefully when the DB is not
configured.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def _require_db(historical_provider_available):
    """Reuse the SQLite availability gate for API tests."""
    return historical_provider_available


class TestUniverseEndpoint:
    def test_universe_summary(self, client, _require_db):
        r = client.get("/api/v1/recommendations/universe")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "success"
        data = body["data"]
        assert data["total_symbols"] > 0
        assert data["total_rows"] > 0
        assert data["latest_date"] is not None


class TestTopEndpoint:
    def test_top_default(self, client, _require_db):
        r = client.get(
            "/api/v1/recommendations/top",
            params={"limit": 5, "min_rows": 120, "max_symbols": 60},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "success"
        assert body["count"] <= 5
        assert isinstance(body["data"], list)
        if body["data"]:
            rec = body["data"][0]
            assert "symbol" in rec
            assert "score" in rec
            assert "action" in rec
            assert "factor_scores" in rec

    def test_top_filtered_by_action(self, client, _require_db):
        r = client.get(
            "/api/v1/recommendations/top",
            params={"limit": 10, "action": "BUY", "max_symbols": 60},
        )
        assert r.status_code == 200
        for rec in r.json()["data"]:
            assert rec["action"] == "BUY"

    def test_top_min_score_filter(self, client, _require_db):
        r = client.get(
            "/api/v1/recommendations/top",
            params={"limit": 50, "min_score": 70, "max_symbols": 60},
        )
        assert r.status_code == 200
        for rec in r.json()["data"]:
            assert rec["score"] >= 70

    def test_top_validates_limit_bounds(self, client):
        r = client.get("/api/v1/recommendations/top", params={"limit": 0})
        assert r.status_code == 422
        r = client.get("/api/v1/recommendations/top", params={"limit": 10_000})
        assert r.status_code == 422


class TestSymbolEndpoint:
    def test_score_single_symbol(self, client, _require_db, historical_provider):
        # Pick a symbol that definitely has enough history.
        meta = historical_provider.symbol_metadata()
        candidates = [
            s for s, m in meta.items() if m.row_count >= 200
        ]
        if not candidates:
            pytest.skip("No symbol with >=200 rows in snapshot.")
        sym = candidates[0]
        r = client.get(f"/api/v1/recommendations/{sym}")
        assert r.status_code == 200
        rec = r.json()["data"]
        assert rec["symbol"] == sym.upper()

    def test_unknown_symbol_404(self, client, _require_db):
        r = client.get("/api/v1/recommendations/__DOES_NOT_EXIST__")
        assert r.status_code == 404


class TestExplainEndpoint:
    def test_explain_includes_weights(self, client, _require_db, historical_provider):
        meta = historical_provider.symbol_metadata()
        candidates = [s for s, m in meta.items() if m.row_count >= 200]
        if not candidates:
            pytest.skip("No symbol with >=200 rows in snapshot.")
        sym = candidates[0]
        r = client.get(f"/api/v1/recommendations/explain/{sym}")
        assert r.status_code == 200
        data = r.json()["data"]
        assert "weights" in data
        assert "history_rows" in data
        assert set(data["weights"].keys()) == {
            "trend", "momentum", "volume", "volatility", "drawdown"
        }


class TestScoreManyEndpoint:
    def test_score_many_requires_symbols(self, client):
        r = client.post("/api/v1/recommendations/score", json={"symbols": []})
        assert r.status_code == 422

    def test_score_many_with_valid_payload(
        self, client, _require_db, historical_provider
    ):
        meta = historical_provider.symbol_metadata()
        candidates = [s for s, m in meta.items() if m.row_count >= 200][:5]
        if len(candidates) < 2:
            pytest.skip("Need at least 2 symbols with >=200 rows.")
        r = client.post(
            "/api/v1/recommendations/score",
            json={"symbols": candidates, "min_rows": 120},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["count"] >= 1
        assert all("score" in x for x in body["data"])
