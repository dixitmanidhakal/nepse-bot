"""
NepseAlpha Scraper
==================

Scrapes live NEPSE market data from nepsealpha.com — a well-known Nepali
stock analysis portal.

Discovered working JSON API endpoints (no Nepal proxy required):
  GET  /trading/1/search?limit=500&query=   → all symbols + metadata
  GET  /trading/1/history?symbol=X          → OHLCV (TradingView format)
       &resolution=1D                          supported: 1, 3, 5, 15, 30, 1D, 1W, 1M
       &from=<unix_ts>&to=<unix_ts>
  GET  /trading/1/quotes                    → (returns [] without auth)
  GET  /trading/1/marks                     → (returns [] without auth)

NOTE: /nepse/1/ endpoints (old API) all return HTTP 404 as of 2026-06.
      The site IS reachable without Nepal proxy using JSON headers.

Features:
  - Direct HTTPS connection (no proxy needed) with same-origin JSON headers.
  - search endpoint → symbol metadata (name, sector).
  - history endpoint → OHLCV in TradingView format {s, t, o, h, l, c, v}.
  - get_live_market(): search + concurrent 1D history for live prices.
  - get_ohlcv(): multi-year daily OHLCV via history?resolution=1D.
  - Per-response TTL cache via shared TTLCache.
  - Graceful degradation — returns [] / {} on any error.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from .cache import get_cache

logger = logging.getLogger(__name__)

BASE = "https://nepsealpha.com"
_API_BASE = f"{BASE}/trading/1"
_TIMEOUT = 15.0
_TTL_LIVE = 45.0
_TTL_OHLCV = 300.0
_TTL_SEARCH = 600.0   # search results change slowly

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://nepsealpha.com/live-market",
    "Origin": "https://nepsealpha.com",
    "sec-fetch-site": "same-origin",
    "sec-fetch-mode": "cors",
    "sec-fetch-dest": "empty",
    "X-Requested-With": "XMLHttpRequest",
}

# Tracks whether Cloudflare has rate-limited us (clears after TTL expires on cache entries)
_rate_limited_until: float = 0.0


# ─── low-level fetch (direct, no proxy) ──────────────────────────────────────

async def _get_json_direct(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    ttl: float = _TTL_LIVE,
) -> Optional[Any]:
    """GET a URL directly as JSON with TTL cache. No proxy rotation needed.

    Respects Cloudflare 429 rate-limits: returns None immediately when
    the module-level _rate_limited_until timestamp is in the future.
    """
    global _rate_limited_until

    # Bail early if we're in a rate-limit cool-down
    if time.time() < _rate_limited_until:
        logger.debug("nepsealpha: skipping %s — rate-limited for %.0fs", url,
                     _rate_limited_until - time.time())
        return None

    cache = get_cache()
    ck = f"nepsealpha::direct::{url}::{sorted((params or {}).items())}"
    cached = cache.get(ck)
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT,
            follow_redirects=True,
            verify=False,
            headers=_HEADERS,
        ) as client:
            r = await client.get(url, params=params)
            if r.status_code == 200:
                try:
                    data = r.json()
                except Exception:
                    import json as _json
                    data = _json.loads(r.text.strip().lstrip("\ufeff"))
                cache.set(ck, data, ttl)
                return data
            elif r.status_code == 429:
                retry_after = float(r.headers.get("Retry-After", "120"))
                _rate_limited_until = time.time() + retry_after
                logger.warning(
                    "nepsealpha: 429 rate-limited — pausing for %.0fs", retry_after
                )
            else:
                logger.debug("nepsealpha direct %s: HTTP %s", url, r.status_code)
    except Exception as exc:
        logger.debug("nepsealpha direct %s: %s", url, exc)

    return None


# ─── safe type coercions ──────────────────────────────────────────────────────

def _sf(v: Any, default: float = 0.0) -> float:
    try:
        return float(str(v).replace(",", "")) if v is not None else default
    except (TypeError, ValueError):
        return default


def _si(v: Any, default: int = 0) -> int:
    try:
        return int(float(str(v).replace(",", ""))) if v is not None else default
    except (TypeError, ValueError):
        return default


# ─── normalizers ─────────────────────────────────────────────────────────────

def _normalize_stock(row: Dict[str, Any], source: str = "nepsealpha") -> Optional[Dict[str, Any]]:
    """Convert a raw nepsealpha API stock row to our normalised format."""
    symbol = str(
        row.get("symbol") or row.get("ticker") or row.get("s") or ""
    ).strip().upper()
    if not symbol:
        return None

    ltp = _sf(
        row.get("ltp") or row.get("lastTradedPrice") or row.get("price")
        or row.get("close") or row.get("lastClose")
    )
    prev = _sf(
        row.get("previousClose") or row.get("prevClose") or row.get("previousPrice") or ltp
    )
    pct = _sf(
        row.get("percentChange") or row.get("pChange") or row.get("percent_change")
        or (((ltp - prev) / prev * 100.0) if prev else 0.0)
    )

    return {
        "symbol": symbol,
        "name": str(
            row.get("name") or row.get("companyName") or row.get("securityName")
            or row.get("full_name") or row.get("description") or symbol
        ).strip(),
        "sector": str(row.get("sector") or row.get("sectorName") or "").strip(),
        "ltp": ltp,
        "previous_close": prev,
        "change": round(ltp - prev, 2),
        "percent_change": round(pct, 2),
        "open": _sf(row.get("open") or row.get("openPrice")),
        "high": _sf(row.get("high") or row.get("highPrice")),
        "low": _sf(row.get("low") or row.get("lowPrice")),
        "volume": _si(
            row.get("volume") or row.get("totalTradeQuantity") or row.get("qty")
        ),
        "turnover": _sf(
            row.get("turnover") or row.get("totalTurnover") or row.get("tradedValue")
        ),
        "trades": _si(row.get("trades") or row.get("totalTrades") or row.get("noOfTransactions")),
        "market_cap": _sf(row.get("marketCap") or row.get("marketCapitalization")),
        "week52_high": _sf(row.get("week52High") or row.get("fiftyTwoWeekHigh")),
        "week52_low": _sf(row.get("week52Low") or row.get("fiftyTwoWeekLow")),
        "eps": _sf(row.get("eps") or row.get("EPS")),
        "pe_ratio": _sf(row.get("pe") or row.get("peRatio")),
        "book_value": _sf(row.get("bookValue") or row.get("book_value")),
        "source": source,
    }


def _tv_to_normalized(
    sym_info: Dict[str, Any],
    tv_data: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Convert a TradingView history response + search metadata into a normalized stock dict.
    tv_data format: {s, t: [...], o: [...], h: [...], l: [...], c: [...], v: [...]}
    """
    if tv_data.get("s") != "ok":
        return None

    c_vals = tv_data.get("c", [])
    o_vals = tv_data.get("o", [])
    h_vals = tv_data.get("h", [])
    l_vals = tv_data.get("l", [])
    v_vals = tv_data.get("v", [])

    if not c_vals:
        return None

    ltp = _sf(c_vals[-1])
    prev = _sf(c_vals[-2]) if len(c_vals) >= 2 else ltp
    pct = ((ltp - prev) / prev * 100.0) if prev else 0.0

    symbol = str(sym_info.get("symbol") or "").strip().upper()
    if not symbol:
        return None

    return {
        "symbol": symbol,
        "name": str(
            sym_info.get("description") or sym_info.get("full_name") or symbol
        ).strip(),
        "sector": str(sym_info.get("sector") or "").strip(),
        "ltp": ltp,
        "previous_close": prev,
        "change": round(ltp - prev, 2),
        "percent_change": round(pct, 2),
        "open": _sf(o_vals[-1]) if o_vals else 0.0,
        "high": _sf(h_vals[-1]) if h_vals else 0.0,
        "low": _sf(l_vals[-1]) if l_vals else 0.0,
        "volume": _si(v_vals[-1]) if v_vals else 0,
        "turnover": 0.0,
        "trades": 0,
        "market_cap": 0.0,
        "week52_high": 0.0,
        "week52_low": 0.0,
        "eps": 0.0,
        "pe_ratio": 0.0,
        "book_value": 0.0,
        "source": "nepsealpha",
    }


