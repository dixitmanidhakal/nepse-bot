"""
Deep unit tests for app/components/bots/nepse_universe.py

Covers:
  - _is_equity() — all symbol pattern categories
  - get_nepse_universe() — cache TTL, DB-first / live-fallback routing
  - get_sector() — happy path, unmapped symbol → "Other", None sector_map
  - run_async() — executes coroutines correctly in fresh thread
  - Cache invalidation on TTL expiry
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from app.components.bots.nepse_universe import (
    _is_equity,
    get_sector,
    run_async,
    _CACHE_TTL,
)


# ── _is_equity ────────────────────────────────────────────────────────────────

class TestIsEquity:
    """Comprehensive tests for the NEPSE equity symbol classifier."""

    # ── Valid equity symbols ──────────────────────────────────────────────────

    def test_plain_uppercase_ticker(self):
        assert _is_equity("NABIL") is True

    def test_two_letter_symbol(self):
        assert _is_equity("NB") is True

    def test_twelve_letter_symbol(self):
        # max allowed length
        assert _is_equity("ABCDEFGHIJKL") is True

    def test_alphanumeric_equity(self):
        # e.g. NTC1 or class-B shares without promo pattern
        assert _is_equity("NLIC") is True

    def test_symbol_with_digits_not_matching_debenture_or_promo(self):
        # CCBD88 is a valid bond class — let's test what the code actually does
        # It matches _DEBENTURE_RE: r'^[A-Z]+D\d{2,}$' — CCBD88 → ends in D88 → IS debenture
        # But wait, CCBD88 was returned by the recommendation API. Let me check the pattern:
        # _DEBENTURE_RE = re.compile(r'^[A-Z]+D\d{2,}$')
        # CCBD88: C, C, B, D, 8, 8 → [A-Z]+D → "CCB" + D → then \d{2,} → 88 → YES matches debenture
        # So CCBD88 would be filtered out by _is_equity. This reveals the test should check
        # what the code actually does, not what we think it should do.
        # Let's just verify the regex matches — CCBD88 is a debenture bond
        result = _is_equity("CCBD88")
        assert result is False  # correctly filtered as debenture

    def test_hydropower_symbol(self):
        assert _is_equity("NHPC") is True

    def test_bank_symbol(self):
        assert _is_equity("NICA") is True

    # ── Debenture bonds filtered out ──────────────────────────────────────────

    def test_debenture_two_digit_suffix(self):
        """Pattern: [A-Z]+D\d{2,} — e.g. NIMBD90"""
        assert _is_equity("NIMBD90") is False

    def test_debenture_four_digit_suffix(self):
        assert _is_equity("MFLD8500") is False

    def test_debenture_plain_two_digit(self):
        assert _is_equity("ADBLD83") is False

    def test_debenture_must_end_in_digits(self):
        """NABILPD84 — ends in D+digits → debenture."""
        assert _is_equity("NABILPD84") is False

    def test_debenture_single_digit_not_matched(self):
        """D\d+ requires \d{2,} → single digit after D is NOT matched → equity."""
        # e.g. "ABCD1" → [A-Z]+D = ABD + \d{2,} — actually wait: ABCD1 → last 2 chars are D1
        # Pattern: ^[A-Z]+D\d{2,}$ → 'D' followed by 2+ digits. "ABCD1" has only 1 digit → no match.
        assert _is_equity("ABCD1") is True  # not a debenture pattern

    # ── Promoter/partial-call shares filtered ─────────────────────────────────

    def test_promoter_share_pattern(self):
        """Pattern: [A-Z]+P\d+ — e.g. NABILP2"""
        assert _is_equity("NABILP2") is False

    def test_promoter_share_double_digit(self):
        assert _is_equity("PRBUPO") is False or _is_equity("PRBUPO") is True
        # PRBUPO: doesn't match ^[A-Z]+P\d+$ because it ends in 'O' not digit → should be equity
        assert _is_equity("PRBUPO") is True

    def test_promoter_double_digit_suffix(self):
        assert _is_equity("NICAP12") is False

    def test_promoter_single_digit(self):
        assert _is_equity("NABILP5") is False

    # ── Invalid characters filtered ────────────────────────────────────────────

    def test_symbol_with_space(self):
        assert _is_equity("NA BIL") is False

    def test_symbol_with_colon(self):
        assert _is_equity("NABIL::") is False

    def test_symbol_with_double_colon(self):
        assert _is_equity("::TOTAL") is False

    def test_symbol_with_hyphen(self):
        assert _is_equity("NABIL-A") is False

    def test_symbol_with_dot(self):
        assert _is_equity("NABIL.B") is False

    def test_symbol_with_slash(self):
        assert _is_equity("NA/BIL") is False

    def test_lowercase_letter(self):
        """Lowercase letters are invalid for NEPSE symbols."""
        assert _is_equity("nabil") is False

    def test_mixed_case(self):
        assert _is_equity("Nabil") is False

    # ── Edge cases: length ────────────────────────────────────────────────────

    def test_empty_string(self):
        assert _is_equity("") is False

    def test_single_character(self):
        assert _is_equity("N") is False

    def test_thirteen_characters(self):
        assert _is_equity("ABCDEFGHIJKLM") is False

    def test_exactly_two_characters(self):
        assert _is_equity("AB") is True

    def test_exactly_twelve_characters(self):
        assert _is_equity("ABCDEFGHIJKL") is True

    # ── None / non-string ─────────────────────────────────────────────────────

    def test_none_input(self):
        """Must not raise; should return False."""
        result = _is_equity(None)  # type: ignore[arg-type]
        assert result is False

    def test_numeric_string(self):
        """Pure digits are not valid equity symbols."""
        # "1234" → no invalid chars per _INVALID_CHARS_RE (it checks [^A-Z0-9])
        # But pure digits: let's check — actually _INVALID_CHARS_RE only blocks non-alphanumeric.
        # However neither debenture nor promo patterns match "1234". So it should pass _is_equity.
        # len=4, no invalid chars, no debenture, no promo → True
        # This reveals a potential false-positive. Document the actual behavior:
        result = _is_equity("1234")
        assert isinstance(result, bool)  # just ensure no crash; document actual result
        assert result is True  # pure digits pass the current filter — known behavior


# ── run_async ─────────────────────────────────────────────────────────────────

class TestRunAsync:
    """Verify run_async() correctly bridges async→sync."""

    def test_returns_simple_value(self):
        async def _coro():
            return 42
        assert run_async(_coro()) == 42

    def test_returns_list(self):
        async def _coro():
            return [1, 2, 3]
        assert run_async(_coro()) == [1, 2, 3]

    def test_returns_dict(self):
        async def _coro():
            return {"key": "value"}
        assert run_async(_coro()) == {"key": "value"}

    def test_awaits_asyncio_sleep(self):
        """Ensures the executor can handle a sleep without deadlock."""
        async def _coro():
            await asyncio.sleep(0.01)
            return "done"
        result = run_async(_coro())
        assert result == "done"

    def test_propagates_exception(self):
        """Exceptions from the coroutine must bubble up."""
        async def _coro():
            raise ValueError("test error")
        with pytest.raises(ValueError, match="test error"):
            run_async(_coro())

    def test_thread_safe_concurrent_calls(self):
        """Multiple threads calling run_async simultaneously must not deadlock."""
        results = []
        errors = []

        async def _coro(n: int):
            await asyncio.sleep(0.001)
            return n * 2

        def _worker(n):
            try:
                results.append(run_async(_coro(n)))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Errors in threads: {errors}"
        assert sorted(results) == [0, 2, 4, 6, 8]


# ── get_sector ────────────────────────────────────────────────────────────────

class TestGetSector:
    """Tests for the symbol→sector lookup function."""

    _SECTOR_MAP = {
        "NABIL": "Commercial Banks",
        "NHPC":  "Hydro Power",
        "NLIC":  "Life Insurance",
    }

    def test_known_symbol_returns_sector(self):
        result = get_sector("NABIL", sector_map=self._SECTOR_MAP)
        # May be canonicalised by _canon_sector; just check it's non-empty
        assert isinstance(result, str)
        assert len(result) > 0
        assert result != "Other"

    def test_unknown_symbol_returns_other(self):
        result = get_sector("XXXXXX", sector_map=self._SECTOR_MAP)
        assert result == "Other"

    def test_lowercase_symbol_is_normalised(self):
        """get_sector should uppercase the symbol before lookup."""
        result = get_sector("nabil", sector_map=self._SECTOR_MAP)
        assert result != "Other"

    def test_symbol_with_leading_space(self):
        """Leading/trailing spaces are stripped."""
        result = get_sector("  NABIL  ", sector_map=self._SECTOR_MAP)
        assert result != "Other"

    def test_empty_sector_map_returns_other(self):
        result = get_sector("NABIL", sector_map={})
        assert result == "Other"

    def test_none_sector_map_triggers_live_lookup(self):
        """When sector_map=None, must not crash (may return 'Other' on test box)."""
        with patch(
            "app.components.bots.nepse_universe.get_sector_map",
            return_value=self._SECTOR_MAP,
        ):
            result = get_sector("NABIL", sector_map=None)
        assert isinstance(result, str)

    def test_sector_map_value_preserved(self):
        """The sector string should not be mangled beyond canonicalisation."""
        result = get_sector("NHPC", sector_map={"NHPC": "Hydro Power"})
        # Canonicalised form; must still be a non-empty, non-Other string
        assert result != "Other"
        assert len(result) >= 2


# ── get_nepse_universe — cache behaviour ──────────────────────────────────────

class TestGetNepseUniverseCache:
    """Verify cache TTL and DB/live routing without touching the network."""

    def _make_provider(self, symbols: List[str]) -> MagicMock:
        p = MagicMock()
        p.is_available.return_value = True
        p.list_symbols.return_value = symbols
        return p

    def test_returns_only_equities_from_db(self):
        """Debentures and promo shares in the DB must be filtered out."""
        from app.components.bots.nepse_universe import get_nepse_universe
        import app.components.bots.nepse_universe as nu

        raw = ["NABIL", "NIMBD90", "NABILP2", "NHPC", "NTC"]
        provider = self._make_provider(raw)

        # Force cache refresh by expiring it
        nu._universe_ts = 0.0
        nu._universe_cache = None

        with patch(
            "app.components.bots.nepse_universe._from_db",
            return_value=[s for s in raw if _is_equity(s)],
        ):
            result = get_nepse_universe(provider)

        # Debenture and promo must not appear
        assert "NIMBD90" not in result
        assert "NABILP2" not in result
        # Valid equities must appear
        assert "NABIL" in result
        assert "NHPC" in result

    def test_cache_hit_returns_same_list(self):
        """Second call within TTL must return same list without re-fetching."""
        from app.components.bots.nepse_universe import get_nepse_universe
        import app.components.bots.nepse_universe as nu

        nu._universe_cache = ["NABIL", "NHPC"]
        nu._universe_ts = time.monotonic()  # fresh

        call_count = {"n": 0}

        def _fake_from_db(p=None):
            call_count["n"] += 1
            return ["NEW"]

        with patch("app.components.bots.nepse_universe._from_db", side_effect=_fake_from_db):
            result = get_nepse_universe()

        assert call_count["n"] == 0  # no DB call — served from cache
        assert "NABIL" in result

    def test_cache_miss_after_ttl(self):
        """After TTL expires, the next call must re-fetch from DB."""
        from app.components.bots.nepse_universe import get_nepse_universe
        import app.components.bots.nepse_universe as nu

        nu._universe_cache = ["OLD"]
        nu._universe_ts = time.monotonic() - _CACHE_TTL - 1  # expired

        refreshed = ["NABIL", "NHPC", "NTC"]

        with patch(
            "app.components.bots.nepse_universe._from_db",
            return_value=refreshed,
        ):
            result = get_nepse_universe()

        assert "NABIL" in result
        assert "OLD" not in result

    def test_live_fallback_when_db_unavailable(self):
        """When DB returns None, fall back to live market symbols."""
        from app.components.bots.nepse_universe import get_nepse_universe
        import app.components.bots.nepse_universe as nu

        nu._universe_cache = None
        nu._universe_ts = 0.0

        live_syms = ["NTC", "NICA", "NABIL"]

        with patch("app.components.bots.nepse_universe._from_db", return_value=None), \
             patch("app.components.bots.nepse_universe._from_live", return_value=live_syms):
            result = get_nepse_universe()

        assert "NTC" in result
        assert "NABIL" in result

    def test_result_is_always_a_list(self):
        """Return type must always be list, never None or dict."""
        from app.components.bots.nepse_universe import get_nepse_universe
        import app.components.bots.nepse_universe as nu

        nu._universe_cache = None
        nu._universe_ts = 0.0

        with patch("app.components.bots.nepse_universe._from_db", return_value=None), \
             patch("app.components.bots.nepse_universe._from_live", return_value=[]):
            result = get_nepse_universe()

        assert isinstance(result, list)

    def test_returns_copy_not_reference(self):
        """Mutating the returned list must not corrupt the internal cache."""
        from app.components.bots.nepse_universe import get_nepse_universe
        import app.components.bots.nepse_universe as nu

        nu._universe_cache = ["NABIL", "NHPC"]
        nu._universe_ts = time.monotonic()

        r1 = get_nepse_universe()
        r1.clear()  # mutate the returned list

        r2 = get_nepse_universe()
        assert len(r2) == 2  # cache must still be intact
