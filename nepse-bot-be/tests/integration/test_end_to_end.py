"""
End-to-end integration tests that exercise multiple layers together.

These tests are intentionally broad — they wire the SQLite historical
provider to the recommendation engine to the REST API, verifying the
whole stack responds consistently.
"""

from __future__ import annotations

import pytest


class TestRecommendationPipeline:
    def test_universe_then_top_consistent(
        self, client, historical_provider_available
    ):
        u = client.get("/api/v1/recommendations/universe").json()["data"]
        t = client.get(
            "/api/v1/recommendations/top",
            params={"limit": 20, "min_rows": 120, "max_symbols": 50},
        ).json()
        # universe_size reported by /top never exceeds the DB total
        assert t["universe_size"] <= u["total_symbols"]
        assert t["count"] == len(t["data"])

    def test_top_and_explain_agree_on_score(
        self, client, historical_provider_available
    ):
        TOP_MIN_ROWS = 120
        top = client.get(
            "/api/v1/recommendations/top",
            params={"limit": 3, "min_rows": TOP_MIN_ROWS, "max_symbols": 40},
        ).json()
        if not top["data"]:
            pytest.skip("No recommendations in snapshot.")
        sym = top["data"][0]["symbol"]
        detail = client.get(
            f"/api/v1/recommendations/explain/{sym}",
            params={"min_rows": TOP_MIN_ROWS},
        ).json()["data"]
        # Score tolerance is ±10 points.
        #
        # Why not ±1?
        #   /top uses load_universe() → _inject_live_bars() which appends a
        #   single synthetic bar built from the live aggregator snapshot.
        #   /explain uses load_ohlcv() → _backfill_from_samirwagle() which
        #   appends one or more real bars fetched from the SamirWagle API.
        #   These two live-data injection strategies can produce different
        #   recent bars (different volume, close price, etc.), which shifts
        #   the MACD histogram, volume ratio and other time-sensitive factors.
        #   A ±10-point window confirms both endpoints score the symbol in the
        #   same neighbourhood without demanding byte-for-byte data identity.
        assert detail["symbol"] == sym
        assert abs(detail["score"] - top["data"][0]["score"]) <= 10.0


class TestConcurrentCallsAreStable:
    def test_multiple_universe_calls_are_consistent(
        self, client, historical_provider_available
    ):
        first = client.get("/api/v1/recommendations/universe").json()["data"]
        second = client.get("/api/v1/recommendations/universe").json()["data"]
        assert first["total_symbols"] == second["total_symbols"]
        assert first["total_rows"] == second["total_rows"]
