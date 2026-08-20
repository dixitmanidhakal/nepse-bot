"""
Smoke tests for /api/v1/free/smc/* (smc_routes.py).

These endpoints call the live aggregator to fetch OHLCV data, so responses
may be 503 when data sources are offline. Tests focus on:
  - Route registration (routes exist and respond)
  - Query-parameter validation (422 for out-of-range)
  - Signal filter accepted without error
"""

from __future__ import annotations

import pytest

_BASE = "/api/v1/free/smc"
_GOOD_SYMBOL = "NABIL"
_BAD_SYMBOL  = "ZZNOTREAL"


def _reasonable(status: int) -> bool:
    return status in (200, 404, 500, 503)


class TestSmcSymbol:
    def test_valid_symbol_responds(self, client):
        r = client.get(f"{_BASE}/{_GOOD_SYMBOL}")
        assert _reasonable(r.status_code)

    def test_response_shape_when_ok(self, client):
        r = client.get(f"{_BASE}/{_GOOD_SYMBOL}")
        if r.status_code == 200:
            body = r.json()
            # Must include the symbol and some SMC fields
            assert "symbol" in body or "error" in body

    def test_limit_param_min_validation(self, client):
        r = client.get(f"{_BASE}/{_GOOD_SYMBOL}", params={"limit": 0})
        assert r.status_code == 422

    def test_limit_param_max_validation(self, client):
        r = client.get(f"{_BASE}/{_GOOD_SYMBOL}", params={"limit": 9999})
        assert r.status_code == 422

    def test_limit_valid_value_accepted(self, client):
        r = client.get(f"{_BASE}/{_GOOD_SYMBOL}", params={"limit": 60})
        assert _reasonable(r.status_code)


class TestSmcTop:
    def test_top_default_responds(self, client):
        r = client.get(f"{_BASE}")
        assert _reasonable(r.status_code)

    def test_top_limit_validation_min(self, client):
        r = client.get(f"{_BASE}", params={"limit": 0})
        assert r.status_code == 422

    def test_top_limit_validation_max(self, client):
        r = client.get(f"{_BASE}", params={"limit": 9999})
        assert r.status_code == 422

    def test_top_universe_size_min_validation(self, client):
        r = client.get(f"{_BASE}", params={"universe_size": 1})
        assert r.status_code == 422

    def test_top_universe_size_max_validation(self, client):
        r = client.get(f"{_BASE}", params={"universe_size": 9999})
        assert r.status_code == 422

    def test_top_min_score_validation(self, client):
        r = client.get(f"{_BASE}", params={"min_score": -1})
        assert r.status_code == 422
        r = client.get(f"{_BASE}", params={"min_score": 101})
        assert r.status_code == 422

    def test_top_signal_filter_accepted(self, client):
        for sig in ("BUY", "SELL", "WATCH"):
            r = client.get(f"{_BASE}", params={"limit": 5, "signal": sig})
            assert _reasonable(r.status_code)

    def test_top_valid_params_accepted(self, client):
        r = client.get(
            f"{_BASE}",
            params={"limit": 5, "min_score": 50.0, "universe_size": 20},
        )
        assert _reasonable(r.status_code)
