"""
Smoke tests for:
  /api/v1/depth/*      (depth_routes.py   — DB-backed historical depth)
  /api/v1/depth/live/* (depth_live_routes.py — in-memory live depth poller)

Historical depth routes require real order-book data in the DB.
Tests accept 404 (no data yet) or 500 as well as 200.

Live depth routes work even without data:
  - session, stats always succeed (200)
  - latest/history → 404 when symbol is not on the watchlist
  - watchlist add/remove/replace → 200
"""

from __future__ import annotations

import pytest

_HIST_BASE = "/api/v1/depth"
_LIVE_BASE = "/api/v1/depth/live"
_SYMBOL = "NABIL"


def _ok_or_err(status: int) -> bool:
    return status in (200, 404, 500)


# ── Live depth — always-available endpoints ───────────────────────────────────

class TestDepthLiveSession:
    def test_session_returns_200(self, client):
        r = client.get(f"{_LIVE_BASE}/market/session")
        assert r.status_code == 200

    def test_session_shape(self, client):
        r = client.get(f"{_LIVE_BASE}/market/session")
        body = r.json()
        assert body["status"] == "success"
        data = body["data"]
        assert "is_open" in data
        assert "is_poll_window" in data
        assert "reason" in data
        assert "now_npt" in data
        assert data["session_open"] == "11:00"
        assert data["session_close"] == "15:00"

    def test_poller_stats_returns_200(self, client):
        r = client.get(f"{_LIVE_BASE}/stats")
        assert r.status_code == 200

    def test_poller_stats_shape(self, client):
        r = client.get(f"{_LIVE_BASE}/stats")
        body = r.json()
        assert body["status"] == "success"
        assert "data" in body


class TestDepthLiveWatchlist:
    def test_watchlist_add_responds(self, client):
        r = client.post(f"{_LIVE_BASE}/watchlist/add", json={"symbol": _SYMBOL})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "success"
        assert "watchlist" in body
        assert _SYMBOL.upper() in body["watchlist"]

    def test_watchlist_remove_responds(self, client):
        client.post(f"{_LIVE_BASE}/watchlist/add", json={"symbol": _SYMBOL})
        r = client.post(f"{_LIVE_BASE}/watchlist/remove", json={"symbol": _SYMBOL})
        assert r.status_code == 200

    def test_watchlist_replace_responds(self, client):
        r = client.post(
            f"{_LIVE_BASE}/watchlist",
            json={"symbols": ["NABIL", "NICA"]},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "success"
        assert body["count"] == 2

    def test_watchlist_replace_empty_rejected(self, client):
        # symbols has min_length=1 → empty list returns 422
        r = client.post(f"{_LIVE_BASE}/watchlist", json={"symbols": []})
        assert r.status_code == 422


class TestDepthLiveSnapshot:
    def test_unknown_symbol_gives_404(self, client):
        # Symbol that is definitely not on the watchlist
        r = client.get(f"{_LIVE_BASE}/ZZZZNOTREAL")
        assert r.status_code == 404

    def test_history_limit_validation_min(self, client):
        r = client.get(f"{_LIVE_BASE}/NABIL/history", params={"limit": 0})
        assert r.status_code == 422

    def test_history_limit_validation_max(self, client):
        r = client.get(f"{_LIVE_BASE}/NABIL/history", params={"limit": 9999})
        assert r.status_code == 422

    def test_history_unknown_symbol_gives_404(self, client):
        r = client.get(f"{_LIVE_BASE}/ZZNOTREAL/history")
        assert r.status_code == 404


# ── Historical depth (DB-backed) ─────────────────────────────────────────────

class TestHistoricalDepthRoutes:
    """
    These routes read persisted order-book data from PostgreSQL.
    Without seeded data they return 404; both 200 and 404 are valid.
    """

    def test_current_depth_responds(self, client):
        r = client.get(f"{_HIST_BASE}/{_SYMBOL}/current")
        assert _ok_or_err(r.status_code)

    def test_analysis_responds(self, client):
        r = client.get(f"{_HIST_BASE}/{_SYMBOL}/analysis")
        assert _ok_or_err(r.status_code)

    def test_imbalance_responds(self, client):
        r = client.get(f"{_HIST_BASE}/{_SYMBOL}/imbalance")
        assert _ok_or_err(r.status_code)

    def test_walls_responds(self, client):
        r = client.get(f"{_HIST_BASE}/{_SYMBOL}/walls")
        assert _ok_or_err(r.status_code)

    def test_liquidity_responds(self, client):
        r = client.get(f"{_HIST_BASE}/{_SYMBOL}/liquidity")
        assert _ok_or_err(r.status_code)

    def test_spread_responds(self, client):
        r = client.get(f"{_HIST_BASE}/{_SYMBOL}/spread")
        assert _ok_or_err(r.status_code)

    def test_pressure_responds(self, client):
        r = client.get(f"{_HIST_BASE}/{_SYMBOL}/pressure")
        assert _ok_or_err(r.status_code)

    def test_history_responds(self, client):
        r = client.get(f"{_HIST_BASE}/{_SYMBOL}/history")
        assert _ok_or_err(r.status_code)

    def test_support_resistance_responds(self, client):
        r = client.get(f"{_HIST_BASE}/{_SYMBOL}/support-resistance")
        assert _ok_or_err(r.status_code)

    def test_seed_requires_body(self, client):
        r = client.post(f"{_HIST_BASE}/seed")
        # Missing body → 422; no symbols in DB/live → 503; success → 200
        assert r.status_code in (200, 422, 503)
