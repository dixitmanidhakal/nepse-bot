"""
Shared pytest fixtures for nepse-bot-be.

Defines:
    - ``client``        : FastAPI TestClient bound to the application.
    - ``sample_ohlcv``  : deterministic synthetic OHLCV DataFrame used by
                          indicator and engine tests.
    - ``historical_provider_available``: marker-style fixture to skip tests that
                                         require the SQLite snapshot.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterator

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

# ── Test-environment guards (must be set BEFORE any app module is imported) ──
# Disable the APScheduler bot scheduler so its background threads don't open
# extra DB connections during testing.  The scheduler fires bot cycles every
# 15 min and holds SessionLocal() connections; without this guard the QueuePool
# (size=5 + overflow=10) is exhausted part-way through the test suite,
# causing intermittent TimeoutErrors on later bot-endpoint tests.
os.environ.setdefault("BOT_SCHEDULER_ENABLED", "false")

# Ensure the backend package is importable when pytest is invoked from repo root.
_BE_ROOT = Path(__file__).resolve().parent.parent
if str(_BE_ROOT) not in sys.path:
    sys.path.insert(0, str(_BE_ROOT))


@pytest.fixture(scope="session")
def app_instance():
    """Import the FastAPI app once per session."""
    from app.main import app as fastapi_app

    return fastapi_app


@pytest.fixture
def client(app_instance) -> Iterator[TestClient]:
    """A TestClient bound to the FastAPI app. Lifespan startup hooks still run."""
    with TestClient(app_instance) as c:
        yield c


@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    """
    Deterministic synthetic OHLCV series (300 trading days of a mild uptrend).

    Uses a fixed random seed so the derived indicator values are reproducible.
    """
    rng = np.random.default_rng(seed=42)
    n = 300
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    # Geometric Brownian-ish path with slight positive drift.
    log_ret = rng.normal(loc=0.0008, scale=0.015, size=n)
    close = 100.0 * np.exp(np.cumsum(log_ret))
    open_ = close * (1.0 + rng.normal(0.0, 0.003, n))
    high = np.maximum(close, open_) * (1.0 + np.abs(rng.normal(0.0, 0.004, n)))
    low = np.minimum(close, open_) * (1.0 - np.abs(rng.normal(0.0, 0.004, n)))
    volume = rng.integers(10_000, 200_000, size=n).astype(float)
    return pd.DataFrame(
        {
            "date": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


@pytest.fixture
def historical_provider():
    """The shared singleton HistoricalDataProvider."""
    from app.services.data.historical_provider import get_historical_provider

    return get_historical_provider()


@pytest.fixture
def historical_provider_available(historical_provider):
    """Skip the calling test when the SQLite snapshot is not configured."""
    if not historical_provider.is_available():
        pytest.skip(
            "Historical SQLite DB not configured (set QUANT_TERMINAL_DB_PATH)."
        )
    return historical_provider
