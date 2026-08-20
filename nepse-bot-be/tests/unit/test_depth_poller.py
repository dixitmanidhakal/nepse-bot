"""Unit tests for the depth poller (ring buffer + failover orchestration)."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.services.data import depth_live_sources as lives
from app.services.data.depth_poller import DepthPoller, DepthRingBuffer


# --------------------------------------------------------------------------- #
# Ring buffer                                                                  #
# --------------------------------------------------------------------------- #

class TestDepthRingBuffer:
    @pytest.mark.asyncio
    async def test_push_and_latest(self):
        buf = DepthRingBuffer(size=3)
        await buf.push("AAA", {"source": "test", "i": 1})
        await buf.push("AAA", {"source": "test", "i": 2})
        latest = buf.latest("AAA")
        assert latest is not None
        assert latest.payload["i"] == 2

    @pytest.mark.asyncio
    async def test_maxlen(self):
        buf = DepthRingBuffer(size=2)
        for i in range(5):
            await buf.push("BBB", {"i": i})
        history = buf.history("BBB", limit=100)
        assert len(history) == 2
        assert [h.payload["i"] for h in history] == [3, 4]

    def test_unknown_symbol_returns_none(self):
        buf = DepthRingBuffer()
        assert buf.latest("ZZZ") is None
        assert buf.history("ZZZ") == []


# --------------------------------------------------------------------------- #
# Watchlist                                                                    #
# --------------------------------------------------------------------------- #

class TestPollerWatchlist:
    @pytest.mark.asyncio
    async def test_set_and_add_remove(self):
        p = DepthPoller()
        await p.set_watchlist(["aaa", "BBB"])
        assert p.watchlist() == ["AAA", "BBB"]
        await p.add("ccc")
        assert "CCC" in p.watchlist()
        await p.remove("aaa")
        assert "AAA" not in p.watchlist()


# --------------------------------------------------------------------------- #
# Failover orchestration                                                       #
# --------------------------------------------------------------------------- #

class TestFailover:
    def test_first_source_wins(self):
        fake = {
            "symbol": "NICA",
            "buy_orders": [{"price": 550, "quantity": 100, "orders": 1}],
            "sell_orders": [{"price": 551, "quantity": 200, "orders": 2}],
            "source": "chukul",
        }
        with patch.object(lives, "fetch_chukul", return_value=fake), \
             patch.object(lives, "fetch_nepsealpha", return_value=None), \
             patch.object(lives, "fetch_nepse_unofficial", return_value=None), \
             patch.object(lives, "fetch_merolagani", return_value=None):
            snap = lives.fetch_live_depth("NICA")
        assert snap is not None
        assert snap["source"] == "chukul"
        assert "order_imbalance" in snap
        assert "bid_ask_spread_percent" in snap
        assert snap["sources_tried"] == ["chukul"]

    def test_falls_through_to_nepsealpha(self):
        fake = {
            "symbol": "NICA",
            "buy_orders": [{"price": 550, "quantity": 100, "orders": 1}],
            "sell_orders": [{"price": 551, "quantity": 200, "orders": 2}],
            "source": "nepsealpha",
        }
        with patch.object(lives, "fetch_chukul", return_value=None), \
             patch.object(lives, "fetch_nepsealpha", return_value=fake), \
             patch.object(lives, "fetch_nepse_unofficial", return_value=None), \
             patch.object(lives, "fetch_merolagani", return_value=None):
            snap = lives.fetch_live_depth("NICA")
        assert snap is not None
        assert snap["source"] == "nepsealpha"
        assert snap["sources_tried"] == ["chukul", "nepsealpha"]

    def test_all_sources_fail(self):
        with patch.object(lives, "fetch_chukul", return_value=None), \
             patch.object(lives, "fetch_nepsealpha", return_value=None), \
             patch.object(lives, "fetch_nepse_unofficial", return_value=None), \
             patch.object(lives, "fetch_merolagani", return_value=None):
            assert lives.fetch_live_depth("XXX") is None

    def test_source_raising_does_not_break_chain(self):
        fake = {
            "symbol": "NICA",
            "buy_orders": [{"price": 550, "quantity": 100}],
            "sell_orders": [{"price": 551, "quantity": 200}],
            "source": "nepsealpha",
        }
        def _raise(_s):
            raise RuntimeError("boom")
        with patch.object(lives, "fetch_chukul", side_effect=_raise), \
             patch.object(lives, "fetch_nepsealpha", return_value=fake), \
             patch.object(lives, "fetch_nepse_unofficial", return_value=None), \
             patch.object(lives, "fetch_merolagani", return_value=None):
            snap = lives.fetch_live_depth("NICA")
        assert snap is not None
        assert snap["source"] == "nepsealpha"


# --------------------------------------------------------------------------- #
# Depth metrics                                                                #
# --------------------------------------------------------------------------- #

class TestDepthMetrics:
    def test_bullish_imbalance_and_wall(self):
        snap = {
            "symbol": "NICA",
            "buy_orders": [
                {"price": 550, "quantity": 5000, "orders": 3},  # wall
                {"price": 549, "quantity": 200, "orders": 1},
                {"price": 548, "quantity": 200, "orders": 1},
                {"price": 547, "quantity": 200, "orders": 1},
                {"price": 546, "quantity": 200, "orders": 1},
            ],
            "sell_orders": [
                {"price": 551, "quantity": 100, "orders": 1},
                {"price": 552, "quantity": 100, "orders": 1},
                {"price": 553, "quantity": 100, "orders": 1},
            ],
            "source": "test",
        }
        m = lives._depth_metrics(snap)
        assert m["has_bid_wall"] is True
        assert m["has_ask_wall"] is False
        assert m["order_imbalance"] > 0.5
        assert m["bid_ask_spread_percent"] > 0.0


# --------------------------------------------------------------------------- #
# depth_for_engine formatter                                                   #
# --------------------------------------------------------------------------- #

class TestDepthForEngine:
    @pytest.mark.asyncio
    async def test_omits_stale_entries(self):
        p = DepthPoller()
        await p.buffer.push("AAA", {"source": "test", "has_bid_wall": True})
        # Force stale by patching monotonic offset via _stats — simpler: set
        # the snapshot's received_at to far in the past.
        snap = p.buffer.latest("AAA")
        snap.received_at -= 10_000
        out = p.depth_for_engine(["AAA"])
        # Outside market hours in the test environment it will be marked stale.
        assert "AAA" not in out
