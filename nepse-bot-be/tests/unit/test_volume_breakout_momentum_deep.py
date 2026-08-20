"""
Deep unit tests for volume_breakout_bot and momentum_bot scoring functions.

Tests the pure scoring functions in isolation — no DB, no live API calls,
no historical provider.  Each function is tested with:
  - Happy-path (all gates pass → valid score)
  - Each individual gate failing → returns None
  - Monotonic sensitivity (stronger signal → higher score)
  - Boundary conditions at threshold values
  - All three timeframe parameter sets (daily / weekly / monthly)
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
import pytest

from app.components.bots.volume_breakout_bot import (
    _volume_breakout_score,
    _rsi as vb_rsi,
    VolumeBreakoutBot,
    _TF_PARAMS as VB_TF_PARAMS,
)
from app.components.bots.momentum_bot import (
    _momentum_score,
    _rsi as mom_rsi,
    _macd_hist,
    _bollinger,
    MomentumBot,
    _TF_PARAMS as MOM_TF_PARAMS,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Volume Breakout Bot
# ═══════════════════════════════════════════════════════════════════════════════

class TestVolumeBreakoutScore:
    """Tests for _volume_breakout_score()."""

    # ── baseline scenario (daily params: vol_min=2.5, rsi=45-78) ─────────────

    _VOL_RATIO  = 3.0     # 3× average → well above daily 2.5× minimum
    _CLOSE      = 505.0   # right at the N-day high (breakout)
    _HIGH_ND    = 505.0   # 20d high
    _RSI        = 60.0    # healthy momentum (45-78 range)
    _CHG_PCT    = 1.5     # green day
    _VOL_MIN    = 2.5     # daily threshold
    _RSI_MIN    = 45
    _RSI_MAX    = 78

    def _score(self, **kw) -> Optional[float]:
        args = dict(
            volume_ratio = self._VOL_RATIO,
            close_now    = self._CLOSE,
            high_nd      = self._HIGH_ND,
            rsi_now      = self._RSI,
            chg_pct      = self._CHG_PCT,
            vol_min      = self._VOL_MIN,
            rsi_min      = self._RSI_MIN,
            rsi_max      = self._RSI_MAX,
        )
        args.update(kw)
        return _volume_breakout_score(**args)

    # ── happy path ─────────────────────────────────────────────────────────

    def test_happy_path_returns_score(self):
        score = self._score()
        assert score is not None
        assert 50.0 <= score <= 100.0

    def test_score_type_is_float(self):
        score = self._score()
        assert isinstance(score, float)

    def test_score_bounded_at_100(self):
        """Even with extreme inputs, score must not exceed 100."""
        score = self._score(volume_ratio=50.0, chg_pct=10.0, rsi_now=70.0)
        assert score is not None
        assert score <= 100.0

    # ── gate: volume ratio ─────────────────────────────────────────────────

    def test_volume_below_minimum_returns_none(self):
        score = self._score(volume_ratio=2.4)  # below daily 2.5×
        assert score is None

    def test_volume_exactly_at_minimum_passes(self):
        score = self._score(volume_ratio=2.5)
        assert score is not None

    def test_volume_well_above_minimum_passes(self):
        score = self._score(volume_ratio=5.0)
        assert score is not None

    def test_higher_volume_gives_higher_score(self):
        lo = self._score(volume_ratio=2.6)
        hi = self._score(volume_ratio=5.0)
        assert hi > lo

    # ── gate: price vs N-day high ──────────────────────────────────────────

    def test_price_more_than_2pct_below_high_returns_none(self):
        """close_now < high_nd * 0.98 → reject."""
        score = self._score(close_now=490.0, high_nd=505.0)  # 490/505 = 97% < 98%
        assert score is None

    def test_price_exactly_at_2pct_below_high_is_boundary(self):
        """close_now == high_nd * 0.98 is the boundary — should pass (== not <)."""
        high = 500.0
        close = high * 0.98  # exactly at boundary
        score = self._score(close_now=close, high_nd=high)
        assert score is not None

    def test_price_above_high_passes(self):
        """Breakout above N-day high — should always pass."""
        score = self._score(close_now=510.0, high_nd=505.0)
        assert score is not None

    def test_price_at_high_gives_max_proximity_bonus(self):
        """close_now == high_nd → proximity = 0 / (high * 0.02) → proximity = 0?
        Wait: proximity = (close - high*0.98) / (high*0.02) = (high - high*0.98) / (high*0.02)
              = high * 0.02 / (high * 0.02) = 1.0 → 20 pts bonus."""
        score_at_high = self._score(close_now=505.0, high_nd=505.0)
        score_near_high = self._score(close_now=499.7, high_nd=505.0)  # ~1.05% below
        assert score_at_high > score_near_high

    # ── gate: RSI range ────────────────────────────────────────────────────

    def test_rsi_below_min_returns_none(self):
        score = self._score(rsi_now=44.0)  # below 45
        assert score is None

    def test_rsi_above_max_returns_none(self):
        score = self._score(rsi_now=79.0)  # above 78
        assert score is None

    def test_rsi_exactly_at_min_passes(self):
        score = self._score(rsi_now=45.0)
        assert score is not None

    def test_rsi_exactly_at_max_passes(self):
        score = self._score(rsi_now=78.0)
        assert score is not None

    def test_rsi_in_sweet_spot_gives_higher_score(self):
        lo = self._score(rsi_now=45.0)   # barely in range
        hi = self._score(rsi_now=65.0)   # well into range → more RSI score
        assert hi > lo

    # ── gate: price change (must be green day) ─────────────────────────────

    def test_red_day_returns_none(self):
        score = self._score(chg_pct=-0.1)
        assert score is None

    def test_zero_change_passes(self):
        """Flat day (0.0%) is not a red day — should pass."""
        score = self._score(chg_pct=0.0)
        assert score is not None

    def test_positive_change_passes(self):
        score = self._score(chg_pct=2.0)
        assert score is not None

    def test_larger_green_day_gives_higher_score(self):
        lo = self._score(chg_pct=0.1)
        hi = self._score(chg_pct=3.5)
        assert hi > lo

    # ── timeframe parameter set correctness ───────────────────────────────

    @pytest.mark.parametrize("tf,expected_vol_min", [
        ("daily",   2.5),
        ("weekly",  2.0),
        ("monthly", 1.8),
    ])
    def test_vol_min_per_timeframe(self, tf, expected_vol_min):
        assert VB_TF_PARAMS[tf]["vol_min"] == expected_vol_min

    @pytest.mark.parametrize("tf,expected_lookback", [
        ("daily",   20),
        ("weekly",  50),
        ("monthly", 100),
    ])
    def test_lookback_high_per_timeframe(self, tf, expected_lookback):
        assert VB_TF_PARAMS[tf]["lookback_high"] == expected_lookback

    @pytest.mark.parametrize("tf", ["daily", "weekly", "monthly"])
    def test_stop_less_than_target_per_timeframe(self, tf):
        p = VB_TF_PARAMS[tf]
        assert p["stop_pct"] < p["target_pct"]

    def test_timeframe_vol_min_ordering(self):
        """monthly vol_min < weekly vol_min < daily vol_min (wider net monthly)."""
        assert VB_TF_PARAMS["monthly"]["vol_min"] < VB_TF_PARAMS["weekly"]["vol_min"]
        assert VB_TF_PARAMS["weekly"]["vol_min"]  < VB_TF_PARAMS["daily"]["vol_min"]

    def test_timeframe_stop_increases_with_tf(self):
        """Wider stops for longer timeframes."""
        assert VB_TF_PARAMS["daily"]["stop_pct"] < VB_TF_PARAMS["weekly"]["stop_pct"]
        assert VB_TF_PARAMS["weekly"]["stop_pct"] < VB_TF_PARAMS["monthly"]["stop_pct"]


class TestVolumeBreakoutRsi:
    """Tests for the _rsi() helper in volume_breakout_bot."""

    def _closes(self, n: int = 80, drift: float = 0.002, seed: int = 7) -> pd.Series:
        rng = np.random.default_rng(seed)
        rets = rng.normal(drift, 0.012, n)
        return pd.Series(np.cumprod(1 + rets) * 400.0)

    def test_rsi_length_matches_input(self):
        closes = self._closes()
        rsi = vb_rsi(closes)
        assert len(rsi) == len(closes)

    def test_rsi_bounded_0_to_100(self):
        closes = self._closes()
        valid = vb_rsi(closes).dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_rsi_rising_market_above_50(self):
        """Strong uptrend → RSI should be > 50 most of the time."""
        closes = self._closes(drift=0.015, n=60)
        rsi_last = vb_rsi(closes).iloc[-1]
        assert rsi_last > 50

    def test_rsi_falling_market_below_50(self):
        closes = self._closes(drift=-0.015, n=60)
        rsi_last = vb_rsi(closes).iloc[-1]
        assert rsi_last < 50


class TestVolumeBreakoutBotMeta:
    def test_bot_id(self):
        assert VolumeBreakoutBot.BOT_ID == "volume_breakout_bot"

    def test_stop_less_than_target(self):
        assert VolumeBreakoutBot.DEFAULT_STOP_PCT < VolumeBreakoutBot.DEFAULT_TARGET_PCT

    def test_max_hold_days_positive(self):
        assert VolumeBreakoutBot.MAX_HOLD_DAYS > 0

    def test_capital_nrs_is_10_lakhs(self):
        assert VolumeBreakoutBot.CAPITAL_NRS == 1_000_000.0


# ═══════════════════════════════════════════════════════════════════════════════
# Momentum Bot
# ═══════════════════════════════════════════════════════════════════════════════

class TestMomentumScore:
    """Tests for _momentum_score()."""

    # ── baseline scenario (daily params) ──────────────────────────────────────
    _RSI_NOW    = 60.0    # inside 50-72
    _RSI_PREV   = 48.0    # was below 50 → fresh RSI cross
    _HIST_NOW   = 0.5     # MACD hist positive
    _HIST_PREV  = -0.2    # was negative → fresh MACD cross
    _CLOSE      = 510.0   # above BB mid
    _BB_MID     = 500.0
    _VOL_RATIO  = 1.5     # above 1.2×
    _RSI_MIN    = 50.0    # daily
    _RSI_MAX    = 72.0
    _VOL_MIN    = 1.2

    def _score(self, **kw) -> Optional[float]:
        args = dict(
            rsi_now      = self._RSI_NOW,
            rsi_prev     = self._RSI_PREV,
            hist_now     = self._HIST_NOW,
            hist_prev    = self._HIST_PREV,
            close        = self._CLOSE,
            bb_mid       = self._BB_MID,
            volume_ratio = self._VOL_RATIO,
            rsi_min      = self._RSI_MIN,
            rsi_max      = self._RSI_MAX,
            vol_min      = self._VOL_MIN,
        )
        args.update(kw)
        return _momentum_score(**args)

    # ── happy path ─────────────────────────────────────────────────────────

    def test_happy_path_returns_score(self):
        score = self._score()
        assert score is not None
        assert 50.0 <= score <= 100.0

    def test_score_type_is_float(self):
        score = self._score()
        assert isinstance(score, float)

    def test_score_max_100(self):
        """Cannot exceed 100 regardless of inputs."""
        score = self._score(
            rsi_now=71.0, rsi_prev=40.0,
            hist_now=10.0, hist_prev=-0.5,
            volume_ratio=5.0,
        )
        assert score is not None
        assert score <= 100.0

    # ── gate: RSI range ────────────────────────────────────────────────────

    def test_rsi_below_min_returns_none(self):
        score = self._score(rsi_now=49.0)  # below daily 50
        assert score is None

    def test_rsi_above_max_returns_none(self):
        score = self._score(rsi_now=73.0)  # above daily 72
        assert score is None

    def test_rsi_exactly_at_min_passes(self):
        score = self._score(rsi_now=50.0)
        assert score is not None

    def test_rsi_exactly_at_max_passes(self):
        score = self._score(rsi_now=72.0)
        assert score is not None

    # ── gate: MACD histogram must be positive ─────────────────────────────

    def test_negative_macd_hist_returns_none(self):
        score = self._score(hist_now=-0.1)
        assert score is None

    def test_zero_macd_hist_returns_none(self):
        """hist_now <= 0 → reject."""
        score = self._score(hist_now=0.0)
        assert score is None

    def test_positive_macd_hist_passes(self):
        score = self._score(hist_now=0.001)
        assert score is not None

    def test_fresh_macd_cross_gives_higher_score(self):
        """hist turns positive (prev<0, now>0) → 15-pt bonus vs sustained positive."""
        fresh_cross  = self._score(hist_prev=-0.5, hist_now=0.1)
        sustained    = self._score(hist_prev=0.3,  hist_now=0.5)
        assert fresh_cross > sustained

    # ── gate: price above BB mid ───────────────────────────────────────────

    def test_price_below_bb_mid_returns_none(self):
        score = self._score(close=499.0, bb_mid=500.0)
        assert score is None

    def test_price_exactly_at_bb_mid_passes(self):
        """close < bb_mid → reject, but close == bb_mid satisfies the strict
        'close < bb_mid' guard (False when equal) so the gate does NOT fire."""
        score = self._score(close=500.0, bb_mid=500.0)
        # The guard is `if close < bb_mid: return None` — equality is NOT blocked
        assert score is not None

    def test_price_above_bb_mid_passes(self):
        score = self._score(close=501.0, bb_mid=500.0)
        assert score is not None

    def test_more_above_bb_mid_gives_higher_score(self):
        lo = self._score(close=501.0, bb_mid=500.0)
        hi = self._score(close=510.0, bb_mid=500.0)
        assert hi >= lo

    # ── gate: volume ratio ─────────────────────────────────────────────────

    def test_volume_below_min_returns_none(self):
        score = self._score(volume_ratio=1.1)  # below daily 1.2×
        assert score is None

    def test_volume_exactly_at_min_passes(self):
        score = self._score(volume_ratio=1.2)
        assert score is not None

    def test_higher_volume_gives_higher_score(self):
        lo = self._score(volume_ratio=1.3)
        hi = self._score(volume_ratio=2.5)
        assert hi > lo

    # ── RSI momentum bonus ─────────────────────────────────────────────────

    def test_rsi_cross_from_below_min_gives_bonus(self):
        """rsi_prev < rsi_min (was outside range) → +10 bonus."""
        with_cross    = self._score(rsi_prev=48.0)   # prev < 50 → bonus
        without_cross = self._score(rsi_prev=55.0)   # prev already in range
        assert with_cross > without_cross

    # ── timeframe parameter set correctness ───────────────────────────────

    @pytest.mark.parametrize("tf,macd_fast,macd_slow,macd_sig", [
        ("daily",   12,  26, 9),
        ("weekly",  26,  52, 18),
        ("monthly", 52, 104, 36),
    ])
    def test_macd_params_per_timeframe(self, tf, macd_fast, macd_slow, macd_sig):
        p = MOM_TF_PARAMS[tf]
        assert p["macd_fast"] == macd_fast
        assert p["macd_slow"] == macd_slow
        assert p["macd_sig"]  == macd_sig

    @pytest.mark.parametrize("tf,bb_period", [
        ("daily",   20),
        ("weekly",  40),
        ("monthly", 80),
    ])
    def test_bb_period_per_timeframe(self, tf, bb_period):
        assert MOM_TF_PARAMS[tf]["bb_period"] == bb_period

    @pytest.mark.parametrize("tf", ["daily", "weekly", "monthly"])
    def test_stop_less_than_target_per_timeframe(self, tf):
        p = MOM_TF_PARAMS[tf]
        assert p["stop_pct"] < p["target_pct"]

    def test_macd_periods_scale_with_timeframe(self):
        """monthly MACD periods must be > weekly > daily (wider lookback)."""
        assert MOM_TF_PARAMS["daily"]["macd_fast"]  < MOM_TF_PARAMS["weekly"]["macd_fast"]
        assert MOM_TF_PARAMS["weekly"]["macd_fast"] < MOM_TF_PARAMS["monthly"]["macd_fast"]


class TestMacdHistHelper:
    """Tests for _macd_hist() in momentum_bot."""

    def _series(self, n: int = 100, drift: float = 0.003, seed: int = 3) -> pd.Series:
        rng = np.random.default_rng(seed)
        rets = rng.normal(drift, 0.01, n)
        return pd.Series(np.cumprod(1 + rets) * 300.0)

    def test_length_matches_input(self):
        s = self._series()
        hist = _macd_hist(s, 12, 26, 9)
        assert len(hist) == len(s)

    def test_uptrend_macd_mostly_positive(self):
        """On a strong uptrend, the last MACD histogram value is typically > 0."""
        s = self._series(drift=0.015, n=80)
        hist = _macd_hist(s, 12, 26, 9)
        # At least the final value should be positive in a strong uptrend
        assert float(hist.iloc[-1]) > 0

    def test_downtrend_macd_crosses_zero_downward(self):
        """After a sharp sustained downtrend, MACD hist is negative at the end.
        Use a perfectly linear declining series so there is no random variance."""
        # Linearly declining: 300 → 100 over 100 bars — deterministically bearish
        s = pd.Series(np.linspace(300.0, 100.0, 100))
        hist = _macd_hist(s, 12, 26, 9)
        assert float(hist.iloc[-1]) < 0


class TestBollingerHelper:
    """Tests for _bollinger() in momentum_bot."""

    def test_shapes(self):
        closes = pd.Series(np.linspace(100, 200, 60))
        mid, upper, lower = _bollinger(closes, period=20)
        assert len(mid) == len(closes)
        assert len(upper) == len(closes)
        assert len(lower) == len(closes)

    def test_upper_above_mid(self):
        closes = pd.Series(np.linspace(100, 200, 60))
        mid, upper, lower = _bollinger(closes, period=20)
        valid = ~mid.isna()
        assert (upper[valid] >= mid[valid]).all()

    def test_lower_below_mid(self):
        closes = pd.Series(np.linspace(100, 200, 60))
        mid, upper, lower = _bollinger(closes, period=20)
        valid = ~mid.isna()
        assert (mid[valid] >= lower[valid]).all()

    def test_wider_std_gives_wider_bands(self):
        closes = pd.Series(np.linspace(100, 200, 60))
        _, upper1, lower1 = _bollinger(closes, std=1.0)
        _, upper2, lower2 = _bollinger(closes, std=3.0)
        # Last valid row: band with std=3 should be wider
        last = ~upper1.isna()
        idx  = last[last].index[-1]
        assert upper2.iloc[idx] >= upper1.iloc[idx]
        assert lower2.iloc[idx] <= lower1.iloc[idx]


class TestMomentumBotMeta:
    def test_bot_id(self):
        assert MomentumBot.BOT_ID == "momentum_bot"

    def test_strategy(self):
        assert MomentumBot.STRATEGY == "momentum"

    def test_stop_less_than_target(self):
        assert MomentumBot.DEFAULT_STOP_PCT < MomentumBot.DEFAULT_TARGET_PCT

    def test_max_hold_days_positive(self):
        assert MomentumBot.MAX_HOLD_DAYS > 0

    def test_capital_nrs_is_10_lakhs(self):
        assert MomentumBot.CAPITAL_NRS == 1_000_000.0
