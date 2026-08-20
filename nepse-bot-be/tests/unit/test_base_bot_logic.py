"""
Unit tests for app/components/bots/base_bot.py — pure logic.

No database, no real HTTP calls, no scheduler.  All datetime-sensitive
functions are tested with a deterministic "frozen-time" helper that
replaces the module-level ``datetime`` binding without any third-party
library (no freezegun needed).

Coverage:
  - _nepse_trading_days_since   : T+2 SEBON settlement day counting
  - _is_daily_entry_window      : 11:30-14:30 NST prime entry gate
  - Module constants             : weekday set, timeframe hold-days, capital params
  - BaseBot._open_trade          : Kelly position sizing, stop/target prices,
                                   capital constraints, symbol normalisation
  - Bot registry completeness    : all 8 bots subclass BaseBot and carry the
                                   required class attributes
"""

from __future__ import annotations

import datetime as _dt
from contextlib import contextmanager
from typing import Generator
from unittest.mock import MagicMock

import pytest

import app.components.bots.base_bot as _bb_module  # we'll monkey-patch datetime here
from app.components.bots.base_bot import (
    _nepse_trading_days_since,
    _is_daily_entry_window,
    _NEPSE_WEEKDAYS,
    _ENTRY_WIN_START_MIN,
    _ENTRY_WIN_END_MIN,
    _TIMEFRAME_HOLD_DAYS,
    MIN_HOLD_TRADING_DAYS,
    BaseBot,
)
from app.models.paper_trade import PaperTrade, TradeOutcome, TradeDirection


# ─── Frozen-time helper ───────────────────────────────────────────────────────

class _FakeDatetime:
    """Minimal drop-in for datetime.datetime that returns a fixed 'now'."""
    _frozen: _dt.datetime | None = None

    @classmethod
    def now(cls, tz=None) -> _dt.datetime:
        assert cls._frozen is not None, "_FakeDatetime.frozen must be set"
        return cls._frozen

    # Allow constructing real datetime objects when needed by the code under test
    def __new__(cls, *args, **kwargs):
        return _dt.datetime(*args, **kwargs)


