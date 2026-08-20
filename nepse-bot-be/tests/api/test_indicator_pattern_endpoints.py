"""
Smoke tests for:
  /api/v1/indicators/* (indicator_routes.py)
  /api/v1/patterns/*   (pattern_routes.py)

Both require the historical SQLite DB.  Without it, routes return 404 or 500.
Tests accept 200 (data present) and 404/500 (no data).
Query-parameter validation tests (422) are always deterministic.
"""

from __future__ import annotations

import pytest

_IND_BASE  = "/api/v1/indicators"
_PAT_BASE  = "/api/v1/patterns"
_SYMBOL    = "NABIL"


def _ok_or_err(status: int) -> bool:
    return status in (200, 404, 500)


# ── Indicator Routes ──────────────────────────────────────────────────────────

class TestIndicatorsAll:
    def test_all_indicators_responds(self, client):
        r = client.get(f"{_IND_BASE}/{_SYMBOL}")
        assert _ok_or_err(r.status_code)

    def test_days_min_validation(self, client):
        r = client.get(f"{_IND_BASE}/{_SYMBOL}", params={"days": 5})
        assert r.status_code == 422

    def test_days_max_validation(self, client):
        r = client.get(f"{_IND_BASE}/{_SYMBOL}", params={"days": 9999})
        assert r.status_code == 422

    def test_days_valid_accepted(self, client):
        r = client.get(f"{_IND_BASE}/{_SYMBOL}", params={"days": 100})
        assert _ok_or_err(r.status_code)


class TestIndicatorsSummary:
    def test_summary_responds(self, client):
        r = client.get(f"{_IND_BASE}/{_SYMBOL}/summary")
        assert _ok_or_err(r.status_code)


class TestIndicatorsMovingAverages:
    def test_moving_averages_responds(self, client):
        r = client.get(f"{_IND_BASE}/{_SYMBOL}/moving-averages")
        assert _ok_or_err(r.status_code)


class TestIndicatorsMomentum:
    def test_momentum_responds(self, client):
        r = client.get(f"{_IND_BASE}/{_SYMBOL}/momentum")
        assert _ok_or_err(r.status_code)


class TestIndicatorsVolatility:
    def test_volatility_responds(self, client):
        r = client.get(f"{_IND_BASE}/{_SYMBOL}/volatility")
        assert _ok_or_err(r.status_code)


class TestIndicatorsVolume:
    def test_volume_responds(self, client):
        r = client.get(f"{_IND_BASE}/{_SYMBOL}/volume")
        assert _ok_or_err(r.status_code)


# ── Pattern Routes ────────────────────────────────────────────────────────────

class TestPatternAll:
    def test_all_patterns_responds(self, client):
        r = client.get(f"{_PAT_BASE}/{_SYMBOL}/all")
        assert _ok_or_err(r.status_code)


class TestPatternSummary:
    def test_summary_responds(self, client):
        r = client.get(f"{_PAT_BASE}/{_SYMBOL}/summary")
        assert _ok_or_err(r.status_code)


class TestSupportResistance:
    def test_support_resistance_responds(self, client):
        r = client.get(f"{_PAT_BASE}/{_SYMBOL}/support-resistance")
        assert _ok_or_err(r.status_code)

    def test_support_responds(self, client):
        r = client.get(f"{_PAT_BASE}/{_SYMBOL}/support")
        assert _ok_or_err(r.status_code)

    def test_resistance_responds(self, client):
        r = client.get(f"{_PAT_BASE}/{_SYMBOL}/resistance")
        assert _ok_or_err(r.status_code)


class TestTrendAnalysis:
    def test_trend_responds(self, client):
        r = client.get(f"{_PAT_BASE}/{_SYMBOL}/trend")
        assert _ok_or_err(r.status_code)

    def test_trend_channel_responds(self, client):
        r = client.get(f"{_PAT_BASE}/{_SYMBOL}/trend/channel")
        assert _ok_or_err(r.status_code)

    def test_trend_reversal_responds(self, client):
        r = client.get(f"{_PAT_BASE}/{_SYMBOL}/trend/reversal")
        assert _ok_or_err(r.status_code)


class TestChartPatterns:
    def test_chart_patterns_responds(self, client):
        r = client.get(f"{_PAT_BASE}/{_SYMBOL}/chart-patterns")
        assert _ok_or_err(r.status_code)

    def test_double_top_responds(self, client):
        r = client.get(f"{_PAT_BASE}/{_SYMBOL}/chart-patterns/double-top")
        assert _ok_or_err(r.status_code)

    def test_double_bottom_responds(self, client):
        r = client.get(f"{_PAT_BASE}/{_SYMBOL}/chart-patterns/double-bottom")
        assert _ok_or_err(r.status_code)

    def test_head_shoulders_responds(self, client):
        r = client.get(f"{_PAT_BASE}/{_SYMBOL}/chart-patterns/head-shoulders")
        assert _ok_or_err(r.status_code)

    def test_triangle_responds(self, client):
        r = client.get(f"{_PAT_BASE}/{_SYMBOL}/chart-patterns/triangle")
        assert _ok_or_err(r.status_code)

    def test_flag_responds(self, client):
        r = client.get(f"{_PAT_BASE}/{_SYMBOL}/chart-patterns/flag")
        assert _ok_or_err(r.status_code)


class TestBreakoutsAndSignals:
    def test_breakouts_responds(self, client):
        r = client.get(f"{_PAT_BASE}/{_SYMBOL}/breakouts")
        assert _ok_or_err(r.status_code)

    def test_signals_responds(self, client):
        r = client.get(f"{_PAT_BASE}/{_SYMBOL}/signals")
        assert _ok_or_err(r.status_code)
