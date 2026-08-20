"""
Free-Sources Aggregator
=======================

Single entry-point used by the rest of the backend. Cascades through
the available free sources in priority order:

    live market, indices, top, status   → yonepse
    per-symbol historical OHLCV         → samirwagle prices.csv → SQLite
    daily floorsheet                    → samirwagle floorsheet CSV
    dividends / rights                  → samirwagle per-symbol
    partial market depth                → yonepse supply_demand
    full 20-level depth                 → NOT AVAILABLE on free tier → raise DepthUnavailable

All responses are already cached inside the individual source modules.

The aggregator is async and network-safe: every external call is wrapped
in try/except and will gracefully degrade rather than raise.
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from . import yonepse, samirwagle, merolagani, nepsealpha, sharesansar, nepalipaisa, nepsetrading

logger = logging.getLogger(__name__)


class DepthUnavailable(Exception):
    """Raised when full 20-level depth is requested but only partial is free."""


# ─── helpers ─────────────────────────────────────────────────────────────────

async def _first_nonempty_list(*coros) -> List[Any]:
    """
    Await coroutines in sequence; return the first non-empty list result.
    If all return empty/None, return [].
    Unused coroutines are explicitly closed to suppress RuntimeWarning.
    """
    coro_list = list(coros)
    for i, coro in enumerate(coro_list):
        try:
            result = await coro
            if result:
                # Close remaining un-consumed coroutines cleanly
                for unused in coro_list[i + 1:]:
                    unused.close()
                return result
        except Exception as exc:  # noqa: BLE001
            logger.debug("aggregator: source error — %s", exc)
    return []


# -------------------------- live market / snapshot --------------------------

async def live_market() -> List[Dict[str, Any]]:
    """
    Per-symbol live snapshot (LTP, change, volume, turnover, market cap).

    Cascade order (real-time first; yonepse is last because it lags ~15 min):
      1. merolagani   – /LatestMarket.aspx HTML table (real-time, always accessible)
      2. sharesansar  – /today-share-price HTML table (real-time, always accessible)
      3. nepalipaisa  – JSON API probing (real-time; needs Nepal proxy for full access)
      4. nepsetrading – Next.js __NEXT_DATA__ + API probing (needs Nepal proxy)
      5. nepsealpha   – JSON API (real-time; geo-blocked, needs NEPAL_PROXY_LIST)
      6. yonepse      – GitHub Actions JSON (~15 min lag, last-resort fallback)
    """
    return await _first_nonempty_list(
        merolagani.get_live_market(),
        sharesansar.get_live_market(),
        nepalipaisa.get_live_market(),
        nepsetrading.get_live_market(),
        nepsealpha.get_live_market(),
        yonepse.get_live_market(),
    )


async def live_quote(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Single-symbol live quote. Returns None if not found in any source.

    Cascade: merolagani → sharesansar → nepalipaisa → nepsetrading → nepsealpha → yonepse.
    yonepse is last because it lags ~15 min; all others are real-time.
    """
    coros = [
        merolagani.get_live_quote(symbol),
        sharesansar.get_live_quote(symbol),
        nepalipaisa.get_live_quote(symbol),
        nepsetrading.get_live_quote(symbol),
        nepsealpha.get_live_quote(symbol),
        yonepse.get_live_market_by_symbol(symbol),
    ]
    for i, coro in enumerate(coros):
        try:
            result = await coro
            if result:
                for unused in coros[i + 1:]:
                    unused.close()
                return result
        except Exception as exc:  # noqa: BLE001
            logger.debug("aggregator live_quote %s: %s", symbol, exc)
    return None


async def market_status() -> Dict[str, Any]:
    return await yonepse.get_market_status()


async def market_summary() -> List[Dict[str, Any]]:
    """Market-wide summary. Cascades: yonepse → merolagani → nepsealpha."""
    data = await yonepse.get_market_summary()
    if data:
        return data
    try:
        ml = await merolagani.get_market_summary()
        if ml:
            return [ml] if isinstance(ml, dict) else ml
    except Exception:  # noqa: BLE001
        pass
    try:
        na = await nepsealpha.get_market_summary()
        if na:
            return [na] if isinstance(na, dict) else na
    except Exception:  # noqa: BLE001
        pass
    return []


