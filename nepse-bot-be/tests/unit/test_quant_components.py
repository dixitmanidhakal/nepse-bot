"""
Unit tests for quant modules ported from nepse-quant-terminal.

Each suite focuses on the public API surface exposed through
``app.components.quant``. Heavy-compute tests (HMM fit on 500 points) are
gated on the availability of ``hmmlearn`` so CI without the optional
dependency still passes.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app.components.quant import (
    conformal,
    disposition,
    market_state,
    pairs,
    portfolio,
    regime,
    signal_types,
    signals,
)
from app.components.quant.signal_types import AlphaSignal, SignalType


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────
def _synthetic_prices(n: int = 252, seed: int = 42,
                      start: float = 100.0) -> pd.Series:
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0005, 0.012, n)
    return pd.Series(start * np.exp(np.cumsum(noise)),
                     index=pd.date_range("2024-01-01", periods=n, freq="B"))


def _synthetic_universe(symbols=("NABIL", "GBIME", "PCBL", "SCB"),
                        n: int = 252, seed: int = 7) -> pd.DataFrame:
    """
    Build a LONG-format DataFrame (symbol, date, open, high, low, close,
    volume) which is the shape the ported quant modules expect.
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    rows = []
    for sym in symbols:
        close = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.015, n)))
        for d, c in zip(dates, close):
            rows.append({
                "symbol": sym,
                "date":   d,
                "open":   c,
                "high":   c * 1.01,
                "low":    c * 0.99,
                "close":  c,
                "volume": 100_000 + rng.integers(0, 50_000),
            })
    return pd.DataFrame(rows)


# ════════════════════════════════════════════════════════════════════════════
# signal_types
# ════════════════════════════════════════════════════════════════════════════
class TestSignalTypes:
    def test_alpha_signal_score(self):
        sig = AlphaSignal(
            symbol="NABIL", signal_type=SignalType.MOMENTUM,
            direction=1, strength=0.7, confidence=0.8,
            reasoning="uptrend",
        )
        assert abs(sig.score - 0.56) < 1e-9

    def test_alpha_signal_to_dict_roundtrip(self):
        sig = AlphaSignal(
            symbol="ABC", signal_type=SignalType.PAIRS_TRADE,
            direction=-1, strength=0.5, confidence=0.5,
            reasoning="mean reversion",
            expires=datetime(2026, 1, 1),
        )
        d = sig.to_dict()
        assert d["symbol"] == "ABC"
        assert d["signal_type"] == "pairs_trade"
        assert d["score"] == -0.25
        assert d["expires"] == "2026-01-01T00:00:00"
        assert d["target_exit_date"] is None


# ════════════════════════════════════════════════════════════════════════════
# signals — canonicalisation + ranking
# ════════════════════════════════════════════════════════════════════════════
class TestSignalsRanking:
    def test_canonicalise_applies_alias(self):
        assert signals.canonicalize_signal_symbol("RHPC") == "RIDI"
        assert signals.canonicalize_signal_symbol("nabil") == "NABIL"

    def test_canonicalise_empty(self):
        assert signals.canonicalize_signal_symbol("") == ""
        assert signals.canonicalize_signal_symbol(None) == ""

    def test_tradeable_rejects_nepse_and_sectors(self):
        assert signals.is_tradeable_signal_symbol("NABIL") is True
        assert signals.is_tradeable_signal_symbol("NEPSE") is False
        assert signals.is_tradeable_signal_symbol("SECTOR::BANKING") is False

    def test_rank_orders_by_score(self):
        raw = [
            {"symbol": "A", "signal_type": "momentum",
             "strength": 0.9, "confidence": 0.9, "reasoning": ""},
            {"symbol": "B", "signal_type": "momentum",
             "strength": 0.4, "confidence": 0.5, "reasoning": ""},
            {"symbol": "C", "signal_type": "momentum",
             "strength": 0.7, "confidence": 0.7, "reasoning": ""},
        ]
        merged = signals.merge_signal_candidates(raw)
        ranked = signals.rank_signal_candidates(merged)
        # highest score first
        scores = [r["score"] for r in ranked]
        assert scores == sorted(scores, reverse=True)
        assert ranked[0]["symbol"] == "A"

    def test_event_context_stub_zero_adjustment(self):
        ctx = signals.EventAdjustmentContext(market_adjustment=0.05)
        details = ctx.details_for("NABIL", "banking")
        # stub returns zero adjustments regardless of market adjustment
        assert details["event_adjustment"] == 0.0
        assert details["sector_adjustment"] == 0.0


# ════════════════════════════════════════════════════════════════════════════
# regime — BOCPD (no external deps)
# ════════════════════════════════════════════════════════════════════════════
class TestBOCPD:
    def test_bocpd_runs_and_returns_probabilities(self):
        rng = np.random.default_rng(0)
        pre  = rng.normal(0.001, 0.01, 200)
        post = rng.normal(-0.005, 0.03, 200)
        returns = np.concatenate([pre, post])
        cp_probs, changepoints = regime.run_bocpd_on_returns(
            returns, hazard_lambda=100, threshold=0.1
        )
        assert len(cp_probs) == len(returns)
        assert len(changepoints) == len(returns)
        # all probabilities must be valid
        assert np.all(cp_probs >= 0.0)
        assert np.all(cp_probs <= 1.0)

    def test_bocpd_detector_resets_run_length(self):
        det = regime.BOCPDDetector()
        for r in np.random.default_rng(0).normal(0, 0.01, 50):
            det.update(r)
        # property, not method
        assert det.expected_run_length > 0
        det.reset()
        assert det.expected_run_length == 0.0


