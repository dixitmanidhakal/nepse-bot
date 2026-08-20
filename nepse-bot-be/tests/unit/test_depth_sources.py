"""Unit tests for the multi-source market-depth fallback chain."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.data import depth_sources
from app.services.http import UpstreamUnavailable


SAMPLE_PAYLOAD = {
    "symbol": "NABIL",
    "timestamp": "2026-04-24T12:00:00+00:00",
    "buy_orders": [{"price": 1200.0, "quantity": 100, "orders": 3}],
    "sell_orders": [{"price": 1201.0, "quantity": 150, "orders": 5}],
    "source": "nepalstock",
}


class TestFallbackChain:
    def test_cache_hit_short_circuits(self):
        with patch.object(
            depth_sources.raw_cache,
            "load_latest",
            return_value={"payload": SAMPLE_PAYLOAD, "fetched_at_utc": "x",
                          "age_seconds": 5.0},
        ), patch.object(depth_sources, "_fetch_from_nepse_lib") as mock_nepse:
            result = depth_sources.fetch_market_depth("NABIL")
        assert result["from_cache"] is True
        assert result["cache_age_seconds"] == 5.0
        mock_nepse.assert_not_called()

    def test_primary_success(self):
        with patch.object(depth_sources.raw_cache, "load_latest", return_value=None), \
             patch.object(depth_sources.raw_cache, "save"), \
             patch.object(depth_sources, "_fetch_from_nepse_lib",
                          return_value=SAMPLE_PAYLOAD):
            result = depth_sources.fetch_market_depth("NABIL")
        assert result == SAMPLE_PAYLOAD

    def test_falls_through_to_merolagani(self):
        merolagani_payload = {**SAMPLE_PAYLOAD, "source": "merolagani"}
        with patch.object(depth_sources.raw_cache, "load_latest", return_value=None), \
             patch.object(depth_sources.raw_cache, "save"), \
             patch.object(depth_sources, "_fetch_from_nepse_lib", return_value=None), \
             patch.object(depth_sources, "_fetch_from_merolagani",
                          return_value=merolagani_payload):
            result = depth_sources.fetch_market_depth("NABIL")
        assert result["source"] == "merolagani"

    def test_all_sources_fail_raises(self):
        with patch.object(depth_sources.raw_cache, "load_latest", return_value=None), \
             patch.object(depth_sources, "_fetch_from_nepse_lib", return_value=None), \
             patch.object(depth_sources, "_fetch_from_merolagani", return_value=None):
            with pytest.raises(UpstreamUnavailable):
                depth_sources.fetch_market_depth("NABIL")

    def test_exception_in_source_is_swallowed(self):
        """An exception in one source must not short-circuit the chain."""
        merolagani_payload = {**SAMPLE_PAYLOAD, "source": "merolagani"}
        with patch.object(depth_sources.raw_cache, "load_latest", return_value=None), \
             patch.object(depth_sources.raw_cache, "save"), \
             patch.object(depth_sources, "_fetch_from_nepse_lib",
                          side_effect=RuntimeError("boom")), \
             patch.object(depth_sources, "_fetch_from_merolagani",
                          return_value=merolagani_payload):
            result = depth_sources.fetch_market_depth("NABIL")
        assert result["source"] == "merolagani"

    def test_empty_symbol_raises(self):
        with pytest.raises(ValueError):
            depth_sources.fetch_market_depth("   ")

    def test_symbol_uppercased(self):
        with patch.object(depth_sources.raw_cache, "load_latest", return_value=None), \
             patch.object(depth_sources.raw_cache, "save"), \
             patch.object(depth_sources, "_fetch_from_nepse_lib",
                          return_value=SAMPLE_PAYLOAD) as mock_fn:
            depth_sources.fetch_market_depth("nabil")
        mock_fn.assert_called_once_with("NABIL")
