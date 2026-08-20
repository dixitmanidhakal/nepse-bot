"""
Smoke tests for the paper-trading bot API routes.

GET  /api/v1/bots/                → list bots
GET  /api/v1/bots/{id}/state      → single bot RL state
GET  /api/v1/bots/{id}/trades     → open + recent closed trades
GET  /api/v1/bots/{id}/trades/history  → closed trade history
GET  /api/v1/bots/summary         → aggregate stats
POST /api/v1/bots/{id}/reset      → reset learning state

Notes:
  - POST /bots/{id}/run and POST /bots/run-all require the historical SQLite
    DB and are skipped when it is not configured.
  - All GET routes work with just a live PostgreSQL connection (bot states
    are auto-created on first access via get_or_create_state).
"""

from __future__ import annotations

import pytest


# ── Known bot IDs from _BOT_META in bot_routes.py ────────────────────────────
_KNOWN_BOT_IDS = [
    "smc_bot", "reco_bot", "momentum_bot",
    "ema_crossover_bot", "mean_reversion_bot",
    "sector_rotation_bot", "volume_breakout_bot",
]


class TestListBots:
    def test_list_bots_returns_200(self, client):
        r = client.get("/api/v1/bots/")
        assert r.status_code == 200

    def test_list_bots_shape(self, client):
        r = client.get("/api/v1/bots/")
        body = r.json()
        assert "count" in body
        assert "bots" in body
        assert isinstance(body["bots"], list)

    def test_list_bots_count_matches_list(self, client):
        r = client.get("/api/v1/bots/")
        body = r.json()
        assert body["count"] == len(body["bots"])

    def test_each_bot_has_required_keys(self, client):
        r = client.get("/api/v1/bots/")
        for bot in r.json()["bots"]:
            assert "id" in bot
            assert "name" in bot
            assert "strategy" in bot
            assert "learning_state" in bot
            assert "open_positions" in bot

    def test_learning_state_has_accuracy_and_threshold(self, client):
        r = client.get("/api/v1/bots/")
        for bot in r.json()["bots"]:
            ls = bot["learning_state"]
            assert "rolling_accuracy" in ls
            assert "current_threshold" in ls
            assert "total_trades" in ls
            assert 0.0 <= ls["rolling_accuracy"] <= 1.0
            assert 70.0 <= ls["current_threshold"] <= 100.0

    def test_learning_state_has_new_count_fields(self, client):
        """sector_counts and regime_counts must be present after session 5 migration."""
        r = client.get("/api/v1/bots/")
        for bot in r.json()["bots"]:
            ls = bot["learning_state"]
            assert "sector_counts" in ls
            assert "regime_counts" in ls


class TestGetBotState:
    @pytest.mark.parametrize("bot_id", _KNOWN_BOT_IDS)
    def test_state_returns_200(self, client, bot_id):
        r = client.get(f"/api/v1/bots/{bot_id}/state")
        assert r.status_code == 200

    @pytest.mark.parametrize("bot_id", _KNOWN_BOT_IDS)
    def test_state_shape(self, client, bot_id):
        r = client.get(f"/api/v1/bots/{bot_id}/state")
        body = r.json()
        assert body["bot_id"] == bot_id
        state = body["state"]
        assert "rolling_accuracy" in state
        assert "current_threshold" in state
        assert "mistakes_log" in state

    def test_unknown_bot_returns_404(self, client):
        r = client.get("/api/v1/bots/nonexistent_bot/state")
        assert r.status_code == 404


class TestGetBotTrades:
    @pytest.mark.parametrize("bot_id", _KNOWN_BOT_IDS)
    def test_trades_returns_200(self, client, bot_id):
        r = client.get(f"/api/v1/bots/{bot_id}/trades")
        assert r.status_code == 200

    @pytest.mark.parametrize("bot_id", _KNOWN_BOT_IDS)
    def test_trades_shape(self, client, bot_id):
        """Routes returns {bot_id, count, trades: [...]}"""
        r = client.get(f"/api/v1/bots/{bot_id}/trades")
        body = r.json()
        assert "bot_id" in body
        assert "count" in body
        assert "trades" in body
        assert isinstance(body["trades"], list)
        assert body["count"] == len(body["trades"])

    @pytest.mark.parametrize("bot_id", _KNOWN_BOT_IDS)
    def test_trades_history_returns_200(self, client, bot_id):
        r = client.get(f"/api/v1/bots/{bot_id}/trades/history")
        assert r.status_code == 200

    @pytest.mark.parametrize("bot_id", _KNOWN_BOT_IDS)
    def test_trades_history_shape(self, client, bot_id):
        """Route returns {bot_id, analytics: {...}, trades: [...]}"""
        r = client.get(f"/api/v1/bots/{bot_id}/trades/history")
        body = r.json()
        assert "bot_id" in body
        assert "analytics" in body
        assert "trades" in body
        assert isinstance(body["trades"], list)
        analytics = body["analytics"]
        assert "total_trades" in analytics
        assert "win_rate_pct" in analytics
        assert "total_pnl_pct" in analytics


class TestBotSummary:
    def test_summary_returns_200(self, client):
        r = client.get("/api/v1/bots/summary")
        assert r.status_code == 200

    def test_summary_shape(self, client):
        """Route returns {total_trades, total_wins, overall_win_rate,
           total_paper_pnl_pct, open_positions, bots: [...]}"""
        r = client.get("/api/v1/bots/summary")
        body = r.json()
        assert "total_trades" in body
        assert "total_wins" in body
        assert "overall_win_rate" in body
        assert "open_positions" in body
        assert "bots" in body
        assert isinstance(body["bots"], list)