async def indices() -> List[Dict[str, Any]]:
    """
    NEPSE + sector indices. Cascades: yonepse → nepsealpha → sharesansar.
    """
    return await _first_nonempty_list(
        yonepse.get_indices(),
        nepsealpha.get_indices(),
        sharesansar.get_indices(),
    )


async def sector_indices() -> List[Dict[str, Any]]:
    """Sector sub-indices. Cascades: yonepse → nepsealpha."""
    return await _first_nonempty_list(
        yonepse.get_sector_indices(),
        nepsealpha.get_indices(),
    )


async def top_stocks() -> Dict[str, List[Dict[str, Any]]]:
    """
    Top gainers / losers / turnover. Cascades: yonepse → merolagani → nepsealpha.
    """
    coros = [
        yonepse.get_top_stocks(),
        merolagani.get_top_stocks(),
        nepsealpha.get_top_movers(),
    ]
    for i, coro in enumerate(coros):
        try:
            result = await coro
            # Accept if any sub-list is non-empty
            if result and any(v for v in result.values() if isinstance(v, list) and v):
                for unused in coros[i + 1:]:
                    unused.close()
                return result
        except Exception as exc:  # noqa: BLE001
            logger.debug("aggregator top_stocks: %s", exc)
    return {
        "top_gainer": [], "top_loser": [], "top_turnover": [],
        "top_trade": [], "top_transaction": [],
    }


# -------------------------- depth --------------------------

async def partial_depth(symbol: str) -> Dict[str, Any]:
    """Best-effort aggregate supply/demand (yonepse). Always safe to call."""
    return await yonepse.get_supply_demand_for_symbol(symbol)


async def full_depth(symbol: str) -> Dict[str, Any]:
    """
    Full 20-level order book — not available on free tier.
    Callers should catch DepthUnavailable and fall back to partial_depth().
    """
    raise DepthUnavailable(
        "Full 20-level depth requires direct access to nepalstock.com.np, "
        "which is geo-restricted to Nepal. Free GitHub-Actions scrapers "
        "only publish aggregate bid/ask. Use partial_depth() for best "
        "available free data."
    )


# -------------------------- floorsheet --------------------------

async def latest_floorsheet_date() -> Optional[str]:
    return await samirwagle.get_latest_floorsheet_date()


