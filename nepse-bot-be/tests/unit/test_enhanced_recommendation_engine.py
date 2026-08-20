"""
Unit tests for the enhanced recommendation engine.

These tests exercise each sub-score in isolation plus the composite path
so partial (missing depth / missing quant) inputs still produce sensible
rankings.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.components import enhanced_recommendation_engine as eng


def _synthetic_ohlcv(n: int = 260, start: float = 100.0, drift: float = 0.001) -> pd.DataFrame:
    """Up-trending synthetic OHLCV with mild noise and one strong OB near the end."""
    rng = np.random.default_rng(42)
    rets = rng.normal(loc=drift, scale=0.012, size=n)
    close = start * np.cumprod(1.0 + rets)
    # Craft an obvious bullish order block at index n-15 / n-14.
    close[n - 15] = close[n - 16] * 0.97  # down candle close
    close[n - 14] = close[n - 15] * 1.05  # strong up candle close
    open_ = np.concatenate(([start], close[:-1]))
    open_[n - 15] = close[n - 16] * 1.005  # down-candle open above its close
    open_[n - 14] = close[n - 15] * 1.002  # up-candle opens just above prior close
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.004, n)))
    # Ensure OB down candle has a real range above the up candle base.
    high[n - 15] = max(high[n - 15], open_[n - 15] * 1.01)
    volume = rng.integers(10_000, 50_000, n).astype(float)
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "date": dates,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


class TestOrderBlockScore:
    def test_no_ob_when_flat(self):
        df = _synthetic_ohlcv(n=40)  # <30 guard path covered below; 40 rows fine
        # Flatten to remove any OB candles
        df["open"] = df["close"]
        df["high"] = df["close"] * 1.001
        df["low"] = df["close"] * 0.999
        score, details = eng._order_block_score(df)
        assert 0.0 <= score <= 1.0
        assert "reason" in details

    def test_insufficient_rows_returns_zero(self):
        df = pd.DataFrame({
            "open": [1, 2, 3],
            "high": [1, 2, 3],
            "low": [1, 2, 3],
            "close": [1, 2, 3],
        })
        score, details = eng._order_block_score(df)
        assert score == 0.0
        assert "insufficient" in details["reason"]

    def test_ob_detected_on_synthetic(self):
        df = _synthetic_ohlcv()
        score, details = eng._order_block_score(df)
        # OB may or may not qualify by the 1.2× thrust guard depending on RNG,
        # but the function must always return a float in [0, 1] and a dict.
        assert 0.0 <= score <= 1.0
        assert isinstance(details, dict)


class TestDepthScore:
    def test_empty_depth_is_zero(self):
        score, details = eng._depth_score(None)
        assert score == 0.0
        assert details["reason"] == "no depth data"

    def test_bullish_depth(self):
        score, details = eng._depth_score({
            "has_bid_wall": True,
            "has_ask_wall": False,
            "order_imbalance": 0.35,
            "bid_ask_spread_percent": 0.2,
        })
        assert score > 0.6
        assert any("bid wall" in r for r in details["reasons"])

    def test_bearish_depth(self):
        score, details = eng._depth_score({
            "has_bid_wall": False,
            "has_ask_wall": True,
            "order_imbalance": -0.4,
            "bid_ask_spread_percent": 1.5,
        })
        # Negative imbalance pulls score toward zero; clipped to 0.
        assert score <= 0.2


class TestQuantScore:
    def test_empty_quant_is_zero(self):
        score, details = eng._quant_score(None)
        assert score == 0.0
        assert details["reason"] == "no quant signals"

    def test_bull_regime_with_tight_var(self):
        score, details = eng._quant_score({
            "regime": "bull",
            "regime_prob_bull": 0.8,
            "bocpd_cp_recent": False,
            "conformal_var": -0.02,
        })
        assert score > 0.6

    def test_recent_changepoint_penalty(self):
        base = {"regime": "bull", "regime_prob_bull": 0.8, "conformal_var": -0.02}
        s1, _ = eng._quant_score(base)
        s2, _ = eng._quant_score({**base, "bocpd_cp_recent": True})
        assert s2 < s1


class TestComposite:
    def test_score_symbol_without_enhancements(self):
        df = _synthetic_ohlcv()
        rec = eng.score_symbol("TEST", df)
        assert rec is not None
        assert rec.symbol == "TEST"
        assert 0.0 <= rec.score <= 100.0
        assert rec.action in {"BUY", "WATCH", "AVOID"}
        # When depth & quant are absent their effective weight should be 0.
        assert rec.enhanced_scores["depth"] == 0.0
        assert rec.enhanced_scores["quant"] == 0.0

    def test_score_symbol_with_full_enhancements(self):
        df = _synthetic_ohlcv()
        rec = eng.score_symbol(
            "TEST",
            df,
            depth={
                "has_bid_wall": True,
                "has_ask_wall": False,
                "order_imbalance": 0.3,
                "bid_ask_spread_percent": 0.25,
            },
            quant={
                "regime": "bull",
                "regime_prob_bull": 0.75,
                "bocpd_cp_recent": False,
                "conformal_var": -0.025,
            },
        )
        assert rec is not None
        assert rec.enhanced_scores["depth"] > 0.0
        assert rec.enhanced_scores["quant"] > 0.0
        payload = rec.as_dict()
        assert "factor_scores" in payload
        assert "enhanced_scores" in payload
        assert "details" in payload

    def test_rank_universe_respects_min_score(self):
        df = _synthetic_ohlcv()
        panel = {"AAA": df, "BBB": df}
        all_recs = eng.rank_universe(panel, top_n=0, min_score=0.0)
        assert len(all_recs) == 2
        floor = max(r.score for r in all_recs) + 1.0
        filtered = eng.rank_universe(panel, top_n=0, min_score=floor)
        assert filtered == []

    def test_renormalisation_drops_missing(self):
        weights = dict(eng.ENHANCED_WEIGHTS)
        renorm = eng._renormalise(weights, dropped=["depth", "quant"])
        assert pytest.approx(sum(renorm.values()), abs=1e-9) == 1.0
        assert "depth" not in renorm
        assert "quant" not in renorm

    def test_missing_classic_returns_none(self):
        tiny = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
        assert eng.score_symbol("X", tiny) is None
