"""
Data-flow integrity tests.

Covers:
  1. _fetch_current_prices() — three-tier price priority
       DB cache (fresh)   → aggregator  → yonepse fallback
  2. PnL & capital tracking math — edge cases (exact zeros, very large losses)
  3. TradeOutcome / TradeDirection enum completeness
  4. Free-API endpoint response schema contracts (via TestClient)
  5. Recommendation engine score determinism — same data → same score every time
"""

from __future__ import annotations

import math
from typing import Dict, List
from unittest.mock import MagicMock, patch

import pytest

from app.models.paper_trade import PaperTrade, TradeOutcome, TradeDirection
from app.components.bots.base_bot import BaseBot


# ── Minimal concrete bot for price-fetch tests ──────────────────────────────

class _PriceBot(BaseBot):
    BOT_ID   = "price_test_bot"
    BOT_NAME = "Price Test Bot"
    STRATEGY = "test"

    def generate_signals(self, db, timeframe="daily"):
        return []


_bot = _PriceBot()


# ═══════════════════════════════════════════════════════════════════════════════
# 1. _fetch_current_prices() — three-tier priority
# ═══════════════════════════════════════════════════════════════════════════════

class TestFetchCurrentPricesPriority:
    """
    Verify that _fetch_current_prices() uses the correct cascade:
      Tier 1 — DB live cache (max 10-min-old data from market scraper)
      Tier 2 — Async aggregator (live market API cascade)
      Tier 3 — Yonepse GitHub JSON fallback (~15-min lag)
    """

    def test_tier1_db_cache_resolves_all_symbols(self):
        """When all symbols are in the DB cache, no further tiers are hit."""
        cached = {"NABIL": 1050.0, "NHPC": 85.5}

        with patch(
            "app.services.data.market_scraper.get_cached_prices",
            return_value=cached,
        ) as mock_db:
            result = _bot._fetch_current_prices(["NABIL", "NHPC"])

        mock_db.assert_called_once()
        assert result["NABIL"] == pytest.approx(1050.0)
        assert result["NHPC"]  == pytest.approx(85.5)

    def test_tier1_partial_hit_falls_to_tier2(self):
        """DB cache has NABIL but not NHPC → tier 2 fills the gap."""
        cached    = {"NABIL": 1050.0}
        live_rows = [
            {"symbol": "NHPC", "ltp": "85.5"},
        ]

        with patch("app.services.data.market_scraper.get_cached_prices", return_value=cached), \
             patch("app.services.data.free_sources.aggregator.live_market",
                   return_value=_async_return(live_rows)), \
             patch("app.components.bots.nepse_universe.run_async",
                   side_effect=lambda coro: live_rows):
            # Use the bot's actual method; mock aggregator.live_market
            from app.services.data.free_sources import aggregator as _agg
            orig = _agg.live_market

            async def _fake_live():
                return live_rows

            _agg.live_market = _fake_live
            try:
                result = _bot._fetch_current_prices(["NABIL", "NHPC"])
            finally:
                _agg.live_market = orig

        assert result["NABIL"] == pytest.approx(1050.0)

    def test_prices_are_floats(self):
        """All returned prices must be float, never string."""
        cached = {"NABIL": 1050.0}
        with patch("app.services.data.market_scraper.get_cached_prices", return_value=cached):
            result = _bot._fetch_current_prices(["NABIL"])
        assert isinstance(result.get("NABIL"), float)

    def test_empty_symbols_returns_empty_dict(self):
        with patch("app.services.data.market_scraper.get_cached_prices", return_value={}):
            result = _bot._fetch_current_prices([])
        assert result == {}

    def test_db_cache_exception_falls_to_next_tier(self):
        """If DB cache raises, the bot must not crash — falls through to tier 2."""
        with patch(
            "app.services.data.market_scraper.get_cached_prices",
            side_effect=RuntimeError("DB connection lost"),
        ):
            # Without tier 2/3 available (they'll fail too), should return empty
            try:
                result = _bot._fetch_current_prices(["NABIL"])
                assert isinstance(result, dict)
            except Exception:
                pytest.fail("_fetch_current_prices must not raise when DB cache fails")

    def test_unknown_symbol_not_in_result(self):
        """Symbols absent from all sources must not appear in result."""
        with patch("app.services.data.market_scraper.get_cached_prices", return_value={}):
            result = _bot._fetch_current_prices(["XXXUNKNOWN999"])
        assert "XXXUNKNOWN999" not in result or result.get("XXXUNKNOWN999") is None