async def floorsheet(
    target: Optional[str | date] = None,
    symbol: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Full floorsheet for a trading date.

    Args:
        target: 'YYYY-MM-DD' string, date object, or None (→ latest available).
        symbol: filter to one symbol if provided.

    Cascade: samirwagle CSV → sharesansar HTML scrape.
    """
    if target is None:
        target = await samirwagle.get_latest_floorsheet_date()

    # samirwagle (primary — daily CSV)
    if target:
        try:
            if symbol:
                rows = await samirwagle.get_floorsheet_for_symbol(symbol, target)
            else:
                rows = await samirwagle.get_floorsheet_for_date(target)
            if rows:
                return rows
        except Exception:  # noqa: BLE001
            pass

    # sharesansar HTML fallback (today's floorsheet only)
    try:
        rows = await sharesansar.get_floorsheet(max_pages=5)
        if rows:
            if symbol:
                sym_u = symbol.upper()
                rows = [r for r in rows if r.get("symbol", "").upper() == sym_u]
            return rows
    except Exception:  # noqa: BLE001
        pass

    return []


# -------------------------- per-symbol series --------------------------

async def symbol_prices(symbol: str) -> List[Dict[str, Any]]:
    """
    OHLCV history (most-recent first).
    Primary: SamirWagle per-symbol prices.csv (daily updated).
    Fallback: nepsealpha OHLCV API.
    """
    rows = await samirwagle.get_symbol_prices(symbol)
    if rows:
        return rows
    # Fallback to nepsealpha OHLCV
    try:
        na_rows = await nepsealpha.get_ohlcv(symbol)
        if na_rows:
            return na_rows
    except Exception:  # noqa: BLE001
        pass
    return []


async def symbol_prices_enriched(symbol: str) -> List[Dict[str, Any]]:
    """
    Same as `symbol_prices` but **prepends today's live bar** (synthesized from
    the live quote snapshot) when it is fresher than the latest historical row.

    Upstream yonepse / samirwagle per-symbol CSVs can lag the live market by
    several days or weeks. Merging the live snapshot in here fixes:
      * empty / truncated charts on StockAnalysis,
      * wrong "last close" used by indicators & recommendations,
      * stale % change in screeners.

    Gap-fill logic: if samirwagle data is stale by more than STALE_THRESHOLD_DAYS,
    we transparently merge in recent bars from nepsealpha to cover the gap.
    nepsealpha is tried silently; if it fails the function degrades gracefully.

    Live quote cascade: yonepse → merolagani → nepsealpha → sharesansar.
    """
    rows = await samirwagle.get_symbol_prices(symbol)
    if not rows:
        try:
            rows = await nepsealpha.get_ohlcv(symbol) or []
        except Exception:  # noqa: BLE001
            rows = []

    # ── Gap-fill: use nepsealpha to cover missing bars if samirwagle is stale ──
    STALE_THRESHOLD_DAYS = 10
    if rows:
        try:
            from datetime import date as _date  # local import to avoid shadowing
            latest_sw_date = _date.fromisoformat(str(rows[0].get("date", ""))[:10])
            days_stale = (_date.today() - latest_sw_date).days
            if days_stale > STALE_THRESHOLD_DAYS:
                # Fetch recent bars from nepsealpha (covers up to ~6 months back)
                period = "1y" if days_stale > 180 else "6m"
                na_rows = await nepsealpha.get_ohlcv(symbol, period=period)
                if na_rows:
                    cutoff = latest_sw_date.isoformat()
                    # Keep only bars newer than samirwagle's latest (fills the gap)
                    gap_rows = [
                        r for r in na_rows
                        if str(r.get("date", ""))[:10] > cutoff
                    ]
                    if gap_rows:
                        # gap_rows already newest-first; merge before samirwagle history
                        # Deduplicate by date (prefer nepsealpha for gap period)
                        seen_dates = {str(r.get("date", ""))[:10] for r in gap_rows}
                        old_rows = [r for r in rows if str(r.get("date", ""))[:10] not in seen_dates]
                        rows = gap_rows + old_rows
                        logger.info(
                            "symbol_prices_enriched(%s): gap-filled %d bars via nepsealpha "
                            "(samirwagle was %d days stale)",
                            symbol.upper(), len(gap_rows), days_stale,
                        )
        except Exception as exc:  # noqa: BLE001
            logger.debug("nepsealpha gap-fill skipped for %s: %s", symbol, exc)

    live = await live_quote(symbol)

    if not live:
        return rows or []

    # Determine the live bar's "date"
    last_updated = live.get("last_updated")
    if isinstance(last_updated, str):
        try:
            # handles "2026-04-28T14:59:49.693975" / with Z
            dt = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
        except Exception:  # noqa: BLE001
            dt = datetime.now(timezone.utc)
    else:
        dt = datetime.now(timezone.utc)
    live_date = dt.date().isoformat()

    latest_hist_date = rows[0].get("date") if rows else None
    if latest_hist_date and str(latest_hist_date) >= live_date:
        # history already has today (or newer) — nothing to prepend
        return rows

    ltp = _safe_float(live.get("ltp"))
    high = _safe_float(live.get("high"), default=ltp)
    low = _safe_float(live.get("low"), default=ltp)
    prev_close = _safe_float(live.get("previous_close"), default=ltp)
    open_px = prev_close if prev_close > 0 else ltp
    pct = _safe_float(live.get("percent_change"), default=0.0)
    qty = int(_safe_float(live.get("volume"), default=0))
    turnover = _safe_float(live.get("turnover"), default=0.0)

    synthesized = {
        "date": live_date,
        "open": round(open_px, 2),
        "high": round(high, 2),
        "low": round(low, 2),
        "ltp": round(ltp, 2),
        "close": round(ltp, 2),  # alias commonly expected by analytics
        "percent_change": round(pct, 2),
        "qty": qty,
        "volume": qty,  # alias
        "turnover": round(turnover, 2),
        "source": "live_snapshot",
    }

    return [synthesized] + list(rows or [])


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except Exception:  # noqa: BLE001
        return default


# -------------------------- sector drill-down --------------------------

def _normalize_sector(name: str) -> str:
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


async def sector_stocks(sector_name: str) -> List[Dict[str, Any]]:
    """
    Return live snapshot rows for every symbol that belongs to the given
    sector. Sector classification comes from the yonepse 'securities' master
    (each row has a `sector` field); we join it to the live snapshot by
    symbol.
    """
    wanted = _normalize_sector(sector_name)
    if not wanted:
        return []

    try:
        securities = await yonepse.get_all_securities()
    except Exception:  # noqa: BLE001
        securities = []

    symbol_to_sector: Dict[str, str] = {}
    for s in securities or []:
        sym = (s.get("symbol") or s.get("ticker") or "").upper()
        sec = (
            s.get("sectorName")
            or s.get("sector")
            or s.get("sector_name")
            or s.get("sectorDescription")
            or ""
        )
        if sym:
            symbol_to_sector[sym] = sec

    try:
        live = await yonepse.get_live_market()
    except Exception:  # noqa: BLE001
        live = []

    matches: List[Dict[str, Any]] = []
    for row in live or []:
        sym = (row.get("symbol") or "").upper()
        sec = symbol_to_sector.get(sym, "")
        if _normalize_sector(sec) == wanted:
            matches.append({**row, "sector": sec})

    # sort by turnover desc so the UI shows biggest movers first
    matches.sort(key=lambda r: _safe_float(r.get("turnover"), 0.0), reverse=True)
    return matches


# -------------------------- live-scored recommendations --------------------------

def _recommend_from_live(rows: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """
    Score the live snapshot on a composite of momentum (today %change),
    volume participation (turnover rank) and intraday strength.

    This is a deliberately simple, transparent scorer that is fresh every
    request and does not depend on any historical source.
    """
    if not rows:
        return []

    # normalize turnover to 0..1 rank
    turnovers = sorted(
        [_safe_float(r.get("turnover")) for r in rows], reverse=True
    )
    total_turnover = sum(turnovers) or 1.0
    rank_lookup: Dict[str, float] = {}
    for r in rows:
        t = _safe_float(r.get("turnover"))
        rank_lookup[(r.get("symbol") or "").upper()] = t / total_turnover

    scored: List[Dict[str, Any]] = []
    for r in rows:
        sym = (r.get("symbol") or "").upper()
        ltp = _safe_float(r.get("ltp"))
        pct = _safe_float(r.get("percent_change"))
        high = _safe_float(r.get("high"), default=ltp)
        low = _safe_float(r.get("low"), default=ltp)
        prev = _safe_float(r.get("previous_close"), default=ltp)
        volume = _safe_float(r.get("volume"))
        turnover = _safe_float(r.get("turnover"))

        # momentum: clipped % change → -1..+1
        momentum = max(-10.0, min(10.0, pct)) / 10.0
        # position-in-range (close vs day range) → 0..1
        pos = 0.5
        if high > low:
            pos = max(0.0, min(1.0, (ltp - low) / (high - low)))
        # turnover weight → 0..1
        turn_weight = rank_lookup.get(sym, 0.0)
        # final composite in 0..100
        raw = (
            0.45 * (momentum * 0.5 + 0.5)   # 0..1
            + 0.30 * pos                     # 0..1
            + 0.25 * min(1.0, turn_weight * 20.0)
        )
        score = round(raw * 100.0, 2)

        if momentum > 0.2 and pos > 0.55:
            action = "BUY"
        elif momentum < -0.25 and pos < 0.4:
            action = "AVOID"
        else:
            action = "WATCH"

        rationale: List[str] = []
        if pct > 0:
            rationale.append(f"Today +{pct:.2f}%")
        elif pct < 0:
            rationale.append(f"Today {pct:.2f}%")
        if pos > 0.7:
            rationale.append("Trading near day-high (strong close)")
        elif pos < 0.3:
            rationale.append("Trading near day-low (weak close)")
        if turnover > 0:
            rationale.append(f"Turnover: NPR {turnover:,.0f}")

        scored.append({
            "symbol": sym,
            "action": action,
            "score": score,
            "last_close": ltp,
            "change_1d_pct": pct,
            "change_5d_pct": None,
            "change_20d_pct": None,
            "rsi_14": None,
            "macd_hist": None,
            "volume_ratio": None,
            "volatility_annualised": None,
            "drawdown_from_high_pct": round((ltp - high) / high * 100.0, 2)
            if high > 0 else None,
            "factor_scores": {
                "trend": round(momentum * 0.5 + 0.5, 3),
                "momentum": round(momentum, 3),
                "volume": round(min(1.0, turn_weight * 20.0), 3),
                "volatility": round(pos, 3),
                "drawdown": round(1.0 - min(1.0, abs(prev - ltp) / max(prev, 1.0)), 3),
            },
            "rationale": rationale,
            "as_of_date": datetime.now(timezone.utc).date().isoformat(),
            "live_volume": volume,
            "live_turnover": turnover,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]


async def recommendations_live(
    limit: int = 50,
    action: Optional[str] = None,
    min_score: float = 0.0,
) -> Dict[str, Any]:
    """Compute recommendations from today's live snapshot (multi-source cascade)."""
    rows = await live_market()   # cascades through all sources
    scored = _recommend_from_live(rows or [], limit=10_000)

    if min_score > 0:
        scored = [r for r in scored if r["score"] >= min_score]
    if action:
        scored = [r for r in scored if r["action"] == action.upper()]

    return {
        "status": "success",
        "count": min(limit, len(scored)),
        "universe_size": len(rows or []),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "data": scored[:limit],
    }