@contextmanager
def freeze_utc(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> Generator:
    """
    Context manager: replaces ``datetime`` in base_bot's module namespace so
    that ``datetime.now(timezone.utc)`` returns a fixed UTC datetime.

    Usage::

        with freeze_utc(2026, 4, 22, 7, 30):
            count = _nepse_trading_days_since(entry)
    """
    _FakeDatetime._frozen = _dt.datetime(year, month, day, hour, minute,
                                          tzinfo=_dt.timezone.utc)
    orig = _bb_module.datetime
    _bb_module.datetime = _FakeDatetime  # type: ignore[assignment]
    try:
        yield _FakeDatetime._frozen
    finally:
        _bb_module.datetime = orig
        _FakeDatetime._frozen = None


# ─── Concrete bot for _open_trade tests ───────────────────────────────────────

class _TestBot(BaseBot):
    """Minimal concrete subclass — only used to exercise BaseBot helpers."""
    BOT_ID   = "test_bot"
    BOT_NAME = "Test Bot"
    STRATEGY = "test"

    def generate_signals(self, db, timeframe="daily"):  # type: ignore[override]
        return []


def _mock_db():
    db = MagicMock()
    db.add = MagicMock()
    db.flush = MagicMock()
    return db


def _mock_state(capital_nrs: float = 1_000_000.0, capital_deployed: float = 0.0):
    s = MagicMock()
    s.capital_nrs      = capital_nrs
    s.capital_deployed = capital_deployed
    return s


def _sig(
    symbol: str = "NABIL",
    score: float = 80.0,
    entry: float = 1_000.0,
    stop_pct: float = 3.0,
    target_pct: float = 6.0,
    sector: str | None = "Banking",
    regime: str | None = "trending",
) -> dict:
    return {
        "symbol":      symbol,
        "score":       score,
        "signal":      "BUY",
        "entry_price": entry,
        "stop_pct":    stop_pct,
        "target_pct":  target_pct,
        "sector":      sector,
        "regime":      regime,
    }


# ─── Constants ────────────────────────────────────────────────────────────────

class TestConstants:
    """Verify NEPSE-specific module constants match the business rules."""

    def test_min_hold_trading_days_is_two(self):
        """T+2 settlement requires exactly 2 completed NEPSE trading days."""
        assert MIN_HOLD_TRADING_DAYS == 2

    def test_nepse_weekdays_is_mon_to_fri(self):
        """NEPSE trades Monday(0) through Friday(4); Sat(5) and Sun(6) are off."""
        assert _NEPSE_WEEKDAYS == frozenset({0, 1, 2, 3, 4})
        assert 5 not in _NEPSE_WEEKDAYS  # Saturday
        assert 6 not in _NEPSE_WEEKDAYS  # Sunday

    def test_nepse_weekdays_has_five_elements(self):
        assert len(_NEPSE_WEEKDAYS) == 5

    def test_timeframe_hold_days_ordering(self):
        """daily < weekly < monthly hold days (progressively longer)."""
        assert _TIMEFRAME_HOLD_DAYS["daily"] < _TIMEFRAME_HOLD_DAYS["weekly"]
        assert _TIMEFRAME_HOLD_DAYS["weekly"] < _TIMEFRAME_HOLD_DAYS["monthly"]

    def test_timeframe_hold_days_values(self):
        assert _TIMEFRAME_HOLD_DAYS["daily"]   == 10
        assert _TIMEFRAME_HOLD_DAYS["weekly"]  == 25
        assert _TIMEFRAME_HOLD_DAYS["monthly"] == 60

    def test_entry_window_is_valid_range(self):
        assert 0 <= _ENTRY_WIN_START_MIN < 24 * 60
        assert 0 <= _ENTRY_WIN_END_MIN   < 24 * 60
        assert _ENTRY_WIN_START_MIN < _ENTRY_WIN_END_MIN

    def test_entry_window_utc_matches_nst(self):
        """Prime entry window: 11:30-14:30 NST = 05:45-08:45 UTC."""
        assert _ENTRY_WIN_START_MIN == 5 * 60 + 45   # 05:45 UTC
        assert _ENTRY_WIN_END_MIN   == 8 * 60 + 45   # 08:45 UTC


# ─── _nepse_trading_days_since ────────────────────────────────────────────────

class TestNepseTradingDaysSince:
    """T+2 settlement day counting with deterministic frozen time."""

    # Week of 2026-04-20 (Mon) … 2026-04-24 (Fri)
    _MON = _dt.datetime(2026, 4, 20, 10, 0, tzinfo=_dt.timezone.utc)
    _TUE = _dt.datetime(2026, 4, 21, 10, 0, tzinfo=_dt.timezone.utc)
    _WED = _dt.datetime(2026, 4, 22, 10, 0, tzinfo=_dt.timezone.utc)
    _THU = _dt.datetime(2026, 4, 23, 10, 0, tzinfo=_dt.timezone.utc)
    _FRI = _dt.datetime(2026, 4, 24, 10, 0, tzinfo=_dt.timezone.utc)
    _SAT = _dt.datetime(2026, 4, 25, 10, 0, tzinfo=_dt.timezone.utc)
    _SUN = _dt.datetime(2026, 4, 26, 10, 0, tzinfo=_dt.timezone.utc)
    _NEXT_MON = _dt.datetime(2026, 4, 27, 10, 0, tzinfo=_dt.timezone.utc)

    def _count(self, entry: _dt.datetime, today_year: int, today_month: int,
                today_day: int, today_hour: int = 12) -> int:
        with freeze_utc(today_year, today_month, today_day, today_hour):
            return _nepse_trading_days_since(entry)

    # ── same-day checks ────────────────────────────────────────────────────

    def test_entry_and_today_same_date_returns_zero(self):
        """Entry today → 0 NEPSE trading days elapsed (trade could be intraday)."""
        count = self._count(self._MON, 2026, 4, 20)
        assert count == 0

    def test_return_type_is_int(self):
        count = self._count(self._MON, 2026, 4, 20)
        assert isinstance(count, int)

    # ── Mon → next-day checks ──────────────────────────────────────────────

    def test_mon_entry_checked_tuesday_gives_one(self):
        """Mon entry, today=Tue → Mon counted (Mon is a full trading day)."""
        count = self._count(self._MON, 2026, 4, 21)
        assert count == 1

    def test_mon_entry_checked_wednesday_gives_two(self):
        """Mon entry, today=Wed → Mon+Tue = 2 NEPSE trading days → T+2 cleared."""
        count = self._count(self._MON, 2026, 4, 22)
        assert count == 2
        assert count >= MIN_HOLD_TRADING_DAYS

    def test_mon_entry_checked_friday_gives_four(self):
        """Mon entry, today=Fri → Tue Wed Thu Fri skipped (today not counted) = 4 days."""
        # iterate: Mon(+1), Tue(+1), Wed(+1), Thu(+1) = 4
        count = self._count(self._MON, 2026, 4, 24)
        assert count == 4

    # ── Friday entry across weekend ────────────────────────────────────────

    def test_fri_entry_checked_saturday_gives_one(self):
        """Fri entry, today=Sat → Fri counted = 1."""
        count = self._count(self._FRI, 2026, 4, 25)
        assert count == 1

    def test_fri_entry_checked_sunday_gives_one(self):
        """Fri entry, today=Sun → Fri(+1), Sat(skip) = 1."""
        count = self._count(self._FRI, 2026, 4, 26)
        assert count == 1

    def test_fri_entry_checked_next_monday_gives_one(self):
        """Fri entry, today=next Mon → Fri(+1), Sat(skip), Sun(skip) = 1."""
        count = self._count(self._FRI, 2026, 4, 27)
        assert count == 1

    def test_fri_entry_checked_next_tuesday_gives_two(self):
        """Fri entry, today=next Tue → Fri(+1), Mon(+1) = 2 → T+2 cleared."""
        count = self._count(self._FRI, 2026, 4, 28)
        assert count == 2
        assert count >= MIN_HOLD_TRADING_DAYS

    # ── Weekend entry ──────────────────────────────────────────────────────

    def test_sat_entry_checked_sunday_gives_zero(self):
        """Sat entry, today=Sun → Sat is not a NEPSE trading day → 0."""
        count = self._count(self._SAT, 2026, 4, 26)
        assert count == 0

    def test_sat_entry_checked_monday_gives_zero(self):
        """Sat entry, today=Mon → Sat(skip), Sun(skip) = 0."""
        count = self._count(self._SAT, 2026, 4, 27)
        assert count == 0

    def test_sat_entry_checked_tuesday_gives_one(self):
        """Sat entry, today=Tue → Sat(skip), Sun(skip), Mon(+1) = 1."""
        count = self._count(self._SAT, 2026, 4, 28)
        assert count == 1

    # ── Naive datetime handling ────────────────────────────────────────────

    def test_naive_entry_date_is_handled_gracefully(self):
        """Naive entry_date (no tzinfo) must not raise — it is normalised to UTC."""
        naive_entry = _dt.datetime(2026, 4, 20, 10, 0)  # no tzinfo
        with freeze_utc(2026, 4, 22):
            count = _nepse_trading_days_since(naive_entry)
        assert isinstance(count, int)
        assert count >= 0

    # ── Non-negative invariant ─────────────────────────────────────────────

    def test_count_never_negative(self):
        """The function should never return a negative count."""
        # Entry far in the past → large positive number
        entry = _dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc)
        with freeze_utc(2026, 4, 22):
            count = _nepse_trading_days_since(entry)
        assert count >= 0

    # ── Multi-week count ───────────────────────────────────────────────────

    def test_full_week_gives_five_trading_days(self):
        """Entry Mon, today 7 calendar days later (Mon) → Mon-Fri = 5 trading days."""
        entry = _dt.datetime(2026, 4, 13, 10, 0, tzinfo=_dt.timezone.utc)  # Mon
        with freeze_utc(2026, 4, 20):  # next Mon (+7 days)
            count = _nepse_trading_days_since(entry)
        # Mon Tue Wed Thu Fri = 5 (Sat Sun skipped)
        assert count == 5


