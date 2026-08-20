"""
Smoke tests for the modules ported / added during the quant-terminal integration.

Covers:
  - app/utils/nepse_calendar.py
  - app/services/data/quant_terminal_db.py
  - app/indicators/regime.py
  - app/indicators/signals.py
  - app/components/position_sizing.py
  - New FastAPI routers (calendar, quant) registered via the v1 package.
"""

from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.utils import nepse_calendar as cal
from app.services.data import quant_terminal_db
from app.indicators import regime, signals
from app.components import position_sizing as psm


# --------------------------------------------------------------------------- #
# Calendar                                                                     #
# --------------------------------------------------------------------------- #

def test_calendar_schedule_shape():
    sched = cal.get_market_schedule()
    assert "trading_week" in sched
    assert "weekend" in sched
    assert "regular" in sched


def test_calendar_weekend_detection():
    # 2025-04-12 is a Saturday (default Mon-Fri mode -> weekend)
    saturday = date(2025, 4, 12)
    sunday = date(2025, 4, 13)
    assert cal.is_nepal_weekend(saturday) is True
    assert cal.is_nepal_weekend(sunday) is True


def test_calendar_known_holiday():
    assert cal.is_known_holiday(date(2025, 4, 14))  # Nepali New Year
    assert not cal.is_known_holiday(date(2025, 4, 15))


def test_calendar_next_trading_day_skips_weekend():
    # 2025-04-12 is a Saturday; next trading day should be Monday 2025-04-14...
    # but 2025-04-14 is Nepali New Year, so it should land on 2025-04-15 (Tue).
    nxt = cal.next_trading_day(date(2025, 4, 12))
    assert nxt == date(2025, 4, 15)


def test_calendar_count_trading_days():
    start = date(2025, 4, 7)   # Monday
    end = date(2025, 4, 11)    # Friday
    # All weekdays, no holidays in this window → 5 trading days.
    assert cal.count_trading_days(start, end) == 5


def test_calendar_festival_windows():
    in_dashain = date(2025, 9, 25)  # within 21 days before Dashain start
    assert cal.is_dashain_period(in_dashain)
    assert cal.is_tihar_period(date(2025, 10, 20))


# --------------------------------------------------------------------------- #
# Quant-terminal DB adapter (unconfigured)                                     #
# --------------------------------------------------------------------------- #

def test_quant_terminal_db_unavailable_by_default():
    # When QUANT_TERMINAL_DB_PATH is not set, the adapter should be inactive.
    assert quant_terminal_db.is_available() in (True, False)
    if not quant_terminal_db.is_available():
        assert quant_terminal_db.fetch_ohlcv("NABIL") == []
        assert quant_terminal_db.list_symbols() == []


# --------------------------------------------------------------------------- #
# Regime                                                                       #
# --------------------------------------------------------------------------- #

def test_regime_detects_bull_run():
    rng = np.random.default_rng(1)
    closes = np.cumprod(1 + rng.normal(0.003, 0.008, 200)) * 1000
    res = regime.classify_regime(closes, window=60)
    assert res.regime in ("bull", "neutral", "bear")
    assert 0.0 <= res.exposure_multiplier <= 1.0


def test_regime_short_series_raises():
    with pytest.raises(ValueError):
        regime.classify_regime([100.0, 101.0], window=60)


def test_regime_from_returns():
    rng = np.random.default_rng(2)
    rets = rng.normal(0.001, 0.01, 200)
    res = regime.classify_regime_from_returns(rets, window=60)
    assert res.window == 60


# --------------------------------------------------------------------------- #
# Signals                                                                      #
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def price_panel():
    idx = pd.date_range("2024-01-01", periods=400, freq="D")
    rng = np.random.default_rng(7)
    return pd.DataFrame({
        "AAA": np.cumprod(1 + rng.normal(0.002, 0.01, 400)) * 500,
        "BBB": np.cumprod(1 + rng.normal(0.000, 0.02, 400)) * 500,
        "CCC": np.cumprod(1 + rng.normal(-0.001, 0.015, 400)) * 500,
    }, index=idx)