# ════════════════════════════════════════════════════════════════════════════
# regime — HMM (skipped when hmmlearn missing)
# ════════════════════════════════════════════════════════════════════════════
_HMM_AVAILABLE = True
try:  # pragma: no cover
    import hmmlearn  # noqa: F401
except ImportError:  # pragma: no cover
    _HMM_AVAILABLE = False


@pytest.mark.skipif(not _HMM_AVAILABLE, reason="hmmlearn not installed")
class TestHMM:
    def test_hmm_regime_classification(self):
        prices = _synthetic_prices(n=300)
        result = regime.detect_regime_from_prices(
            prices, n_states=3, lookback=252, n_init=2
        )
        assert "regime" in result
        assert result["regime"] in {"bull", "neutral", "bear"}
        assert "probabilities" in result
        probs = result["probabilities"]
        assert abs(sum(probs.values()) - 1.0) < 1e-6


# ════════════════════════════════════════════════════════════════════════════
# market_state
# ════════════════════════════════════════════════════════════════════════════
class TestMarketState:
    def test_compute_market_state_returns_regime(self):
        prices = _synthetic_universe(n=300)
        as_of = prices["date"].iloc[-1]
        state = market_state.compute_market_state(prices, as_of)
        # regime is an attribute on the MarketState dataclass
        assert state.regime in {"TRENDING", "CHOPPY", "NEUTRAL"}
        assert 0.0 <= state.score <= 4.0
        # summary() returns a human-readable string
        assert isinstance(state.summary(), str)


# ════════════════════════════════════════════════════════════════════════════
# pairs
# ════════════════════════════════════════════════════════════════════════════
class TestPairs:
    def test_compute_spread_returns_tuple(self):
        rng = np.random.default_rng(0)
        prices_a = 100 + np.cumsum(rng.normal(0, 0.5, 200))
        prices_b = 100 + np.cumsum(rng.normal(0, 0.5, 200))
        trader = pairs.PairsTrader(lookback=60)
        spread, z_score, beta, spread_mean, halflife = trader.compute_spread(
            prices_a, prices_b,
        )
        assert len(spread) == 200
        assert isinstance(z_score, float)
        assert isinstance(beta, float)
        assert isinstance(spread_mean, float)
        assert halflife is None or isinstance(halflife, float)


# ════════════════════════════════════════════════════════════════════════════
# portfolio
# ════════════════════════════════════════════════════════════════════════════
class TestPortfolio:
    def test_equal_weight_fallback(self):
        prices = _synthetic_universe(symbols=("A", "B", "C"), n=250)
        as_of = prices["date"].iloc[-1]
        alloc = portfolio.allocate_portfolio(
            method="equal", prices_df=prices,
            symbols=["A", "B", "C"],
            date=as_of, capital=300_000.0,
        )
        total = sum(v for k, v in alloc.items() if not k.startswith("_"))
        assert abs(total - 300_000.0) < 1.0
        assert len(alloc) >= 3

    def test_hrp_allocation_sums_to_capital(self):
        prices = _synthetic_universe(symbols=("A", "B", "C", "D"), n=252)
        as_of = prices["date"].iloc[-1]
        alloc = portfolio.allocate_portfolio(
            method="hrp", prices_df=prices,
            symbols=["A", "B", "C", "D"],
            date=as_of, capital=1_000_000.0,
        )
        weight_total = sum(
            v for k, v in alloc.items()
            if not k.startswith("_") and isinstance(v, (int, float))
        )
        assert 950_000 < weight_total <= 1_000_000 + 1

    def test_gold_hedge_unavailable_raises(self):
        prices = _synthetic_universe(symbols=("A",), n=252)
        as_of = prices["date"].iloc[-1]
        with pytest.raises(NotImplementedError):
            portfolio.allocate_portfolio(
                method="equal", prices_df=prices, symbols=["A"],
                date=as_of, capital=100_000.0,
                gold_hedge_db_path="/does/not/exist.db",
            )


# ════════════════════════════════════════════════════════════════════════════
# conformal
# ════════════════════════════════════════════════════════════════════════════
class TestConformal:
    def test_conformal_var_returns_negative_number(self):
        rng = np.random.default_rng(0)
        returns = rng.normal(0.0005, 0.015, 500)
        var_est = conformal.compute_conformal_var(
            returns=returns, alpha=0.05, window=252,
        )
        # VaR should be a negative return (loss threshold)
        assert var_est < 0
        # and within plausible bounds for 1.5% daily vol
        assert var_est > -0.1

    def test_position_scale_in_unit_interval(self):
        rng = np.random.default_rng(0)
        returns = rng.normal(0.0, 0.02, 400)
        scale = conformal.compute_conformal_position_scale(
            returns=returns, alpha=0.05, max_loss_pct=0.02,
        )
        assert 0.0 <= scale <= 1.0


# ════════════════════════════════════════════════════════════════════════════
# disposition
# ════════════════════════════════════════════════════════════════════════════
class TestDisposition:
    def test_generate_cgo_signals_runs(self):
        prices = _synthetic_universe(symbols=("A", "B"), n=300)
        as_of = prices["date"].iloc[-1]
        # function may or may not accept volume_df depending on signature
        try:
            sigs = disposition.generate_cgo_signals_at_date(
                prices_df=prices, date=as_of,
                cgo_threshold=0.05, volume_spike=1.0,
            )
        except TypeError:
            sigs = disposition.generate_cgo_signals_at_date(
                prices_df=prices, date=as_of,
            )
        assert isinstance(sigs, list)
        for s in sigs:
            assert isinstance(s, signal_types.AlphaSignal)
