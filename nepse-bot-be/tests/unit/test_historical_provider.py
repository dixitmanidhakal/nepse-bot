"""
Unit tests for ``app.services.data.historical_provider.HistoricalDataProvider``.

Tests that require a real SQLite DB use the ``historical_provider_available``
fixture which auto-skips when the snapshot isn't configured.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from app.services.data.historical_provider import HistoricalDataProvider


@pytest.fixture
def mock_sqlite_db():
    """Spin up a throw-away SQLite DB populated with deterministic prices."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    p = Path(tmp.name)

    conn = sqlite3.connect(p)
    conn.execute(
        """
        CREATE TABLE stock_prices (
            symbol TEXT, date TEXT,
            open REAL, high REAL, low REAL, close REAL, volume REAL
        )
        """
    )
    rows = []
    for sym in ("AAA", "BBB"):
        for i in range(200):
            d = f"2024-01-{(i % 28) + 1:02d}"  # simple deterministic date strings
            d = (pd.Timestamp("2024-01-01") + pd.Timedelta(days=i)).strftime("%Y-%m-%d")
            price = 100.0 + i * (0.5 if sym == "AAA" else 0.1)
            rows.append((sym, d, price, price * 1.01, price * 0.99, price, 10_000))
    conn.executemany(
        "INSERT INTO stock_prices VALUES (?, ?, ?, ?, ?, ?, ?)", rows
    )
    conn.commit()
    conn.close()

    yield p
    p.unlink(missing_ok=True)


class TestHistoricalProviderIsolated:
    def test_is_available_when_file_missing(self):
        prov = HistoricalDataProvider(db_path="/nonexistent/path.db")
        assert prov.is_available() is False
        assert prov.list_symbols() == []
        assert prov.load_ohlcv("ANY").empty

    def test_load_ohlcv_from_mock_db(self, mock_sqlite_db):
        prov = HistoricalDataProvider(db_path=str(mock_sqlite_db))
        assert prov.is_available()
        df = prov.load_ohlcv("AAA")
        assert not df.empty
        assert len(df) == 200
        assert list(df.columns) == ["date", "open", "high", "low", "close", "volume"]
        # Sorted chronologically
        assert df["date"].is_monotonic_increasing

    def test_load_ohlcv_case_insensitive(self, mock_sqlite_db):
        prov = HistoricalDataProvider(db_path=str(mock_sqlite_db))
        df = prov.load_ohlcv("aaa")
        assert not df.empty

    def test_load_ohlcv_min_rows_filters(self, mock_sqlite_db):
        prov = HistoricalDataProvider(db_path=str(mock_sqlite_db))
        df = prov.load_ohlcv("AAA", min_rows=500)
        assert df.empty  # 200 rows < 500 threshold

    def test_list_symbols(self, mock_sqlite_db):
        prov = HistoricalDataProvider(db_path=str(mock_sqlite_db))
        syms = prov.list_symbols()
        assert "AAA" in syms
        assert "BBB" in syms

    def test_symbol_metadata(self, mock_sqlite_db):
        prov = HistoricalDataProvider(db_path=str(mock_sqlite_db))
        meta = prov.symbol_metadata()
        assert "AAA" in meta
        assert meta["AAA"].row_count == 200
        assert meta["AAA"].first_date is not None
        assert meta["AAA"].last_date is not None

    def test_load_universe_returns_dict(self, mock_sqlite_db):
        prov = HistoricalDataProvider(db_path=str(mock_sqlite_db))
        panel = prov.load_universe(min_rows=50)
        assert "AAA" in panel and "BBB" in panel
        for sym, df in panel.items():
            assert "close" in df.columns

    def test_load_universe_min_rows_filters(self, mock_sqlite_db):
        prov = HistoricalDataProvider(db_path=str(mock_sqlite_db))
        panel = prov.load_universe(min_rows=5000)
        assert panel == {}

    def test_load_universe_respects_symbol_filter(self, mock_sqlite_db):
        prov = HistoricalDataProvider(db_path=str(mock_sqlite_db))
        panel = prov.load_universe(symbols=["AAA"], min_rows=10)
        assert set(panel.keys()) == {"AAA"}


class TestHistoricalProviderReal:
    """Tests that run against the real quant-terminal SQLite snapshot."""

    def test_provider_is_available(self, historical_provider_available):
        assert historical_provider_available.is_available()

    def test_real_db_has_symbols(self, historical_provider_available):
        syms = historical_provider_available.list_symbols()
        assert len(syms) >= 100

    def test_real_load_common_symbol(self, historical_provider_available):
        df = historical_provider_available.load_ohlcv("NABIL", min_rows=60)
        assert not df.empty
        assert {"date", "open", "high", "low", "close", "volume"}.issubset(df.columns)
        assert df["close"].dtype.kind in {"f", "i"}
