"""
Yonepse GitHub-Actions Data Client
==================================

Primary free-tier live data source. Fetches pre-scraped NEPSE JSON from
the `Shubhamnpk/yonepse` repository, refreshed by GitHub Actions every
~15 minutes during market hours.

Reachable from Vercel US in ~30–180 ms with zero rate-limiting.

Endpoints covered and upstream shape (verified 2026-04-28):

    nepse_data.json        list[dict]   per-symbol live snapshot
                           keys: symbol, name, ltp, previous_close, change,
                                 percent_change, high, low, volume, turnover,
                                 trades, last_updated (ISO), market_cap

    indices.json           list[dict]   NEPSE + Sensitive + Float + Sens Float
                           keys: index, close, high, low, previousClose,
                                 change, perChange, fiftyTwoWeekHigh,
                                 fiftyTwoWeekLow, currentValue, generatedTime

    sector_indices.json    list[dict]   17 sector indices
                           keys: indexCode, indexName, description,
                                 sectorMaster.sectorDescription,
                                 baseYearMarketCapitalization

    top_stocks.json        dict         keys: top_gainer, top_loser,
                                               top_turnover, top_trade,
                                               top_transaction
                           each list[dict] with: symbol, ltp, cp,
                                                 pointChange, percentageChange,
                                                 securityName, securityId

    market_summary.json    list[dict]   keys: detail, value
                           (Total Turnover, Market Cap, Listed Co., etc.)

    market_status.json     dict         keys: is_open (bool),
                                               last_checked (ISO)

    supply_demand.json     dict         keys: supplyList, demandList
                           each list[dict] with: totalQuantity, totalOrder,
                                                 securityId, symbol, securityName

    live_trades.json       list[dict]   empty outside market hours

    all_securities.json    list[dict]   613 securities master
                           keys: id, companyName, symbol, securityName, status,
                                 companyEmail, website, sectorName,
                                 regulatoryBody, instrumentType, dpsCode

    brokers.json           list[dict]   92 brokers master

    upcoming_ipo.json      list[dict]   upcoming IPO announcements

No writes. Safe for serverless. All responses cached in `TTLCache`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from .cache import get_cache

logger = logging.getLogger(__name__)

BASE = "https://raw.githubusercontent.com/Shubhamnpk/yonepse/main/data"
GITHUB_API = "https://api.github.com/repos/Shubhamnpk/yonepse"

# Default TTL (seconds). Data upstream refreshes every ~15 min so 60 s is safe.
DEFAULT_TTL = 60.0
LONG_TTL = 3600.0  # for static-ish masters (all_securities, brokers)

_HEADERS = {
    "User-Agent": "nepse-bot/1.0 (+https://github.com/dixitmanidhakal)",
    "Accept": "application/json, text/plain, */*",
}


# -------------------------- core fetch --------------------------

async def _fetch_json(
    path: str,
    ttl: float = DEFAULT_TTL,
    timeout: float = 15.0,
) -> Optional[Any]:
    """
    GET `{BASE}/{path}` as JSON. Returns cached value on cache hit.
    Returns None on any error (never raises) — callers decide what to do.
    """
    cache = get_cache()
    key = f"yonepse::{path}"

    async def loader() -> Optional[Any]:
        url = f"{BASE}/{path}"
        try:
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=True, headers=_HEADERS
            ) as client:
                r = await client.get(url)
                if r.status_code != 200:
                    logger.warning(
                        "yonepse %s: HTTP %s (%s bytes)",
                        path, r.status_code, len(r.content),
                    )
                    return None
                return r.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("yonepse %s: %s", path, exc)
            return None

    return await cache.aget_or_set(key, loader, ttl)


# -------------------------- public API --------------------------

async def get_live_market() -> List[Dict[str, Any]]:
    """Per-symbol snapshot (362 active securities)."""
    data = await _fetch_json("nepse_data.json", ttl=DEFAULT_TTL)
    if isinstance(data, list):
        return data
    return []


async def get_live_market_by_symbol(symbol: str) -> Optional[Dict[str, Any]]:
    """Convenience helper — O(N) scan over the cached live snapshot."""
    if not symbol:
        return None
    sym_u = symbol.upper()
    for row in await get_live_market():
        if str(row.get("symbol", "")).upper() == sym_u:
            return row
    return None


async def get_indices() -> List[Dict[str, Any]]:
    """Primary indices: NEPSE, Sensitive, Float, Sensitive Float."""
    data = await _fetch_json("indices.json")
    return data if isinstance(data, list) else []


async def get_sector_indices() -> List[Dict[str, Any]]:
    """17 sector sub-indices."""
    data = await _fetch_json("sector_indices.json")
    return data if isinstance(data, list) else []


async def get_top_stocks() -> Dict[str, List[Dict[str, Any]]]:
    """
    Top gainers/losers/turnover/trade/transaction.
    Returns dict with keys: top_gainer, top_loser, top_turnover, top_trade, top_transaction.
    """
    data = await _fetch_json("top_stocks.json")
    if isinstance(data, dict):
        return {
            k: v for k, v in data.items() if isinstance(v, list)
        }
    return {
        "top_gainer": [], "top_loser": [], "top_turnover": [],
        "top_trade": [], "top_transaction": [],
    }


async def get_market_summary() -> List[Dict[str, Any]]:
    """Market-wide summary stats (turnover, mkt cap, listed co., etc.)."""
    data = await _fetch_json("market_summary.json")
    return data if isinstance(data, list) else []


async def get_market_status() -> Dict[str, Any]:
    """{'is_open': bool, 'last_checked': ISO-string}."""
    data = await _fetch_json("market_status.json", ttl=30.0)
    if isinstance(data, dict):
        return data
    return {"is_open": None, "last_checked": None}


async def get_supply_demand() -> Dict[str, List[Dict[str, Any]]]:
    """Aggregate supply / demand lists (best-effort 2-level depth substitute)."""
    data = await _fetch_json("supply_demand.json", ttl=30.0)
    if isinstance(data, dict):
        return {
            "supplyList": data.get("supplyList", []) or [],
            "demandList": data.get("demandList", []) or [],
        }
    return {"supplyList": [], "demandList": []}


async def get_supply_demand_for_symbol(symbol: str) -> Dict[str, Any]:
    """
    Partial market depth for one symbol, derived from supply_demand.json.
    This is NOT a full 20-level order book — just best aggregate bid/ask
    available in the free feed.

    Returns:
        {
          "symbol": "NABIL",
          "supply": { "totalQuantity": int, "totalOrder": int } | None,
          "demand": { "totalQuantity": int, "totalOrder": int } | None,
          "partial": True,
          "source": "yonepse:supply_demand"
        }
    """
    sym_u = (symbol or "").upper()
    sd = await get_supply_demand()
    supply = next(
        (r for r in sd["supplyList"] if str(r.get("symbol", "")).upper() == sym_u),
        None,
    )
    demand = next(
        (r for r in sd["demandList"] if str(r.get("symbol", "")).upper() == sym_u),
        None,
    )
    return {
        "symbol": sym_u,
        "supply": (
            {
                "totalQuantity": supply.get("totalQuantity"),
                "totalOrder": supply.get("totalOrder"),
            }
            if supply
            else None
        ),
        "demand": (
            {
                "totalQuantity": demand.get("totalQuantity"),
                "totalOrder": demand.get("totalOrder"),
            }
            if demand
            else None
        ),
        "partial": True,
        "source": "yonepse:supply_demand",
    }


async def get_all_securities() -> List[Dict[str, Any]]:
    """Full securities master (613 rows). Static-ish → long TTL."""
    data = await _fetch_json("all_securities.json", ttl=LONG_TTL)
    return data if isinstance(data, list) else []


async def get_brokers() -> List[Dict[str, Any]]:
    """Broker directory. Long TTL."""
    data = await _fetch_json("brokers.json", ttl=LONG_TTL)
    return data if isinstance(data, list) else []


async def get_upcoming_ipo() -> List[Dict[str, Any]]:
    """Upcoming IPO calendar."""
    data = await _fetch_json("upcoming_ipo.json", ttl=300.0)
    return data if isinstance(data, list) else []


async def get_disclosures() -> List[Dict[str, Any]]:
    data = await _fetch_json("disclosures.json", ttl=300.0)
    return data if isinstance(data, list) else []


async def get_notices() -> List[Dict[str, Any]]:
    data = await _fetch_json("notices.json", ttl=300.0)
    return data if isinstance(data, list) else []


async def get_dps() -> List[Dict[str, Any]]:
    data = await _fetch_json("dps.json", ttl=LONG_TTL)
    return data if isinstance(data, list) else []


async def get_live_trades() -> List[Dict[str, Any]]:
    """Live trade stream. Empty outside market hours."""
    data = await _fetch_json("live_trades.json", ttl=15.0)
    return data if isinstance(data, list) else []


# -------------------------- freshness helpers --------------------------

async def get_freshness() -> Dict[str, Any]:
    """
    Inspect GitHub commits API + embedded `last_updated` to report how
    stale the scraper is. Useful for `/__verify-data` and for aggregator
    fallback decisions.

    Returns:
        {
          "last_commit_utc": str | None,
          "commit_age_minutes": float | None,
          "data_last_updated": str | None,
          "data_age_minutes": float | None,
          "sample_symbol_count": int | None,
        }
    """
    cache = get_cache()
    key = "yonepse::freshness"

    async def loader() -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "last_commit_utc": None,
            "commit_age_minutes": None,
            "data_last_updated": None,
            "data_age_minutes": None,
            "sample_symbol_count": None,
        }
        async with httpx.AsyncClient(
            timeout=10.0, follow_redirects=True, headers=_HEADERS
        ) as client:
            try:
                r = await client.get(f"{GITHUB_API}/commits?per_page=1")
                if r.status_code == 200 and r.json():
                    iso = r.json()[0]["commit"]["committer"]["date"]
                    out["last_commit_utc"] = iso
                    out["commit_age_minutes"] = _minutes_since(iso)
            except Exception as exc:  # noqa: BLE001
                logger.debug("yonepse freshness commits: %s", exc)

        snapshot = await get_live_market()
        if snapshot:
            out["sample_symbol_count"] = len(snapshot)
            ts = snapshot[0].get("last_updated")
            if isinstance(ts, str):
                out["data_last_updated"] = ts
                out["data_age_minutes"] = _minutes_since(ts)
        return out

    return await cache.aget_or_set(key, loader, ttl=60.0)


def _minutes_since(iso_str: str) -> Optional[float]:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return round((datetime.now(timezone.utc) - dt).total_seconds() / 60.0, 1)
    except Exception:  # noqa: BLE001
        return None
