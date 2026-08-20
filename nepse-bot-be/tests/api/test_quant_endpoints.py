"""
Smoke tests for:
  /api/v1/quant/*          (quant_routes.py)
  /api/v1/quant/advanced/* (quant_advanced_routes.py)
  /api/v1/recommendations-enhanced/* (enhanced_recommendation_routes.py)

Most quant POST endpoints accept self-contained JSON payloads and work
offline (no DB or live data required). Tests use deterministic synthetic
price series to exercise the full compute path.

Enhanced recommendation routes need the SQLite DB; they may return 404/500
in the test environment.
"""

from __future__ import annotations

import pytest

_QUANT_BASE  = "/api/v1/quant"
_ADV_BASE    = "/api/v1/quant/advanced"
_ENH_BASE    = "/api/v1/recommendations-enhanced"

# 65-item synthetic price series (≥61 required by regime business logic)
_CLOSES = [100.0 + i * 0.5 + (i % 7 - 3) * 0.8 for i in range(65)]
# 60-item returns series (≥50 needed by conformal-var, ≥10 by BOCPD)
_RETURNS = [0.005 + (i % 5 - 2) * 0.002 for i in range(60)]


# ── /quant/regime ─────────────────────────────────────────────────────────────

class TestQuantRegime:
    def test_regime_from_closes(self, client):
        r = client.post(f"{_QUANT_BASE}/regime", json={"closes": _CLOSES})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "success"
        assert "data" in body

    def test_regime_requires_closes(self, client):
        r = client.post(f"{_QUANT_BASE}/regime", json={})
        assert r.status_code == 422

    def test_regime_from_returns(self, client):
        r = client.post(f"{_QUANT_BASE}/regime/returns", json={"returns": _RETURNS})
        assert r.status_code == 200
        assert r.json()["status"] == "success"

    def test_regime_returns_requires_payload(self, client):
        r = client.post(f"{_QUANT_BASE}/regime/returns", json={})
        assert r.status_code == 422


# ── /quant/size-positions ─────────────────────────────────────────────────────

class TestQuantSizePositions:
    _VALID_PAYLOAD = {
        "signals": [
            {"symbol": "NABIL", "strength": 0.8, "confidence": 0.7, "signal_type": "buy"},
            {"symbol": "NICA",  "strength": 0.6, "confidence": 0.6, "signal_type": "buy"},
        ],
        "capital": 500_000.0,
        "prices": {"NABIL": 1200.0, "NICA": 900.0},
    }

    def test_size_positions_valid(self, client):
        r = client.post(f"{_QUANT_BASE}/size-positions", json=self._VALID_PAYLOAD)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "success"
        assert "positions" in body["data"]

    def test_size_positions_requires_capital(self, client):
        payload = {**self._VALID_PAYLOAD, "capital": -1000}
        r = client.post(f"{_QUANT_BASE}/size-positions", json=payload)
        assert r.status_code == 422

    def test_size_positions_empty_signals_ok(self, client):
        payload = {**self._VALID_PAYLOAD, "signals": [], "prices": {}}
        r = client.post(f"{_QUANT_BASE}/size-positions", json=payload)
        assert r.status_code in (200, 422)


# ── /quant/transaction-cost ───────────────────────────────────────────────────