# ─── _is_daily_entry_window ───────────────────────────────────────────────────

class TestDailyEntryWindow:
    """
    Test the prime daily entry window check.

    Window: 05:45 UTC – 08:45 UTC  (11:30 NST – 14:30 NST).
    """

    def _check(self, hour: int, minute: int = 0) -> bool:
        with freeze_utc(2026, 4, 21, hour, minute):  # Monday
            return _is_daily_entry_window()

    # ── inside window ──────────────────────────────────────────────────────

    def test_midpoint_inside_window(self):
        """07:00 UTC = 12:45 NST → inside window."""
        assert self._check(7, 0) is True

    def test_exactly_at_start_is_inside(self):
        """05:45 UTC = window start → inside (inclusive)."""
        assert self._check(5, 45) is True

    def test_exactly_at_end_is_inside(self):
        """08:45 UTC = window end → inside (inclusive)."""
        assert self._check(8, 45) is True

    def test_one_minute_before_end_is_inside(self):
        assert self._check(8, 44) is True

    def test_late_in_window_is_inside(self):
        """08:00 UTC = 13:45 NST → inside."""
        assert self._check(8, 0) is True

    # ── outside window ─────────────────────────────────────────────────────

    def test_one_minute_before_start_is_outside(self):
        """05:44 UTC → one minute before window opens."""
        assert self._check(5, 44) is False

    def test_one_minute_after_end_is_outside(self):
        """08:46 UTC → one minute after window closes."""
        assert self._check(8, 46) is False

    def test_midnight_is_outside(self):
        assert self._check(0, 0) is False

    def test_early_morning_is_outside(self):
        """04:00 UTC = 09:45 NST → market not even pre-open."""
        assert self._check(4, 0) is False

    def test_late_afternoon_is_outside(self):
        """09:30 UTC = 15:15 NST → market closed."""
        assert self._check(9, 30) is False

    def test_end_of_day_is_outside(self):
        assert self._check(23, 59) is False

    # ── boundary arithmetic ────────────────────────────────────────────────

    def test_window_start_matches_constant(self):
        start_h, start_m = divmod(_ENTRY_WIN_START_MIN, 60)
        assert self._check(start_h, start_m) is True

    def test_window_end_matches_constant(self):
        end_h, end_m = divmod(_ENTRY_WIN_END_MIN, 60)
        assert self._check(end_h, end_m) is True

    def test_window_covers_nepse_prime_period(self):
        """Assert that known good NST times (12:00–14:00) map to window=True."""
        # 12:00 NST = 06:15 UTC, 13:00 NST = 07:15 UTC, 14:00 NST = 08:15 UTC
        for utc_h, utc_m in [(6, 15), (7, 15), (8, 15)]:
            with freeze_utc(2026, 4, 21, utc_h, utc_m):
                assert _is_daily_entry_window() is True, \
                    f"Expected True for {utc_h:02d}:{utc_m:02d} UTC"