class TestResetBot:
    def test_reset_known_bot(self, client):
        r = client.post("/api/v1/bots/smc_bot/reset")
        # Either 200 (reset succeeded) or 404 (no state exists yet) are valid
        assert r.status_code in (200, 404)

    def test_reset_unknown_bot_returns_404(self, client):
        r = client.post("/api/v1/bots/ghost_bot/reset")
        assert r.status_code == 404

    def test_reset_after_state_creation_returns_200(self, client):
        # Force state creation, then reset
        client.get("/api/v1/bots/momentum_bot/state")
        r = client.post("/api/v1/bots/momentum_bot/reset")
        assert r.status_code == 200
        assert r.json()["status"] == "reset"

    def test_reset_clears_capital_and_counts(self, client):
        client.get("/api/v1/bots/ema_crossover_bot/state")
        client.post("/api/v1/bots/ema_crossover_bot/reset")
        state = client.get("/api/v1/bots/ema_crossover_bot/state").json()["state"]
        assert state["total_trades"] == 0
        assert state["wins"] == 0
        assert state["rolling_accuracy"] == 1.0
        assert state["current_threshold"] == 80.0
        assert state["capital_nrs"] == 1_000_000.0
        assert state["capital_deployed"] == 0.0
        assert state["total_pnl_nrs"] == 0.0


# ── New analytics fields (added in session 6) ────────────────────────────

class TestTradeHistoryExtendedAnalytics:
    """Verify the new risk/quality analytics fields are present and sane."""

    @pytest.mark.parametrize("bot_id", _KNOWN_BOT_IDS)
    def test_new_analytics_keys_present(self, client, bot_id):
        body = client.get(f"/api/v1/bots/{bot_id}/trades/history").json()
        a = body["analytics"]
        # New fields added in session 6
        assert "total_win_nrs" in a
        assert "total_loss_nrs" in a
        assert "profit_factor" in a       # None when no losses
        assert "rr_ratio" in a            # None when no losses
        assert "expectancy_pct" in a
        assert "avg_hold_days_win" in a
        assert "avg_hold_days_loss" in a
        assert "timeframe_breakdown" in a

    @pytest.mark.parametrize("bot_id", _KNOWN_BOT_IDS)
    def test_no_closed_trades_gives_null_profit_factor(self, client, bot_id):
        a = client.get(f"/api/v1/bots/{bot_id}/trades/history").json()["analytics"]
        if a["total_trades"] == 0:
            assert a["profit_factor"] is None
            assert a["rr_ratio"] is None
            assert a["expectancy_pct"] == 0

    @pytest.mark.parametrize("bot_id", _KNOWN_BOT_IDS)
    def test_timeframe_filter_accepted(self, client, bot_id):
        for tf in ("daily", "weekly", "monthly"):
            r = client.get(
                f"/api/v1/bots/{bot_id}/trades/history",
                params={"timeframe": tf},
            )
            assert r.status_code == 200
            assert r.json()["timeframe_filter"] == tf

    @pytest.mark.parametrize("bot_id", _KNOWN_BOT_IDS)
    def test_limit_boundary_validation(self, client, bot_id):
        r = client.get(f"/api/v1/bots/{bot_id}/trades/history", params={"limit": 0})
        assert r.status_code == 422
        r = client.get(f"/api/v1/bots/{bot_id}/trades/history", params={"limit": 500})
        assert r.status_code == 422


# ── POST run endpoints — validation only ─────────────────────────────────

class TestBotRunValidation:
    def test_invalid_timeframe_run_returns_422(self, client):
        r = client.post("/api/v1/bots/smc_bot/run", params={"timeframe": "hourly"})
        assert r.status_code == 422

    def test_unknown_bot_run_returns_404(self, client):
        r = client.post("/api/v1/bots/no_such_bot/run", params={"timeframe": "daily"})
        assert r.status_code == 404

    def test_run_all_invalid_timeframe_returns_422(self, client):
        r = client.post("/api/v1/bots/run-all", params={"timeframe": "intraday"})
        assert r.status_code == 422

    def test_valid_timeframes_are_accepted_by_routing(self, client):
        """Routing + validation layer must not reject valid timeframes (200 or 500 from live fetch)."""
        for tf in ("daily", "weekly", "monthly"):
            r = client.post("/api/v1/bots/smc_bot/run", params={"timeframe": tf})
            assert r.status_code in (200, 500), f"Unexpected {r.status_code} for timeframe={tf}"


# ── Capital accounting consistency ────────────────────────────────────────

class TestCapitalAccounting:
    @pytest.mark.parametrize("bot_id", _KNOWN_BOT_IDS)
    def test_open_positions_and_deployed_non_negative(self, client, bot_id):
        r = client.get("/api/v1/bots/")
        bots = {b["id"]: b for b in r.json()["bots"]}
        if bot_id in bots:
            ls = bots[bot_id]["learning_state"]
            assert ls["capital_deployed"] >= 0
            assert ls["capital_available"] >= 0
            assert bots[bot_id]["open_positions"] >= 0

    def test_summary_deployed_leq_capital(self, client):
        body = client.get("/api/v1/bots/summary").json()
        assert body["total_deployed_nrs"] <= body["total_capital_nrs"] + 1.0

    def test_summary_win_rate_range(self, client):
        body = client.get("/api/v1/bots/summary").json()
        assert 0.0 <= body["overall_win_rate"] <= 100.0