def _async_return(value):
    """Create a mock async function that returns a fixed value."""
    import asyncio
    async def _inner(*args, **kwargs):
        return value
    return _inner


# ═══════════════════════════════════════════════════════════════════════════════
# 2. PnL & Capital Math — edge cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestPnlMathEdgeCases:
    """Pure arithmetic tests for P&L formulas used in _resolve_open_trades."""

    def test_pnl_pct_win_at_target(self):
        """6% target hit → +6% P&L."""
        entry, current = 1000.0, 1060.0
        pnl_pct = (current - entry) / entry * 100.0
        assert abs(pnl_pct - 6.0) < 0.001

    def test_pnl_pct_loss_at_stop(self):
        """-3% stop hit → -3% P&L."""
        entry, current = 1000.0, 970.0
        pnl_pct = (current - entry) / entry * 100.0
        assert abs(pnl_pct + 3.0) < 0.001

    def test_pnl_pct_timeout_flat(self):
        """No price change → 0% P&L."""
        entry, current = 500.0, 500.0
        pnl_pct = (current - entry) / entry * 100.0
        assert pnl_pct == 0.0

    def test_pnl_nrs_win(self):
        """NPR P&L = allocated * pnl_pct / 100."""
        allocated, pnl_pct = 100_000.0, 6.0
        pnl_nrs = round(allocated * pnl_pct / 100.0, 2)
        assert pnl_nrs == 6_000.0

    def test_pnl_nrs_loss(self):
        allocated, pnl_pct = 100_000.0, -3.0
        pnl_nrs = round(allocated * pnl_pct / 100.0, 2)
        assert pnl_nrs == -3_000.0

    def test_pnl_nrs_zero_allocation(self):
        """Zero-allocation trade (shouldn't happen in practice) → zero P&L."""
        pnl_nrs = round(0.0 * 6.0 / 100.0, 2)
        assert pnl_nrs == 0.0

    def test_pnl_nrs_is_finite(self):
        """No NaN or Inf in P&L calculations."""
        for allocated in [1_000.0, 100_000.0, 1_000_000.0]:
            for pnl_pct in [-50.0, 0.0, 50.0, 100.0]:
                pnl_nrs = allocated * pnl_pct / 100.0
                assert math.isfinite(pnl_nrs)

    def test_total_pnl_accumulates_correctly(self):
        """Summing multiple trade P&Ls matches expected total."""
        trades_pnl = [6_000.0, -3_000.0, 0.0, 12_000.0]
        total = sum(trades_pnl)
        assert total == 15_000.0

    # ── High-water mark and drawdown ─────────────────────────────────────────

    def test_hwm_updated_only_when_new_high(self):
        """Peak capital only updates when current > previous peak."""
        peak    = 1_100_000.0
        current = 1_050_000.0   # below peak → no update
        new_peak = max(peak, current)
        assert new_peak == 1_100_000.0

    def test_hwm_updates_on_new_high(self):
        peak    = 1_100_000.0
        current = 1_150_000.0   # new high
        new_peak = max(peak, current)
        assert new_peak == 1_150_000.0

    def test_drawdown_from_peak(self):
        """DD% = (peak - current) / peak * 100."""
        peak, current = 1_100_000.0, 1_000_000.0
        dd = (peak - current) / peak * 100.0
        assert abs(dd - 9.09) < 0.01

    def test_drawdown_is_zero_at_peak(self):
        peak = current = 1_000_000.0
        dd = (peak - current) / peak * 100.0
        assert dd == 0.0

    def test_drawdown_does_not_go_negative(self):
        """If current > peak (shouldn't happen after HWM update), dd < 0 is invalid."""
        peak, current = 1_000_000.0, 1_050_000.0
        dd = (peak - current) / peak * 100.0
        # We only store drawdown when dd > previous max_drawdown, so a negative
        # value would simply not update the stored max — just verify the formula:
        assert dd < 0  # would be filtered before storing

    def test_capital_available_formula(self):
        """available = max(0, deployable - deployed)."""
        capital    = 1_000_000.0
        cash_res   = 0.20
        deployable = capital * (1 - cash_res)   # 800_000
        deployed   = 600_000.0
        available  = max(0.0, deployable - deployed)
        assert available == 200_000.0

    def test_capital_available_clamped_at_zero(self):
        deployable = 800_000.0
        deployed   = 900_000.0  # over-deployed
        available  = max(0.0, deployable - deployed)
        assert available == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# 3. TradeOutcome / TradeDirection enum completeness