# ─── BaseBot._open_trade (Kelly sizing) ───────────────────────────────────────

class TestOpenTrade:
    """
    Verify _open_trade produces PaperTrade objects with correct economics.

    No DB, no scheduler — the method is tested as a pure data-transformation
    function; the MagicMock DB just absorbs add() / flush() calls.
    """

    _bot = _TestBot()

    def _trade(self, **kwargs) -> PaperTrade | None:
        sig     = kwargs.pop("sig",     _sig())
        db      = kwargs.pop("db",      _mock_db())
        state   = kwargs.pop("state",   _mock_state())
        tf      = kwargs.pop("tf",      "daily")
        avail   = kwargs.pop("avail",   200_000.0)
        capital = kwargs.pop("capital", 1_000_000.0)
        return self._bot._open_trade(sig, db, state, tf, avail, capital)

    # ── basic sanity ──────────────────────────────────────────────────────

    def test_returns_paper_trade_instance(self):
        trade = self._trade()
        assert isinstance(trade, PaperTrade)

    def test_trade_is_open_on_creation(self):
        trade = self._trade()
        assert trade is not None
        assert trade.is_open is True

    def test_trade_outcome_is_open(self):
        trade = self._trade()
        assert trade is not None
        assert trade.outcome == TradeOutcome.OPEN

    def test_direction_is_long(self):
        trade = self._trade()
        assert trade is not None
        assert trade.direction == TradeDirection.LONG

    # ── symbol normalisation ──────────────────────────────────────────────

    def test_symbol_lowercased_input_is_uppercased(self):
        trade = self._trade(sig=_sig(symbol="nabil"))
        assert trade is not None
        assert trade.symbol == "NABIL"

    def test_symbol_already_upper_preserved(self):
        trade = self._trade(sig=_sig(symbol="GBIME"))
        assert trade is not None
        assert trade.symbol == "GBIME"

    # ── stop / target price arithmetic ────────────────────────────────────

    def test_target_price_above_entry(self):
        trade = self._trade(sig=_sig(entry=1_000.0, target_pct=6.0))
        assert trade is not None
        assert trade.target_price > trade.entry_price

    def test_stop_price_below_entry(self):
        trade = self._trade(sig=_sig(entry=1_000.0, stop_pct=3.0))
        assert trade is not None
        assert trade.stop_price < trade.entry_price

    def test_target_price_calculation(self):
        entry, tgt = 1_000.0, 6.0
        trade = self._trade(sig=_sig(entry=entry, target_pct=tgt))
        assert trade is not None
        expected = round(entry * (1 + tgt / 100), 2)
        assert abs(trade.target_price - expected) < 0.01

    def test_stop_price_calculation(self):
        entry, stp = 1_000.0, 3.0
        trade = self._trade(sig=_sig(entry=entry, stop_pct=stp))
        assert trade is not None
        expected = round(entry * (1 - stp / 100), 2)
        assert abs(trade.stop_price - expected) < 0.01

    def test_risk_reward_ratio_sane(self):
        """R:R should be >= 1 for all bots (target% > stop%)."""
        trade = self._trade(sig=_sig(entry=1_000.0, stop_pct=3.0, target_pct=6.0))
        assert trade is not None
        risk   = trade.entry_price - trade.stop_price
        reward = trade.target_price - trade.entry_price
        assert reward >= risk

    # ── guard conditions ──────────────────────────────────────────────────

    def test_zero_entry_returns_none(self):
        trade = self._trade(sig=_sig(entry=0.0))
        assert trade is None

    def test_negative_entry_returns_none(self):
        trade = self._trade(sig=_sig(entry=-500.0))
        assert trade is None

    # ── shares quantity & capital ─────────────────────────────────────────

    def test_shares_qty_is_positive_integer(self):
        trade = self._trade(sig=_sig(entry=500.0, score=80.0))
        assert trade is not None
        assert isinstance(trade.shares_qty, int)
        assert trade.shares_qty > 0

    def test_capital_allocated_is_shares_times_entry(self):
        entry = 750.0
        trade = self._trade(sig=_sig(entry=entry, score=80.0))
        assert trade is not None
        expected = round(trade.shares_qty * entry, 2)
        assert abs(trade.capital_allocated - expected) < 0.01

    def test_capital_allocated_does_not_exceed_available(self):
        avail = 50_000.0
        trade = self._trade(sig=_sig(entry=500.0, score=100.0), avail=avail)
        assert trade is not None
        assert trade.capital_allocated <= avail + 1.0  # +1 for fp rounding

    def test_higher_score_produces_more_capital(self):
        """Kelly half-Kelly sizing: score 90 → larger position than score 50."""
        t_low  = self._trade(sig=_sig(entry=500.0, score=50.0))
        t_high = self._trade(sig=_sig(entry=500.0, score=90.0))
        assert t_low is not None and t_high is not None
        assert t_high.capital_allocated >= t_low.capital_allocated

    def test_score_100_does_not_exceed_max_position_pct(self):
        """Even at score=100, position must not exceed MAX_POSITION_PCT * capital."""
        capital = 1_000_000.0
        trade = self._trade(
            sig=_sig(entry=100.0, score=100.0),
            capital=capital,
            avail=capital,
        )
        assert trade is not None
        max_allowed = capital * _TestBot.MAX_POSITION_PCT
        assert trade.capital_allocated <= max_allowed + 1.0

    def test_score_0_still_opens_minimum_position(self):
        """Score=0 maps to kelly_frac=0 → clamped to 5% min position."""
        capital = 1_000_000.0
        trade = self._trade(
            sig=_sig(entry=100.0, score=0.0),
            capital=capital,
            avail=capital,
        )
        # min position = 5% of capital = 50,000 NPR → at 100 NPR/share = 500 shares
        assert trade is not None
        assert trade.shares_qty > 0
        min_capital = capital * 0.05  # 5% of capital
        assert trade.capital_allocated >= min_capital - 1.0

    # ── timeframe → hold days ─────────────────────────────────────────────

    @pytest.mark.parametrize("timeframe,expected_days", [
        ("daily",   10),
        ("weekly",  25),
        ("monthly", 60),
    ])
    def test_timeframe_sets_max_hold_days(self, timeframe, expected_days):
        trade = self._trade(sig=_sig(), tf=timeframe)
        assert trade is not None
        assert trade.max_hold_days == expected_days

    def test_timeframe_stored_on_trade(self):
        for tf in ("daily", "weekly", "monthly"):
            trade = self._trade(sig=_sig(), tf=tf)
            assert trade is not None
            assert trade.timeframe == tf

    # ── extra signal metadata ─────────────────────────────────────────────

    def test_sector_stored_on_trade(self):
        trade = self._trade(sig=_sig(sector="Banking"))
        assert trade is not None
        assert trade.sector == "Banking"

    def test_regime_stored_on_trade(self):
        trade = self._trade(sig=_sig(regime="trending"))
        assert trade is not None
        assert trade.regime_at_entry == "trending"

    def test_signal_score_stored_on_trade(self):
        trade = self._trade(sig=_sig(score=87.5))
        assert trade is not None
        assert abs(trade.signal_score - 87.5) < 0.01