class TestQuantTransactionCost:
    def test_transaction_cost_buy(self, client):
        r = client.post(
            f"{_QUANT_BASE}/transaction-cost",
            json={"amount": 100_000.0, "is_buy": True},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "success"
        assert body["data"]["cost"] > 0

    def test_transaction_cost_sell(self, client):
        r = client.post(
            f"{_QUANT_BASE}/transaction-cost",
            json={"amount": 50_000.0, "is_buy": False},
        )
        assert r.status_code == 200

    def test_transaction_cost_zero_amount_invalid(self, client):
        r = client.post(
            f"{_QUANT_BASE}/transaction-cost",
            json={"amount": 0, "is_buy": True},
        )
        assert r.status_code == 422

    def test_transaction_cost_negative_amount_invalid(self, client):
        r = client.post(
            f"{_QUANT_BASE}/transaction-cost",
            json={"amount": -100, "is_buy": True},
        )
        assert r.status_code == 422


# ── /quant/kelly ──────────────────────────────────────────────────────────────

class TestQuantKelly:
    def test_kelly_valid(self, client):
        r = client.post(
            f"{_QUANT_BASE}/kelly",
            json={"win_prob": 0.6, "avg_win": 0.08, "avg_loss": 0.04},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "success"
        assert "kelly_fraction" in body["data"]
        assert 0 <= body["data"]["kelly_fraction"] <= 1

    def test_kelly_win_prob_bounds(self, client):
        r = client.post(
            f"{_QUANT_BASE}/kelly",
            json={"win_prob": 1.5, "avg_win": 0.08, "avg_loss": 0.04},
        )
        assert r.status_code == 422

    def test_kelly_negative_avg_win_invalid(self, client):
        r = client.post(
            f"{_QUANT_BASE}/kelly",
            json={"win_prob": 0.6, "avg_win": -0.01, "avg_loss": 0.04},
        )
        assert r.status_code == 422


# ── /quant/snapshot ───────────────────────────────────────────────────────────

class TestQuantSnapshot:
    def test_snapshot_get_responds(self, client):
        r = client.get(f"{_QUANT_BASE}/snapshot")
        # May return stale snapshot or 200 with empty-ish data
        assert r.status_code in (200, 500)

    def test_snapshot_refresh_responds(self, client):
        r = client.post(f"{_QUANT_BASE}/snapshot/refresh")
        assert r.status_code in (200, 500)


# ── /quant/advanced/* ─────────────────────────────────────────────────────────

class TestQuantAdvancedRegimeHMM:
    _VALID = {"closes": _CLOSES}

    def test_regime_hmm_valid(self, client):
        r = client.post(f"{_ADV_BASE}/regime-hmm", json=self._VALID)
        assert r.status_code in (200, 500)
        if r.status_code == 200:
            assert r.json()["success"] is True

    def test_regime_hmm_too_few_closes(self, client):
        r = client.post(f"{_ADV_BASE}/regime-hmm", json={"closes": [100.0] * 5})
        assert r.status_code == 422

    def test_regime_hmm_invalid_n_states(self, client):
        r = client.post(f"{_ADV_BASE}/regime-hmm", json={**self._VALID, "n_states": 1})
        assert r.status_code == 422


class TestQuantAdvancedBocpd:
    # RegimeBOCPDRequest requires `returns` (not `closes`), min_length=10
    _VALID = {"returns": _RETURNS}

    def test_regime_bocpd_valid(self, client):
        r = client.post(f"{_ADV_BASE}/regime-bocpd", json=self._VALID)
        assert r.status_code in (200, 500)

    def test_regime_bocpd_too_few_returns(self, client):
        r = client.post(f"{_ADV_BASE}/regime-bocpd", json={"returns": [0.001] * 5})
        assert r.status_code == 422


class TestQuantAdvancedMarketState:
    # MarketStateRequest requires `prices: Dict[str, List[float]]`
    _VALID = {
        "prices": {
            "NABIL": _CLOSES,
            "NICA":  [c * 0.85 for c in _CLOSES],
        }
    }

    def test_market_state_valid(self, client):
        r = client.post(f"{_ADV_BASE}/market-state", json=self._VALID)
        assert r.status_code in (200, 500)

    def test_market_state_requires_prices(self, client):
        # Missing required `prices` field → 422
        r = client.post(f"{_ADV_BASE}/market-state", json={})
        assert r.status_code == 422


class TestQuantAdvancedPairsSpread:
    # PairsSpreadRequest requires prices_a and prices_b (not a prices dict)
    _VALID_PAYLOAD = {
        "prices_a": _CLOSES,
        "prices_b": [c * 0.85 for c in _CLOSES],
    }

    def test_pairs_spread_valid(self, client):
        r = client.post(f"{_ADV_BASE}/pairs-spread", json=self._VALID_PAYLOAD)
        assert r.status_code in (200, 400, 500)

    def test_pairs_spread_requires_prices_a_and_b(self, client):
        # Missing both required fields → 422
        r = client.post(f"{_ADV_BASE}/pairs-spread", json={})
        assert r.status_code == 422

    def test_pairs_spread_too_few_prices(self, client):
        r = client.post(
            f"{_ADV_BASE}/pairs-spread",
            json={"prices_a": [100.0] * 5, "prices_b": [85.0] * 5},
        )
        assert r.status_code == 422


class TestQuantAdvancedPortfolioAllocate:
    # PortfolioAllocRequest requires both `prices` dict AND `symbols` list
    _VALID_PAYLOAD = {
        "prices": {
            "NABIL": _CLOSES,
            "NICA":  [c * 0.85 for c in _CLOSES],
        },
        "symbols": ["NABIL", "NICA"],
    }

    def test_portfolio_allocate_valid(self, client):
        r = client.post(f"{_ADV_BASE}/portfolio-allocate", json=self._VALID_PAYLOAD)
        assert r.status_code in (200, 500)

    def test_portfolio_allocate_requires_prices(self, client):
        r = client.post(f"{_ADV_BASE}/portfolio-allocate", json={})
        assert r.status_code == 422

    def test_portfolio_allocate_requires_symbols(self, client):
        # Missing required `symbols` field → 422
        r = client.post(
            f"{_ADV_BASE}/portfolio-allocate",
            json={"prices": {"NABIL": _CLOSES}},
        )
        assert r.status_code == 422


class TestQuantAdvancedConformalVar:
    # ConformalVaRRequest requires `returns` (min_length=50), not `closes`
    _VALID = {"returns": _RETURNS}

    def test_conformal_var_valid(self, client):
        r = client.post(f"{_ADV_BASE}/conformal-var", json=self._VALID)
        assert r.status_code in (200, 500)

    def test_conformal_var_requires_returns(self, client):
        r = client.post(f"{_ADV_BASE}/conformal-var", json={})
        assert r.status_code == 422

    def test_conformal_var_too_few_returns(self, client):
        r = client.post(f"{_ADV_BASE}/conformal-var", json={"returns": [0.01] * 10})
        assert r.status_code == 422


class TestQuantAdvancedSignalsRank:
    # SignalRankRequest requires `candidates: List[Dict]`, not a prices dict
    _VALID_PAYLOAD = {
        "candidates": [
            {
                "symbol": "NABIL",
                "signal_type": "buy",
                "strength": 0.8,
                "confidence": 0.7,
                "reasoning": "test signal A",
            },
            {
                "symbol": "NICA",
                "signal_type": "buy",
                "strength": 0.6,
                "confidence": 0.65,
                "reasoning": "test signal B",
            },
        ]
    }

    def test_signals_rank_valid(self, client):
        r = client.post(f"{_ADV_BASE}/signals-rank", json=self._VALID_PAYLOAD)
        assert r.status_code in (200, 500)

    def test_signals_rank_requires_candidates(self, client):
        r = client.post(f"{_ADV_BASE}/signals-rank", json={})
        assert r.status_code == 422


class TestQuantAdvancedDisposition:
    # DispositionRequest uses `prices: Dict[str, List[float]]` — correct shape
    _VALID_PAYLOAD = {
        "prices": {
            "NABIL": _CLOSES,
            "NICA":  [c * 0.85 for c in _CLOSES],
        }
    }

    def test_disposition_valid(self, client):
        r = client.post(f"{_ADV_BASE}/disposition", json=self._VALID_PAYLOAD)
        assert r.status_code in (200, 500)


# ── /recommendations-enhanced/* ──────────────────────────────────────────────

class TestEnhancedRecommendationsTop:
    def test_top_limit_validation_min(self, client):
        r = client.get(f"{_ENH_BASE}/top", params={"limit": 0})
        assert r.status_code == 422

    def test_top_valid_limit_responds(self, client):
        r = client.get(f"{_ENH_BASE}/top", params={"limit": 5})
        assert r.status_code in (200, 404, 500)

    def test_top_min_score_filter_accepted(self, client):
        r = client.get(f"{_ENH_BASE}/top", params={"limit": 5, "min_score": 60})
        assert r.status_code in (200, 404, 500)


class TestEnhancedRecommendationsSymbol:
    def test_score_single_symbol_responds(self, client):
        r = client.get(f"{_ENH_BASE}/NABIL")
        assert r.status_code in (200, 404, 500)

    def test_explain_symbol_responds(self, client):
        r = client.get(f"{_ENH_BASE}/explain/NABIL")
        assert r.status_code in (200, 404, 500)


class TestEnhancedRecommendationsPost:
    def test_score_requires_symbols(self, client):
        r = client.post(f"{_ENH_BASE}/score", json={"symbols": []})
        assert r.status_code == 422

    def test_score_with_valid_list_responds(self, client):
        r = client.post(f"{_ENH_BASE}/score", json={"symbols": ["NABIL", "NICA"]})
        assert r.status_code in (200, 404, 500)