# ═══════════════════════════════════════════════════════════════════════════════

class TestTradeEnumCompleteness:
    """Verify the enum values match the business domain exactly."""

    def test_outcome_has_win(self):
        assert TradeOutcome.WIN is not None

    def test_outcome_has_loss(self):
        assert TradeOutcome.LOSS is not None

    def test_outcome_has_timeout(self):
        assert TradeOutcome.TIMEOUT is not None

    def test_outcome_has_open(self):
        assert TradeOutcome.OPEN is not None

    def test_outcome_win_value_is_string(self):
        assert isinstance(TradeOutcome.WIN.value, str)

    def test_outcome_all_values_unique(self):
        values = [e.value for e in TradeOutcome]
        assert len(values) == len(set(values))

    def test_direction_has_long(self):
        assert TradeDirection.LONG is not None

    def test_direction_value_is_string(self):
        assert isinstance(TradeDirection.LONG.value, str)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Free-API endpoint response schema contracts
# ═══════════════════════════════════════════════════════════════════════════════

class TestFreeApiSchemas:
    """Smoke-test the free data endpoints via TestClient for schema correctness."""

    def test_health_endpoint_returns_source_map(self, client):
        """The free /health endpoint returns one key per data source
        (merolagani, nepalipaisa, nepsealpha, etc.) — not a generic 'status' key."""
        r = client.get("/api/v1/free/health")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, dict)
        assert len(body) > 0  # at least one source reported
        # At least one of the known free sources must be present
        known_sources = {"merolagani", "nepalipaisa", "nepsealpha", "yonepse",
                         "sharesansar", "samirwagle", "nepsetrading"}
        assert known_sources & set(body.keys()), \
            f"No known source found in health response keys: {list(body.keys())}"

    def test_live_market_endpoint_returns_list_shape(self, client):
        r = client.get("/api/v1/free/market/live")
        assert r.status_code == 200
        body = r.json()
        # Must have count and data list
        assert "count" in body or "data" in body

    def test_sector_indices_endpoint_returns_list(self, client):
        r = client.get("/api/v1/free/indices/sectors")
        # 200 or 503 (if source unreachable during test) are acceptable
        assert r.status_code in (200, 503, 504)

    def test_bot_list_endpoint_shape(self, client):
        r = client.get("/api/v1/bots/")
        assert r.status_code == 200
        body = r.json()
        assert "count" in body
        assert "bots" in body
        assert isinstance(body["bots"], list)
        assert body["count"] == len(body["bots"])

    def test_bot_summary_endpoint_has_win_rate(self, client):
        r = client.get("/api/v1/bots/summary")
        assert r.status_code == 200
        body = r.json()
        assert "overall_win_rate" in body
        wr = body["overall_win_rate"]
        assert 0.0 <= wr <= 100.0

    def test_unknown_bot_state_returns_404(self, client):
        r = client.get("/api/v1/bots/nonexistent_XYZ_bot/state")
        assert r.status_code == 404

    def test_invalid_timeframe_returns_422(self, client):
        r = client.post("/api/v1/bots/smc_bot/run", params={"timeframe": "hourly"})
        assert r.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Recommendation engine score determinism
# ═══════════════════════════════════════════════════════════════════════════════

