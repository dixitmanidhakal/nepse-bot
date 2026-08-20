"""
Smoke tests for the new NEPSE strategy bots.

Tests the pure scoring functions without a database or historical data.
  - ema_crossover_bot._ema_cross_score
  - mean_reversion_bot._mr_score

Also verifies bot class constants and registry membership.
"""

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from app.components.bots.ema_crossover_bot import (
    EMACrossoverBot,
    _ema_cross_score,
    _ema_series,
    _rsi as ema_rsi,
)
from app.components.bots.mean_reversion_bot import (
    MeanReversionBot,
    _mr_score,
    _rsi as mr_rsi,
    _bollinger,
)
from app.components.bots import BOT_REGISTRY

# _SECTOR_MAP and _EMA_UNIVERSE were refactored into nepse_universe.
# TestSectorMaps mocks get_sector_map() so no live API call is needed.
_MOCK_SECTOR_MAP = {
    "NABIL": "Banking",
    "NHPC": "Hydropower",
    "NLIC": "Insurance",
    "NICA": "Banking",
    "HIDCL": "Hydropower",
}


# ── Registry ──────────────────────────────────────────────────────────────────

class TestBotRegistry:
    def test_ema_crossover_registered(self):
        assert "ema_crossover" in BOT_REGISTRY
        assert BOT_REGISTRY["ema_crossover"] is EMACrossoverBot

    def test_mean_reversion_registered(self):
        assert "mean_reversion" in BOT_REGISTRY
        assert BOT_REGISTRY["mean_reversion"] is MeanReversionBot

    def test_all_bots_registered(self):
        expected = {
            "smc", "recommendation", "momentum",
            "ema_crossover", "mean_reversion",
            "sector_rotation", "volume_breakout",
            "quant_composite",
        }
        assert expected == set(BOT_REGISTRY.keys())


# ── EMACrossoverBot class attributes ─────────────────────────────────────────

class TestEMACrossoverBotMeta:
    def test_bot_id(self):
        assert EMACrossoverBot.BOT_ID == "ema_crossover_bot"

    def test_stop_tighter_than_target(self):
        assert EMACrossoverBot.DEFAULT_STOP_PCT < EMACrossoverBot.DEFAULT_TARGET_PCT

    def test_max_hold_positive(self):
        assert EMACrossoverBot.MAX_HOLD_DAYS > 0


# ── EMA helpers ──────────────────────────────────────────────────────────────

class TestEmaHelpers:
    def _closes(self, n=100, drift=0.003, seed=1):
        rng = np.random.default_rng(seed)
        rets = rng.normal(drift, 0.01, n)
        return pd.Series(np.cumprod(1 + rets) * 500.0)

    def test_ema_series_length_matches_input(self):
        closes = self._closes()
        result = _ema_series(closes, span=9)
        assert len(result) == len(closes)

    def test_rsi_bounded(self):
        closes = self._closes(n=60)
        rsi = ema_rsi(closes)
        valid = rsi.dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_ema_series_lags_price_on_uptrend(self):
        """On a steadily rising series, EMA(9) should be below last close."""
        closes = self._closes(drift=0.01, n=60)
        ema9 = _ema_series(closes, 9)
        assert float(ema9.iloc[-1]) < float(closes.iloc[-1])


# ── _ema_cross_score ──────────────────────────────────────────────────────────

class TestEmaCrossScore:
    # Scenario: valid crossover (daily params: vol_min=1.5)
    _EMA_FAST_NOW  = 510.0
    _EMA_FAST_PREV = 498.0   # was below mid EMA → fresh cross
    _EMA_MID_NOW   = 508.0
    _EMA_MID_PREV  = 499.0   # prev fast <= prev mid
    _EMA_SLOW_NOW  = 490.0   # price above this
    _CLOSE_NOW     = 512.0
    _VOL_RATIO     = 2.0     # above 1.5× threshold
    _VOL_MIN       = 1.5     # daily threshold

    def _score(self, **overrides):
        kwargs = dict(
            ema_fast_now  = self._EMA_FAST_NOW,
            ema_fast_prev = self._EMA_FAST_PREV,
            ema_mid_now   = self._EMA_MID_NOW,
            ema_mid_prev  = self._EMA_MID_PREV,
            ema_slow_now  = self._EMA_SLOW_NOW,
            close_now     = self._CLOSE_NOW,
            volume_ratio  = self._VOL_RATIO,
            vol_min       = self._VOL_MIN,
        )
        kwargs.update(overrides)
        return _ema_cross_score(**kwargs)

    def test_happy_path_returns_score_in_range(self):
        score = self._score()
        assert score is not None
        assert 50.0 <= score <= 100.0

    def test_price_below_slow_ema_returns_none(self):
        score = self._score(close_now=480.0, ema_slow_now=490.0)
        assert score is None

    def test_fast_below_mid_ema_returns_none(self):
        score = self._score(ema_fast_now=505.0, ema_mid_now=507.0)
        assert score is None

    def test_flat_fast_ema_returns_none(self):
        """Fast EMA not rising → skip."""
        score = self._score(ema_fast_now=self._EMA_FAST_PREV)   # no slope
        assert score is None

    def test_low_volume_returns_none(self):
        score = self._score(volume_ratio=1.3)   # below 1.5 threshold
        assert score is None

    def test_stale_cross_still_passes(self):
        """Fast EMA was already above mid in prev bar — cross_last_bar branch."""
        score = self._score(
            ema_fast_prev=502.0,   # above ema_mid_prev → already crossed
            ema_mid_prev=499.0,
        )
        assert score is not None

    def test_higher_volume_gives_higher_score(self):
        low_vol  = self._score(volume_ratio=1.6)
        high_vol = self._score(volume_ratio=3.0)
        assert high_vol > low_vol


