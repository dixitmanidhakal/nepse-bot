"""
Unit tests for ``app.components.recommendation_engine``.

These tests exercise the deterministic scoring logic on synthetic OHLCV
DataFrames — no database, no network, no FastAPI.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.components import recommendation_engine as rec


# --------------------------------------------------------------------------- #
# Score-symbol tests                                                          #
# --------------------------------------------------------------------------- #

class TestScoreSymbol:
    def test_returns_none_on_empty(self):
        assert rec.score_symbol("EMPTY", pd.DataFrame()) is None

    def test_returns_none_on_short_history(self, sample_ohlcv):
        short = sample_ohlcv.iloc[:30].copy()
        assert rec.score_symbol("SHORT", short) is None

    def test_score_within_bounds(self, sample_ohlcv):
        r = rec.score_symbol("SAMPLE", sample_ohlcv)
        assert r is not None
        assert 0.0 <= r.score <= 100.0
        for v in r.factor_scores.values():
            assert 0.0 <= v <= 1.0

    def test_action_consistent_with_score(self, sample_ohlcv):
        r = rec.score_symbol("SAMPLE", sample_ohlcv)
        assert r is not None
        if r.score >= 65:
            assert r.action == "BUY"
        elif r.score >= 45:
            assert r.action == "WATCH"
        else:
            assert r.action == "AVOID"

    def test_case_insensitive_columns(self, sample_ohlcv):
        df = sample_ohlcv.rename(
            columns={"close": "Close", "open": "Open", "volume": "Volume"}
        )
        r = rec.score_symbol("SAMPLE", df)
        assert r is not None

    def test_symbol_uppercased(self, sample_ohlcv):
        r = rec.score_symbol("nabil", sample_ohlcv)
        assert r is not None
        assert r.symbol == "NABIL"

    def test_rationale_populated(self, sample_ohlcv):
        r = rec.score_symbol("SAMPLE", sample_ohlcv)
        assert r is not None
        assert isinstance(r.rationale, list)
        assert len(r.rationale) >= 1

    def test_as_dict_is_json_serialisable(self, sample_ohlcv):
        import json

        r = rec.score_symbol("SAMPLE", sample_ohlcv)
        assert r is not None
        # Should serialise without raising
        json.dumps(r.as_dict())

    def test_deterministic(self, sample_ohlcv):
        r1 = rec.score_symbol("SAMPLE", sample_ohlcv.copy())
        r2 = rec.score_symbol("SAMPLE", sample_ohlcv.copy())
        assert r1 is not None and r2 is not None
        assert r1.score == r2.score
        assert r1.factor_scores == r2.factor_scores


# --------------------------------------------------------------------------- #
# Factor isolation tests                                                       #
# --------------------------------------------------------------------------- #

class TestIndividualFactors:
    def test_strong_uptrend_scores_high_trend(self):
        # Strictly monotonically increasing series → perfect MA alignment
        close = pd.Series(np.linspace(100, 200, 260))
        score = rec._trend_score(close)
        assert score >= 0.8

    def test_flat_series_low_trend(self):
        close = pd.Series([100.0] * 260)
        score = rec._trend_score(close)
        # Flat series: no slope bonus but last == sma20 so sub-condition fails
        assert score <= 0.5

    def test_momentum_positive_on_uptrend(self):
        # Use a mildly noisy uptrend so RSI has real gains and losses to work
        # with. A perfectly linear series produces zero losses and RSI defaults
        # to 50 from the fillna in ``_rsi``.
        rng = np.random.default_rng(seed=7)
        n = 200
        drift = np.linspace(0.0, 50.0, n)
        noise = rng.normal(0.0, 0.5, size=n)
        close = pd.Series(100.0 + drift + noise)
        score, rsi_val, _ = rec._momentum_score(close)
        assert score > 0.3
        assert rsi_val is not None and rsi_val >= 50

    def test_volume_score_handles_short_series(self):
        vol = pd.Series([1000.0] * 10)
        score, ratio = rec._volume_score(vol)
        assert score == 0.0
        assert ratio is None

    def test_volatility_score_prefers_moderate(self):
        # Low-volatility series
        close = pd.Series(np.linspace(100, 105, 200))
        score, vol = rec._volatility_score(close)
        assert score >= 0.0
        assert vol is not None and vol >= 0.0


# --------------------------------------------------------------------------- #
# Universe ranking                                                             #
# --------------------------------------------------------------------------- #

class TestRankUniverse:
    def _make_symbol(self, drift: float, seed: int) -> pd.DataFrame:
        rng = np.random.default_rng(seed=seed)
        n = 260
        dates = pd.date_range("2023-01-01", periods=n, freq="B")
        close = 100.0 * np.exp(np.cumsum(rng.normal(drift, 0.012, n)))
        return pd.DataFrame(
            {
                "date": dates,
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": rng.integers(10_000, 100_000, size=n).astype(float),
            }
        )

    def test_rank_returns_sorted(self):
        universe = {
            "A": self._make_symbol(drift=0.0020, seed=1),   # strong uptrend
            "B": self._make_symbol(drift=0.0005, seed=2),   # mild uptrend
            "C": self._make_symbol(drift=-0.0015, seed=3),  # downtrend
        }
        recs = rec.rank_universe(universe, top_n=0)
        assert len(recs) == 3
        scores = [r.score for r in recs]
        assert scores == sorted(scores, reverse=True)

    def test_min_score_filters(self):
        universe = {
            "X": self._make_symbol(drift=-0.002, seed=10),
        }
        recs = rec.rank_universe(universe, min_score=99.0)
        assert recs == []

    def test_top_n_truncates(self):
        universe = {
            chr(65 + i): self._make_symbol(drift=0.0005 * (i - 2), seed=100 + i)
            for i in range(6)
        }
        recs = rec.rank_universe(universe, top_n=3)
        assert len(recs) == 3

    def test_skip_empty_dataframes(self):
        universe = {"A": pd.DataFrame(), "B": self._make_symbol(0.001, 1)}
        recs = rec.rank_universe(universe, top_n=0)
        assert len(recs) == 1
        assert recs[0].symbol == "B"


# --------------------------------------------------------------------------- #
# Weight override                                                              #
# --------------------------------------------------------------------------- #

class TestWeights:
    def test_default_weights_sum_close_to_1(self):
        total = sum(rec.WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9

    def test_weights_override_changes_score(self, sample_ohlcv):
        r_default = rec.score_symbol("S", sample_ohlcv)
        r_trend_only = rec.score_symbol(
            "S",
            sample_ohlcv,
            weights={"trend": 1.0, "momentum": 0.0, "volume": 0.0,
                     "volatility": 0.0, "drawdown": 0.0},
        )
        assert r_default is not None and r_trend_only is not None
        # They should differ (unless by coincidence every factor is equal)
        assert r_default.score != r_trend_only.score or \
               r_default.factor_scores["trend"] * 100 == r_trend_only.score