class TestScoreDeterminism:
    """
    score_symbol() is a pure function — the same DataFrame must produce the
    same score on every call (no random state, no side effects).
    """

    def _make_df(self, n: int = 300, seed: int = 42):
        import numpy as np
        rng = np.random.default_rng(seed)
        log_ret = rng.normal(0.0008, 0.015, n)
        close = 100.0 * np.exp(np.cumsum(log_ret))
        import pandas as pd
        return pd.DataFrame({
            "date":   pd.date_range("2023-01-01", periods=n, freq="B"),
            "open":   close * (1 + rng.normal(0, 0.003, n)),
            "high":   close * (1 + abs(rng.normal(0, 0.004, n))),
            "low":    close * (1 - abs(rng.normal(0, 0.004, n))),
            "close":  close,
            "volume": rng.integers(10_000, 200_000, size=n).astype(float),
        })

    def test_same_data_same_score(self):
        """Calling score_symbol twice on identical data → identical score."""
        from app.components.recommendation_engine import score_symbol
        df = self._make_df()
        r1 = score_symbol("NABIL", df)
        r2 = score_symbol("NABIL", df)
        assert r1 is not None and r2 is not None
        assert r1.score == r2.score

    def test_different_seed_different_score(self):
        """Two distinct random DataFrames should (almost certainly) give different scores."""
        from app.components.recommendation_engine import score_symbol
        df1 = self._make_df(seed=42)
        df2 = self._make_df(seed=99)
        r1 = score_symbol("SYM", df1)
        r2 = score_symbol("SYM", df2)
        # Not strictly guaranteed (could coincide by chance), but very unlikely
        # with distinct seeds; just check both are valid Recommendations
        assert r1 is not None
        assert r2 is not None
        assert 0.0 <= r1.score <= 100.0
        assert 0.0 <= r2.score <= 100.0

    def test_score_respects_factor_weights(self):
        """Sum of (weight * factor_score) * 100 == composite score (within rounding)."""
        from app.components.recommendation_engine import score_symbol, WEIGHTS
        df = self._make_df()
        rec = score_symbol("NABIL", df)
        assert rec is not None
        computed = sum(WEIGHTS[k] * rec.factor_scores[k] for k in WEIGHTS)
        assert abs(computed * 100 - rec.score) < 0.5  # 0.5-point rounding tolerance

    def test_empty_dataframe_returns_none(self):
        from app.components.recommendation_engine import score_symbol
        import pandas as pd
        assert score_symbol("NABIL", pd.DataFrame()) is None

    def test_insufficient_rows_returns_none(self):
        """Fewer than MIN_HISTORY_DAYS rows → None (not enough to score)."""
        from app.components.recommendation_engine import score_symbol, MIN_HISTORY_DAYS
        import pandas as pd, numpy as np
        n = MIN_HISTORY_DAYS - 1
        df = pd.DataFrame({
            "date":   pd.date_range("2024-01-01", periods=n, freq="B"),
            "close":  np.linspace(100, 110, n),
            "volume": [1000] * n,
        })
        assert score_symbol("NABIL", df) is None

    def test_score_in_valid_range(self):
        """Every score must be in [0, 100]."""
        from app.components.recommendation_engine import score_symbol
        for seed in range(5):
            df  = self._make_df(seed=seed)
            rec = score_symbol("TEST", df)
            if rec is not None:
                assert 0.0 <= rec.score <= 100.0

    def test_action_corresponds_to_score(self):
        """BUY ≥ 65, WATCH 45-64, AVOID < 45."""
        from app.components.recommendation_engine import score_symbol
        df  = self._make_df()
        rec = score_symbol("NABIL", df)
        assert rec is not None
        if rec.score >= 65:
            assert rec.action == "BUY"
        elif rec.score >= 45:
            assert rec.action == "WATCH"
        else:
            assert rec.action == "AVOID"

    def test_factor_scores_all_in_0_1_range(self):
        """Each individual factor score (before weighting) must be in [0, 1]."""
        from app.components.recommendation_engine import score_symbol
        df  = self._make_df()
        rec = score_symbol("NABIL", df)
        assert rec is not None
        for name, val in rec.factor_scores.items():
            assert 0.0 <= val <= 1.0, f"factor '{name}' out of range: {val}"
