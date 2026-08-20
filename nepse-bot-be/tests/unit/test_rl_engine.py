"""
Smoke tests for app/components/rl_engine.py

Tests the pure-logic parts (_ema, evaluate_signal, _post_mortem structure)
without a real database session.  DB-mutating paths (process_closed_trade)
are tested via the integration test.
"""

import pytest
from unittest.mock import MagicMock

from app.components.rl_engine import (
    _ema,
    evaluate_signal,
    _post_mortem,
    _ACCURACY_TIGHTEN,
    _SECTOR_FLOOR,
    _REGIME_FLOOR,
    _MIN_SECTOR_SAMPLES,
    _MIN_REGIME_SAMPLES,
)
from app.models.paper_trade import PaperTrade, TradeOutcome


# ── _ema ─────────────────────────────────────────────────────────────────────

class TestEma:
    def test_ema_win_increases_value(self):
        val = _ema(0.75, 1.0, alpha=0.2)
        assert val > 0.75

    def test_ema_loss_decreases_value(self):
        val = _ema(0.75, 0.0, alpha=0.2)
        assert val < 0.75

    def test_ema_alpha_one_replaces(self):
        assert _ema(0.50, 0.90, alpha=1.0) == 0.90

    def test_ema_alpha_zero_preserves(self):
        assert _ema(0.50, 0.90, alpha=0.0) == 0.50

    def test_ema_bounds(self):
        # After many wins starting from 0.5 the value should approach 1.0
        val = 0.5
        for _ in range(50):
            val = _ema(val, 1.0)
        assert val > 0.99


# ── _make_state helper ────────────────────────────────────────────────────────

def _make_state(
    total_trades: int = 0,
    rolling_accuracy: float = 1.0,
    sector_accuracy: dict | None = None,
    sector_counts: dict | None = None,
    regime_accuracy: dict | None = None,
    regime_counts: dict | None = None,
) -> MagicMock:
    """Create a mock BotLearningState with configurable fields."""
    state = MagicMock()
    state.total_trades    = total_trades
    state.rolling_accuracy = rolling_accuracy
    state.sector_accuracy = sector_accuracy or {}
    state.sector_counts   = sector_counts   or {}
    state.regime_accuracy = regime_accuracy or {}
    state.regime_counts   = regime_counts   or {}
    return state


# ── evaluate_signal ───────────────────────────────────────────────────────────