async def symbol_dividends(symbol: str) -> List[Dict[str, Any]]:
    return await samirwagle.get_symbol_dividends(symbol)


async def symbol_rights(symbol: str) -> List[Dict[str, Any]]:
    return await samirwagle.get_symbol_rights(symbol)


# -------------------------- meta --------------------------

async def all_securities() -> List[Dict[str, Any]]:
    """Master of 613 securities with sector, company name, etc."""
    return await yonepse.get_all_securities()


async def brokers() -> List[Dict[str, Any]]:
    return await yonepse.get_brokers()


async def upcoming_ipo() -> List[Dict[str, Any]]:
    return await yonepse.get_upcoming_ipo()


async def disclosures() -> List[Dict[str, Any]]:
    return await yonepse.get_disclosures()


async def notices() -> List[Dict[str, Any]]:
    return await yonepse.get_notices()


async def dps() -> List[Dict[str, Any]]:
    return await yonepse.get_dps()


# -------------------------- health --------------------------

async def health() -> Dict[str, Any]:
    """
    Aggregate freshness report from all underlying sources — used by
    /__verify-data and by `nepse_api_client` when deciding fallbacks.

    Includes: yonepse, samirwagle, merolagani, nepsealpha, sharesansar,
              nepalipaisa, nepsetrading.
    Each scraper check is bounded to 5 s so the health endpoint never hangs.
    """
    import asyncio as _aio

    async def _timed(coro, fallback):
        try:
            return await _aio.wait_for(coro, timeout=5.0)
        except Exception:  # noqa: BLE001
            return fallback

    y = await yonepse.get_freshness()
    s = await samirwagle.get_freshness()
    fresh_enough = (y.get("commit_age_minutes") or 99999) < 120

    # Run all scraper health checks concurrently, each bounded to 5 s
    na_health, np_health, nt_health = await _aio.gather(
        _timed(nepsealpha.get_freshness(),  {"reachable": False, "note": "geo-blocked without NEPAL_PROXY_LIST"}),
        _timed(nepalipaisa.get_freshness(), {"reachable": False, "note": "timed out or unreachable"}),
        _timed(nepsetrading.get_freshness(), {"reachable": False, "note": "timed out or unreachable"}),
    )

    return {
        "yonepse": y,
        "samirwagle": s,
        "nepsealpha": na_health,
        "nepalipaisa": np_health,
        "nepsetrading": nt_health,
        "merolagani": {"note": "available — use live_market() to verify"},
        "sharesansar": {"note": "available — use live_market() to verify"},
        "yonepse_fresh": fresh_enough,
        "depth_available": "partial",  # always partial on free tier
        "full_depth_available": False,
        "scraper_sources": [
            "yonepse", "merolagani", "sharesansar",
            "nepalipaisa", "nepsetrading", "nepsealpha",
        ],
        "proxy_rotation": (
            "configure PROXY_LIST for general rotation; "
            "set NEPAL_PROXY_LIST (Nepal-exit proxies) to unlock "
            "geo-blocked sources: nepsealpha, nepalipaisa, nepsetrading"
        ),
    }