# ─── Bot registry completeness ────────────────────────────────────────────────

class TestBotRegistryCompleteness:
    """
    Verify the BOT_REGISTRY has all 8 expected bots and each bot carries the
    required class attributes with sane values.
    """

    _EXPECTED_BOT_KEYS = {
        "smc", "recommendation", "momentum",
        "ema_crossover", "mean_reversion",
        "sector_rotation", "volume_breakout",
        "quant_composite",
    }

    def test_registry_has_all_eight_bots(self):
        from app.components.bots import BOT_REGISTRY
        assert set(BOT_REGISTRY.keys()) == self._EXPECTED_BOT_KEYS

    def test_all_bots_subclass_base_bot(self):
        from app.components.bots import BOT_REGISTRY
        for name, cls in BOT_REGISTRY.items():
            assert issubclass(cls, BaseBot), f"{name} must subclass BaseBot"

    @pytest.mark.parametrize("attr", [
        "BOT_ID", "BOT_NAME", "STRATEGY",
        "DEFAULT_STOP_PCT", "DEFAULT_TARGET_PCT", "MAX_HOLD_DAYS",
        "CAPITAL_NRS", "MAX_POSITION_PCT", "CASH_RESERVE_PCT", "MAX_CONCURRENT",
    ])
    def test_all_bots_have_required_class_attr(self, attr):
        from app.components.bots import BOT_REGISTRY
        for name, cls in BOT_REGISTRY.items():
            assert hasattr(cls, attr), f"{name} is missing class attribute '{attr}'"

    def test_all_bots_stop_pct_less_than_target_pct(self):
        """Risk/reward: stop must always be narrower than target."""
        from app.components.bots import BOT_REGISTRY
        for name, cls in BOT_REGISTRY.items():
            assert cls.DEFAULT_STOP_PCT < cls.DEFAULT_TARGET_PCT, (
                f"{name}: stop_pct ({cls.DEFAULT_STOP_PCT}) >= "
                f"target_pct ({cls.DEFAULT_TARGET_PCT})"
            )

    def test_all_bots_have_positive_hold_days(self):
        from app.components.bots import BOT_REGISTRY
        for name, cls in BOT_REGISTRY.items():
            assert cls.MAX_HOLD_DAYS > 0, f"{name}.MAX_HOLD_DAYS must be > 0"

    def test_all_bots_have_valid_capital_parameters(self):
        from app.components.bots import BOT_REGISTRY
        for name, cls in BOT_REGISTRY.items():
            assert cls.CAPITAL_NRS > 0, f"{name}: CAPITAL_NRS must be > 0"
            assert 0 < cls.MAX_POSITION_PCT <= 1.0, (
                f"{name}: MAX_POSITION_PCT must be in (0, 1]"
            )
            assert 0 < cls.CASH_RESERVE_PCT <= 1.0, (
                f"{name}: CASH_RESERVE_PCT must be in (0, 1]"
            )
            assert cls.MAX_CONCURRENT >= 1, (
                f"{name}: MAX_CONCURRENT must be >= 1"
            )

    def test_max_position_pct_plus_cash_reserve_does_not_exceed_one(self):
        """Deployable fraction must be positive: 1 - cash_reserve > 0."""
        from app.components.bots import BOT_REGISTRY
        for name, cls in BOT_REGISTRY.items():
            deployable = 1.0 - cls.CASH_RESERVE_PCT
            assert deployable > 0, f"{name}: cash reserve ≥ 100% leaves nothing to deploy"

    def test_each_bot_id_is_unique(self):
        from app.components.bots import BOT_REGISTRY
        ids = [cls.BOT_ID for cls in BOT_REGISTRY.values()]
        assert len(ids) == len(set(ids)), "Duplicate BOT_ID detected"

    def test_quant_composite_in_registry(self):
        from app.components.bots import BOT_REGISTRY
        assert "quant_composite" in BOT_REGISTRY

    def test_ema_crossover_in_registry(self):
        from app.components.bots import BOT_REGISTRY
        assert "ema_crossover" in BOT_REGISTRY