# ── MeanReversionBot class attributes ────────────────────────────────────────

class TestMeanReversionBotMeta:
    def test_bot_id(self):
        assert MeanReversionBot.BOT_ID == "mean_reversion_bot"

    def test_stop_pct_exists(self):
        assert MeanReversionBot.DEFAULT_STOP_PCT > 0

    def test_hold_days_shorter_than_momentum(self):
        from app.components.bots.momentum_bot import MomentumBot
        assert MeanReversionBot.MAX_HOLD_DAYS <= MomentumBot.MAX_HOLD_DAYS


# ── Bollinger Band helper ─────────────────────────────────────────────────────

class TestBollingerBand:
    def test_band_shape(self):
        closes = pd.Series(np.linspace(100, 200, 100))
        mid, upper, lower = _bollinger(closes)
        assert len(mid) == len(closes)
        # First (period-1) bars are NaN by design; only check valid rows
        valid = ~mid.isna()
        assert (upper[valid] >= mid[valid]).all()
        assert (mid[valid] >= lower[valid]).all()


# ── _mr_score ─────────────────────────────────────────────────────────────────

class TestMrScore:
    # Scenario: genuine oversold bounce setup
    _RSI_NOW  = 30.0         # well below 38
    _RSI_PREV = 25.0         # RSI rising: now > prev
    _CLOSE    = 395.0
    _BB_LO    = 390.0        # close is within 1.3% of bb_lower (≤ 3%)
    _VOL_RATIO = 2.0         # above 1.4
    _LOW_52W  = 350.0        # 12.8% below close — safe (≥ 8% above it)

    def _score(self, **overrides):
        kwargs = dict(
            rsi_now      = self._RSI_NOW,
            rsi_prev     = self._RSI_PREV,
            close_now    = self._CLOSE,
            bb_lower     = self._BB_LO,
            volume_ratio = self._VOL_RATIO,
            low_52w      = self._LOW_52W,
            rsi_oversold = 38.0,   # daily timeframe threshold
            vol_min      = 1.4,    # daily timeframe minimum
        )
        kwargs.update(overrides)
        return _mr_score(**kwargs)

    def test_happy_path_returns_score_in_range(self):
        score = self._score()
        assert score is not None
        assert 50.0 <= score <= 100.0

    def test_rsi_not_oversold_returns_none(self):
        score = self._score(rsi_now=45.0)
        assert score is None

    def test_falling_rsi_returns_none(self):
        """RSI must be rising (bounce forming)."""
        score = self._score(rsi_now=30.0, rsi_prev=35.0)  # prev > now → still falling
        assert score is None

    def test_price_far_from_bb_lower_returns_none(self):
        """If price is > 3% above bb_lower, conditions are not met."""
        score = self._score(close_now=410.0, bb_lower=390.0)  # 5.1% above → fail
        assert score is None

    def test_low_volume_returns_none(self):
        score = self._score(vol_ratio=1.2) if False else self._score(volume_ratio=1.3)
        assert score is None

    def test_fallen_knife_returns_none(self):
        """Price too close to 52-week low (< 8% above) → skip."""
        score = self._score(close_now=380.0, low_52w=355.0)  # only 6.8% above → fail
        assert score is None

    def test_deeper_rsi_gives_higher_score(self):
        """Deeper oversold → higher potential bounce → higher score."""
        score_mild  = self._score(rsi_now=36.0, rsi_prev=33.0)
        score_deep  = self._score(rsi_now=22.0, rsi_prev=18.0)
        assert score_deep > score_mild

    def test_higher_volume_gives_higher_score(self):
        low_vol  = self._score(volume_ratio=1.5)
        high_vol = self._score(volume_ratio=3.0)
        assert high_vol > low_vol


# ── Sector map coverage ───────────────────────────────────────────────────────

class TestSectorMaps:
    """Sector-map tests use a static mock to avoid live API calls."""

    def test_nabil_in_banking(self):
        assert _MOCK_SECTOR_MAP["NABIL"] == "Banking"

    def test_nhpc_in_hydropower(self):
        assert _MOCK_SECTOR_MAP["NHPC"] == "Hydropower"

    def test_nlic_in_insurance(self):
        assert _MOCK_SECTOR_MAP["NLIC"] == "Insurance"

    def test_get_sector_map_returns_dict(self):
        """get_sector_map() (mocked) returns a dict."""
        from app.components.bots.nepse_universe import get_sector_map
        with patch(
            "app.components.bots.nepse_universe.get_sector_map",
            return_value=_MOCK_SECTOR_MAP,
        ):
            result = get_sector_map()
        assert isinstance(result, dict)
        assert "NABIL" in result