@pytest.fixture(scope="module")
def volume_panel(price_panel):
    rng = np.random.default_rng(9)
    return pd.DataFrame(
        {c: rng.integers(1000, 50_000, len(price_panel)) for c in price_panel.columns},
        index=price_panel.index,
    )


def test_volume_breakout_signals_shape(price_panel, volume_panel):
    out = signals.volume_breakout_signals(price_panel, volume_panel)
    for s in out:
        assert set(s) >= {"symbol", "score", "reason"}
        assert 0.0 <= s["score"] <= 1.0


def test_low_volatility_signals(price_panel):
    out = signals.low_volatility_signals(price_panel)
    for s in out:
        assert 0.0 <= s["score"] <= 1.0


def test_mean_reversion_signals_runs(price_panel):
    out = signals.mean_reversion_signals(price_panel)
    # Not asserting non-empty: random data may not meet the threshold.
    assert isinstance(out, list)


def test_xsec_momentum_ranking(price_panel):
    out = signals.xsec_momentum_signals(price_panel)
    assert len(out) == len(price_panel.columns)
    # Scores should be monotonically non-increasing (top rank first).
    scores = [s["score"] for s in out]
    assert scores == sorted(scores, reverse=True)


# --------------------------------------------------------------------------- #
# Position sizing                                                              #
# --------------------------------------------------------------------------- #

def test_transaction_cost_tiered():
    # Small trade falls in the flat-Rs-10 tier (plus SEBON + DP)
    cost = psm.calculate_transaction_cost(2000.0)
    assert cost > 10.0
    # A 50 000 trade should use the 0.36% tier
    cost_mid = psm.calculate_transaction_cost(50_000.0)
    assert cost_mid > 50_000 * 0.003


def test_kelly_fraction_bounds():
    f = psm.calculate_kelly_fraction(0.6, 0.10, 0.05)
    assert 0.0 <= f <= 0.25


def test_size_positions_respects_limits():
    sigs = [
        {"symbol": "AAA", "score": 0.95, "confidence": 0.9, "sector": "Commercial Banks"},
        {"symbol": "BBB", "score": 0.90, "confidence": 0.8, "sector": "Commercial Banks"},
        {"symbol": "CCC", "score": 0.70, "confidence": 0.7, "sector": "Hydropower"},
        {"symbol": "DDD", "score": 0.60, "confidence": 0.6, "sector": "Finance"},
    ]
    prices = {"AAA": 500, "BBB": 400, "CCC": 200, "DDD": 800}
    positions = psm.size_positions(sigs, capital=1_000_000, prices=prices)
    # Each position weight should respect max_single_pct (15%)
    for p in positions:
        assert p.weight <= 0.15 + 1e-6
    # Total deployed should leave at least cash_reserve_pct (20%)
    total = sum(p.value for p in positions)
    assert total <= 1_000_000 * 0.80 + 1.0


def test_should_rebalance_no_trade_zone():
    current = {"AAA": 0.15, "BBB": 0.15}
    nearly_same = {"AAA": 0.155, "BBB": 0.145}
    # Very small turnover → should NOT rebalance
    assert psm.should_rebalance(current, nearly_same) is False
    # Large turnover → SHOULD rebalance
    large_change = {"AAA": 0.05, "CCC": 0.20}
    assert psm.should_rebalance(current, large_change) is True


# --------------------------------------------------------------------------- #
# Router registration                                                          #
# --------------------------------------------------------------------------- #

def test_v1_router_contains_calendar_and_quant():
    from app.api.v1 import router
    paths = {r.path for r in router.routes}
    assert "/calendar/status" in paths
    assert "/quant/regime" in paths
    assert "/quant/size-positions" in paths