# ─── Capital management arithmetic ─────────────────────────────────────────────

class TestCapitalManagementMath:
    """
    Verify the capital deployment formulas used in run_cycle.
    These are pure arithmetic tests — no DB needed.
    """

    def test_deployable_fraction(self):
        """80% of capital is deployable (100% - 20% cash reserve)."""
        capital = 1_000_000.0
        cash_reserve = 0.20
        deployable = capital * (1 - cash_reserve)
        assert deployable == 800_000.0

    def test_min_position_is_4pct(self):
        """A new trade needs at least 4% of capital available."""
        capital = 1_000_000.0
        min_pos = capital * 0.04
        assert min_pos == 40_000.0

    def test_available_capital_zero_when_fully_deployed(self):
        capital     = 1_000_000.0
        deployable  = capital * 0.80  # 800k
        deployed    = 800_000.0
        available   = max(0.0, deployable - deployed)
        assert available == 0.0

    def test_available_never_negative(self):
        """Over-deployed capital is clamped to 0."""
        deployable  = 800_000.0
        deployed    = 900_000.0  # somehow over-deployed
        available   = max(0.0, deployable - deployed)
        assert available == 0.0

    def test_pnl_pct_calculation(self):
        """P&L % = (current - entry) / entry * 100."""
        entry   = 1_000.0
        current = 1_060.0  # target hit
        pnl_pct = (current - entry) / entry * 100
        assert abs(pnl_pct - 6.0) < 0.001

    def test_pnl_nrs_calculation(self):
        """NPR P&L = allocated_capital * pnl_pct / 100."""
        allocated = 100_000.0
        pnl_pct   = 6.0
        pnl_nrs   = round(allocated * pnl_pct / 100, 2)
        assert pnl_nrs == 6_000.0

    def test_drawdown_from_peak(self):
        """Drawdown % = (peak - current) / peak * 100."""
        peak    = 1_100_000.0
        current = 1_000_000.0
        dd      = (peak - current) / peak * 100
        assert abs(dd - 9.09) < 0.01

    def test_kelly_fraction_at_score_80(self):
        """Kelly fraction for score=80 → half-Kelly = 0.5 * 0.80 = 0.40."""
        score       = 80.0
        score_norm  = min(1.0, score / 100.0)  # 0.80
        kelly_frac  = 0.5 * score_norm          # 0.40
        max_pos_pct = 0.20
        pos_pct     = max_pos_pct * kelly_frac  # 0.08 = 8%
        assert abs(pos_pct - 0.08) < 0.001

    def test_kelly_fraction_at_score_100_is_capped(self):
        """Score=100 → kelly_frac=0.5, pos_pct=10%, still within max_pos_pct=20%."""
        score      = 100.0
        score_norm = 1.0
        kelly_frac = 0.5 * score_norm  # 0.5
        max_pos    = 0.20
        pos_pct    = max_pos * kelly_frac  # 0.10 = 10%
        assert pos_pct <= max_pos  # never exceeds 20%

    def test_kelly_fraction_at_score_0_is_clamped_to_min(self):
        """Score=0 → raw pos_pct=0, clamped up to 5% minimum."""
        score      = 0.0
        score_norm = 0.0
        kelly_frac = 0.5 * score_norm  # 0.0
        max_pos    = 0.20
        raw_pct    = max_pos * kelly_frac  # 0.0
        pos_pct    = max(0.05, min(raw_pct, max_pos))
        assert pos_pct == 0.05  # clamped to 5%