class TestEvaluateSignal:
    def _sig(self, sector=None, regime=None):
        return {
            "symbol":      "NABIL",
            "score":       85.0,
            "signal":      "BUY",
            "entry_price": 985.0,
            "sector":      sector,
            "regime":      regime,
        }

    def test_new_bot_always_allowed(self):
        """Fresh bot (0 trades) should pass all gates."""
        state = _make_state(total_trades=0)
        allowed, reason = evaluate_signal(self._sig(), state)
        assert allowed is True
        assert reason == "ok"

    def test_accuracy_gate_fires_above_5_trades(self):
        """Rolling accuracy < 80% with ≥ 5 trades → blocked."""
        state = _make_state(total_trades=10, rolling_accuracy=0.70)
        allowed, reason = evaluate_signal(self._sig(), state)
        assert allowed is False
        assert "accuracy" in reason.lower()
        assert "80%" in reason

    def test_accuracy_gate_does_not_fire_below_5_trades(self):
        """With fewer than 5 trades, accuracy gate is not applied."""
        state = _make_state(total_trades=3, rolling_accuracy=0.50)
        allowed, reason = evaluate_signal(self._sig(), state)
        assert allowed is True

    def test_accuracy_gate_does_not_fire_at_exactly_80pct(self):
        """Boundary: 80% accuracy is acceptable (>= 0.80 passes)."""
        state = _make_state(total_trades=10, rolling_accuracy=0.80)
        allowed, reason = evaluate_signal(self._sig(), state)
        assert allowed is True

    def test_sector_gate_fires_when_below_floor_and_sufficient_trades(self):
        """Sector with accuracy < 60% after ≥ 3 trades → blocked."""
        state = _make_state(
            total_trades=10,
            rolling_accuracy=0.85,
            sector_accuracy={"Banking": 0.50},
            sector_counts={"Banking": 5},
        )
        sig = self._sig(sector="Banking")
        allowed, reason = evaluate_signal(sig, state)
        assert allowed is False
        assert "Banking" in reason
        assert "60%" in reason

    def test_sector_gate_does_not_fire_with_insufficient_samples(self):
        """If sector has < 3 trades, accuracy is not trusted → allow."""
        state = _make_state(
            total_trades=5,
            rolling_accuracy=0.85,
            sector_accuracy={"Banking": 0.40},
            sector_counts={"Banking": 2},   # < _MIN_SECTOR_SAMPLES (3)
        )
        sig = self._sig(sector="Banking")
        allowed, reason = evaluate_signal(sig, state)
        assert allowed is True

    def test_sector_gate_does_not_fire_above_floor(self):
        """Sector with 65% accuracy is above the 60% floor → allowed."""
        state = _make_state(
            total_trades=10,
            rolling_accuracy=0.85,
            sector_accuracy={"Hydropower": 0.65},
            sector_counts={"Hydropower": 8},
        )
        sig = self._sig(sector="Hydropower")
        allowed, reason = evaluate_signal(sig, state)
        assert allowed is True

    def test_regime_gate_fires_when_below_floor(self):
        """Regime with accuracy < 60% and ≥ 3 trades → blocked."""
        state = _make_state(
            total_trades=10,
            rolling_accuracy=0.85,
            regime_accuracy={"sideways": 0.45},
            regime_counts={"sideways": 4},
        )
        sig = self._sig(regime="sideways")
        allowed, reason = evaluate_signal(sig, state)
        assert allowed is False
        assert "sideways" in reason

    def test_regime_gate_does_not_fire_with_insufficient_samples(self):
        """Regime with 1 trade: too few samples to trust → allow."""
        state = _make_state(
            total_trades=5,
            rolling_accuracy=0.85,
            regime_accuracy={"volatile": 0.30},
            regime_counts={"volatile": 1},
        )
        sig = self._sig(regime="volatile")
        allowed, reason = evaluate_signal(sig, state)
        assert allowed is True

    def test_regime_unknown_not_gated(self):
        """Regime='unknown' must never trigger the gate."""
        state = _make_state(
            total_trades=10,
            rolling_accuracy=0.85,
            regime_accuracy={"unknown": 0.10},
            regime_counts={"unknown": 10},
        )
        sig = self._sig(regime="unknown")
        allowed, _ = evaluate_signal(sig, state)
        assert allowed is True

    def test_no_sector_no_regime_passes_all_gates(self):
        """Signals without sector/regime fields skip those gates."""
        state = _make_state(total_trades=10, rolling_accuracy=0.85)
        sig = {"symbol": "NTC", "score": 82.0, "signal": "BUY", "entry_price": 700.0}
        allowed, reason = evaluate_signal(sig, state)
        assert allowed is True


# ── _post_mortem ──────────────────────────────────────────────────────────────

class TestPostMortem:
    def _make_trade(
        self,
        outcome=TradeOutcome.LOSS,
        signal_score=78.0,
        pnl_pct=-4.0,
        regime_at_entry=None,
        signal_context=None,
    ) -> MagicMock:
        trade = MagicMock(spec=PaperTrade)
        trade.outcome        = outcome
        trade.signal_score   = signal_score
        trade.pnl_pct        = pnl_pct
        trade.regime_at_entry = regime_at_entry
        trade.signal_context  = signal_context or {}
        return trade

    def test_returns_string(self):
        trade = self._make_trade()
        result = _post_mortem(trade)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_timeout_mentioned_for_timeout_outcome(self):
        trade = self._make_trade(outcome=TradeOutcome.TIMEOUT, pnl_pct=0.0)
        result = _post_mortem(trade)
        assert "sideways" in result.lower() or "timeout" in result.lower() or "target" in result.lower()

    def test_low_score_flagged(self):
        trade = self._make_trade(signal_score=76.0)
        result = _post_mortem(trade)
        assert "76.0" in result or "marginal" in result.lower() or "score" in result.lower()

    def test_sideways_regime_flagged(self):
        trade = self._make_trade(regime_at_entry="sideways")
        result = _post_mortem(trade)
        assert "sideways" in result.lower()

    def test_volatile_regime_flagged(self):
        trade = self._make_trade(regime_at_entry="volatile")
        result = _post_mortem(trade)
        assert "volatile" in result.lower() or "volatil" in result.lower()

    def test_large_loss_flagged(self):
        trade = self._make_trade(pnl_pct=-5.5)
        result = _post_mortem(trade)
        assert "stop" in result.lower() or "loss" in result.lower() or "-5.5" in result

    def test_equilibrium_zone_flagged(self):
        trade = self._make_trade(signal_context={"zone": "equilibrium"})
        result = _post_mortem(trade)
        assert "equilibrium" in result.lower()

    def test_fallback_message_when_no_pattern(self):
        """High-score, trending, small loss → fallback generic message."""
        trade = self._make_trade(
            signal_score=90.0,
            pnl_pct=-1.5,
            regime_at_entry="trending",
        )
        result = _post_mortem(trade)
        assert isinstance(result, str)
        assert len(result) > 0