# ─── public API ──────────────────────────────────────────────────────────────

async def get_live_market() -> List[Dict[str, Any]]:
    """
    nepsealpha live market — intentionally returns [] to avoid rate-limiting.

    Context:
      The old /nepse/1/allstocks and /nepse/1/live-market endpoints are gone
      (HTTP 404) as of 2026-06. The replacement approach (concurrent
      /trading/1/history calls per symbol) triggers Cloudflare 429 at scale.

      nepsealpha is the 5th cascade source; nepalipaisa (342 stocks) and
      nepsetrading (360 stocks) already cover bulk live market data.

      nepsealpha's primary value now is:
        - get_ohlcv()      → full daily history back to 2012 via /trading/1/history
        - get_live_quote() → single-symbol current price (one request, safe)
        - get_freshness()  → health check (one request, safe)

    Returns:
        [] always — callers cascade to yonepse if needed.
    """
    return []


async def get_live_quote(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Single-symbol live quote via /trading/1/history?resolution=1D.
    Returns normalized stock dict or None.
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return None

    now_ts = int(time.time())
    three_days_ago = now_ts - 3 * 86400

    # Get symbol metadata from search (cached for 10 min)
    search_data = await _get_json_direct(
        f"{_API_BASE}/search",
        params={"limit": 500, "query": ""},
        ttl=_TTL_SEARCH,
    )
    sym_info: Dict[str, Any] = {"symbol": sym}
    if search_data and isinstance(search_data, list):
        for row in search_data:
            if str(row.get("symbol", "")).upper() == sym:
                sym_info = row
                break

    tv = await _get_json_direct(
        f"{_API_BASE}/history",
        params={"symbol": sym, "resolution": "1D", "from": three_days_ago, "to": now_ts},
        ttl=_TTL_LIVE,
    )
    if not tv:
        return None
    return _tv_to_normalized(sym_info, tv)


async def get_market_summary() -> Dict[str, Any]:
    """
    Market-level summary. The old /nepse/1/market-summary endpoint no longer
    exists. Returns {} — callers should cascade to yonepse/merolagani.
    """
    return {}


async def get_indices() -> List[Dict[str, Any]]:
    """
    NEPSE index and sector sub-indices. Old /nepse/1/indices endpoint is gone.
    Returns [] — callers should cascade to yonepse/sharesansar.
    """
    return []


async def get_top_movers() -> Dict[str, List[Dict[str, Any]]]:
    """
    Top gainers / losers / turnover computed from live market snapshot.
    Falls back to empty if live market is unavailable.
    """
    live = await get_live_market()
    if not live:
        return {"top_gainer": [], "top_loser": [], "top_turnover": []}

    by_pct = sorted(live, key=lambda r: r.get("percent_change", 0.0), reverse=True)
    by_turnover = sorted(live, key=lambda r: r.get("volume", 0), reverse=True)
    return {
        "top_gainer": by_pct[:10],
        "top_loser": by_pct[-10:][::-1],
        "top_turnover": by_turnover[:10],
        "source": "nepsealpha_computed",
    }


async def get_company_info(symbol: str) -> Dict[str, Any]:
    """
    Company metadata from the search endpoint.
    Returns {} on failure.
    """
    sym = (symbol or "").strip().upper()
    search_data = await _get_json_direct(
        f"{_API_BASE}/search",
        params={"limit": 500, "query": ""},
        ttl=_TTL_SEARCH,
    )
    if search_data and isinstance(search_data, list):
        for row in search_data:
            if str(row.get("symbol", "")).upper() == sym:
                norm = _normalize_stock({
                    "symbol": sym,
                    "name": row.get("description") or row.get("full_name") or sym,
                    "sector": row.get("sector") or "",
                })
                return norm or {}
    return {}


async def get_ohlcv(symbol: str, period: str = "1y") -> List[Dict[str, Any]]:
    """
    Fetch daily OHLCV historical data for a symbol from nepsealpha.

    Uses /trading/1/history?resolution=1D (TradingView format).
    Data goes back to ~2012 for most NEPSE stocks.

    Args:
        symbol: Stock symbol (e.g. "NABIL").
        period: Time period string — "1m", "3m", "6m", "1y", "3y", "5y", "all".
                Determines the `from` Unix timestamp.

    Returns:
        List of OHLCV dicts: {date, open, high, low, ltp, qty}.
        Newest-first order (most recent bar first).
        Returns [] on failure.
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return []

    # Convert period string to days
    period_days = {
        "1m": 30, "3m": 90, "6m": 180,
        "1y": 365, "3y": 1095, "5y": 1825,
        "all": 5000,
    }
    days = period_days.get(period.lower(), 365)

    now_ts = int(time.time())
    from_ts = now_ts - days * 86400

    tv = await _get_json_direct(
        f"{_API_BASE}/history",
        params={
            "symbol": sym,
            "resolution": "1D",
            "from": from_ts,
            "to": now_ts,
        },
        ttl=_TTL_OHLCV,
    )
    if not tv or tv.get("s") != "ok":
        logger.debug("nepsealpha: no OHLCV data for %s", sym)
        return []

    t_vals = tv.get("t", [])
    o_vals = tv.get("o", [])
    h_vals = tv.get("h", [])
    l_vals = tv.get("l", [])
    c_vals = tv.get("c", [])
    v_vals = tv.get("v", [])

    if not t_vals:
        return []

    import datetime as _dt

    result: List[Dict[str, Any]] = []
    for i in range(len(t_vals) - 1, -1, -1):  # newest-first
        ts = t_vals[i]
        date_str = _dt.date.fromtimestamp(ts).isoformat()
        result.append({
            "date": date_str,
            "open": _sf(o_vals[i]) if i < len(o_vals) else 0.0,
            "high": _sf(h_vals[i]) if i < len(h_vals) else 0.0,
            "low": _sf(l_vals[i]) if i < len(l_vals) else 0.0,
            "ltp": _sf(c_vals[i]) if i < len(c_vals) else 0.0,
            "qty": _si(v_vals[i]) if i < len(v_vals) else 0,
            "source": "nepsealpha",
        })

    logger.info("nepsealpha: %d OHLCV records for %s", len(result), sym)
    return result


async def get_freshness() -> Dict[str, Any]:
    """
    Check if nepsealpha is reachable and returning data.
    Uses a quick single-symbol history call (much faster than full live market).
    """
    sym = "NABIL"
    now_ts = int(time.time())
    two_days_ago = now_ts - 2 * 86400
    tv = await _get_json_direct(
        f"{_API_BASE}/history",
        params={"symbol": sym, "resolution": "1D", "from": two_days_ago, "to": now_ts},
        ttl=60.0,
    )
    reachable = bool(tv and tv.get("s") == "ok" and tv.get("c"))
    ltp = _sf(tv["c"][-1]) if reachable else 0.0
    return {
        "source": "nepsealpha",
        "live_symbol_count": 1 if reachable else 0,
        "reachable": reachable,
        "sample_ltp": ltp,
        "sample_symbol": sym,
    }
