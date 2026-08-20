"""
Smoke tests for app/services/data/market_scraper.py

Tests the pure-logic helpers (_parse_float, _first, _normalise_row)
without touching the database or network.
"""

import pytest

from app.services.data.market_scraper import (
    _parse_float,
    _first,
    _normalise_row,
)


# ── _parse_float ──────────────────────────────────────────────────────────────

class TestParseFloat:
    def test_none_returns_none(self):
        assert _parse_float(None) is None

    def test_integer_string(self):
        assert _parse_float("1234") == 1234.0

    def test_float_string(self):
        assert abs(_parse_float("3.14") - 3.14) < 1e-9

    def test_zero_is_valid(self):
        assert _parse_float(0) == 0.0
        assert _parse_float("0") == 0.0
        assert _parse_float(0.0) == 0.0

    def test_nan_string_returns_none(self):
        # float("nan") → NaN; we guard against that
        result = _parse_float(float("nan"))
        assert result is None

    def test_non_numeric_string_returns_none(self):
        assert _parse_float("abc") is None

    def test_empty_string_returns_none(self):
        assert _parse_float("") is None

    def test_negative_value(self):
        assert _parse_float(-5.5) == -5.5


# ── _first ────────────────────────────────────────────────────────────────────

class TestFirst:
    def test_returns_first_present_key(self):
        row = {"a": 10, "b": 20}
        assert _first("a", "b", row=row) == 10

    def test_skips_none_but_not_zero(self):
        """This is the critical fix: 0.0 must NOT be skipped."""
        row = {"a": None, "b": 0.0, "c": 5.0}
        assert _first("a", "b", "c", row=row) == 0.0

    def test_skips_missing_keys(self):
        row = {"x": None, "y": 42}
        assert _first("a", "x", "y", row=row) == 42

    def test_all_none_returns_none(self):
        row = {"a": None, "b": None}
        assert _first("a", "b", row=row) is None

    def test_empty_row_returns_none(self):
        assert _first("a", "b", row={}) is None

    def test_integer_zero_not_skipped(self):
        row = {"volume": 0}
        assert _first("volume", row=row) == 0


# ── _normalise_row ────────────────────────────────────────────────────────────

class TestNormaliseRow:
    def _merolagani_row(self):
        """Typical merolagani scraper output dict."""
        return {
            "symbol": "NABIL",
            "ltp": 985.0,
            "open": 980.0,
            "high": 990.0,
            "low": 975.0,
            "previous_close": 978.0,
            "percent_change": 0.0,   # zero — must be preserved, not dropped
            "volume": 0,             # zero — must be preserved
            "turnover": 960350.0,
        }

    def test_happy_path_returns_canonical_keys(self):
        row = self._merolagani_row()
        result = _normalise_row(row)
        assert result is not None
        assert result["symbol"] == "NABIL"
        assert result["ltp"] == 985.0
        assert result["open_price"] == 980.0
        assert result["high_price"] == 990.0
        assert result["low_price"] == 975.0
        assert result["previous_close"] == 978.0

    def test_zero_percent_change_preserved(self):
        """Regression: Python `or` would silently drop 0.0 → we use _first."""
        row = self._merolagani_row()
        result = _normalise_row(row)
        assert result is not None
        assert result["percent_change"] == 0.0

    def test_zero_volume_preserved(self):
        """Same regression for volume."""
        row = self._merolagani_row()
        result = _normalise_row(row)
        assert result is not None
        assert result["volume"] == 0.0

    def test_missing_symbol_returns_none(self):
        row = {"ltp": 100.0}
        assert _normalise_row(row) is None

    def test_empty_symbol_returns_none(self):
        row = {"symbol": "", "ltp": 100.0}
        assert _normalise_row(row) is None

    def test_missing_ltp_returns_none(self):
        row = {"symbol": "EBL"}
        assert _normalise_row(row) is None

    def test_symbol_uppercased(self):
        row = {"symbol": "nabil", "ltp": 500.0}
        result = _normalise_row(row)
        assert result is not None
        assert result["symbol"] == "NABIL"

    def test_alias_fields_nepsealpha(self):
        """nepsealpha uses camelCase aliases."""
        row = {
            "symbol": "EBL",
            "ltp": 600.0,
            "openPrice": 598.0,
            "highPrice": 610.0,
            "lowPrice": 595.0,
            "previousClose": 599.0,
            "percentChange": 0.17,
            "totalVolume": 3200,
            "totalTurnover": 1920000.0,
        }
        result = _normalise_row(row)
        assert result is not None
        assert result["open_price"] == 598.0
        assert result["high_price"] == 610.0
        assert result["low_price"] == 595.0
        assert result["previous_close"] == 599.0
        assert result["percent_change"] == 0.17
        assert result["volume"] == 3200.0
        assert result["turnover"] == 1920000.0

    def test_alias_fields_sharesansar(self):
        """sharesansar / yonepse may use qty/traded_quantity."""
        row = {
            "symbol": "SBI",
            "ltp": 350.0,
            "open": 348.0,
            "high": 355.0,
            "low": 347.0,
            "prev_close": 349.0,
            "change_percent": -0.29,
            "qty": 5000,
            "traded_value": 1750000.0,
        }
        result = _normalise_row(row)
        assert result is not None
        assert result["volume"] == 5000.0
        assert result["turnover"] == 1750000.0
        assert result["previous_close"] == 349.0
