"""
NepaliPaisa Scraper
===================

Scrapes live NEPSE market data from nepalipaisa.com.

The site serves data via a set of JSON API endpoints discovered by inspecting
the site's nepse-data.js bundle (as of 2026-06):

  Priority 1 — confirmed working JSON API paths (no Nepal proxy needed):
    GET /api/GetStockLive?stockSymbol=    → all 345 stocks, full OHLCV
    GET /api/GetNepseLive                 → NEPSE index value
    GET /api/GetTopMarketMovers           → top gainers / losers
    GET /api/GetTodaySharePrice           → today's trade prices
    GET /api/GetLastCloseDate             → last market close date

  Response envelope for /api/GetStockLive:
    {
      "statusCode": 200,
      "message": "Success",
      "result": {
        "stocks": [
          {
            "stockSymbol": "NABIL",
            "companyName": "...",
            "closingPrice": 955.0,    ← LTP
            "maxPrice": 970.0,        ← high
            "minPrice": 945.0,        ← low
            "openingPrice": 969.0,    ← open
            "previousClosing": 950.0, ← prev close
            "differenceRs": 5.0,      ← change
            "percentChange": 0.53,    ← % change
            "volume": 881,
            "amount": 837419.5,       ← turnover
            "noOfTransactions": 0
          }
        ]
      }
    }

  Priority 2 — __NEXT_DATA__ extraction:
    GET /  (HTML)                         parse window.__NEXT_DATA__ JSON blob
                                          or window.__REACT_DATA__ / data-props

  Nepal-IP proxy:
    If NEPAL_PROXY_LIST env var is set (comma-separated proxy URLs whose exit
    nodes are in Nepal), requests automatically route through those proxies.
    Falls back to PROXY_LIST / direct if not configured.
    Note: /api/GetStockLive works globally — Nepal proxy is NOT required.

Features:
  - Exponential backoff retry (up to 3 attempts per endpoint).
  - Per-response TTL cache shared with other scrapers.
  - Returns [] / {} on any failure — never raises to callers.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from .cache import get_cache
from .proxy_rotator import get_rotator

logger = logging.getLogger(__name__)

BASE = "https://www.nepalipaisa.com"
_TIMEOUT = 15.0
_MAX_RETRIES = 3
_TTL_LIVE = 60.0       # seconds – slightly longer since this is a fallback source
_TTL_STATIC = 300.0

# Mimic a browser visiting the site
_EXTRA_HEADERS = {
    "Referer":        "https://www.nepalipaisa.com/",
    "Origin":         "https://www.nepalipaisa.com",
    "sec-fetch-site": "same-origin",
    "sec-fetch-mode": "cors",
    "sec-fetch-dest": "empty",
    "X-Requested-With": "XMLHttpRequest",
}

# Primary endpoint — confirmed working from outside Nepal (no proxy needed).
# Returns all ~345 stocks with full OHLCV in a single call.
_PRIMARY_URL = f"{BASE}/api/GetStockLive"

# Fallback JSON API endpoint candidates (tried if primary fails)
_API_CANDIDATES: List[str] = [
    f"{BASE}/api/GetTodaySharePrice",
    f"{BASE}/api/GetTopMarketMovers",
    # Legacy routes (may return 404 on redesigned site, kept as last resort)
    f"{BASE}/LiveMarket/GetLiveMarket",
    f"{BASE}/StockQuote/GetAllStocks",
    f"{BASE}/api/live-market",
    f"{BASE}/api/market/today",
]


# ─── low-level fetch ─────────────────────────────────────────────────────────

async def _get_json_direct(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    ttl: float = _TTL_LIVE,
) -> Optional[Any]:
    """
    GET a URL as JSON with NO proxy (direct connection) + TTL cache.
    Used for endpoints confirmed to work from outside Nepal.
    """
    cache = get_cache()
    ck = f"nepalipaisa::direct::{url}::{sorted((params or {}).items())}"
    cached = cache.get(ck)
    if cached is not None:
        return cached

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        **_EXTRA_HEADERS,
    }

    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT,
            follow_redirects=True,
            verify=False,
            headers=headers,
        ) as client:
            r = await client.get(url, params=params)
            if r.status_code == 200:
                try:
                    data = r.json()
                except Exception:
                    text = r.text.strip().lstrip("\ufeff")
                    try:
                        data = json.loads(text)
                    except Exception:
                        return None
                cache.set(ck, data, ttl)
                logger.debug("nepalipaisa direct: %s → %d", url, r.status_code)
                return data
            else:
                logger.debug("nepalipaisa direct: %s → HTTP %s", url, r.status_code)
    except Exception as exc:
        logger.debug("nepalipaisa direct: %s error: %s", url, exc)

    return None


async def _get_json(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    ttl: float = _TTL_LIVE,
) -> Optional[Any]:
    """GET a URL as JSON with proxy rotation, jitter, retry, and TTL cache."""
    cache = get_cache()
    ck = f"nepalipaisa::json::{url}::{sorted((params or {}).items())}"
    cached = cache.get(ck)
    if cached is not None:
        return cached

    rotator = get_rotator()
    last_exc: Optional[Exception] = None

    for attempt in range(_MAX_RETRIES):
        headers, proxy_url = rotator.next_async()
        headers.update(_EXTRA_HEADERS)
        headers["Accept"] = "application/json, text/plain, */*"
        proxies = rotator.httpx_proxies(proxy_url)

        await rotator.exponential_jitter(attempt, base_ms=300.0, cap_ms=6_000.0)

        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT,
                follow_redirects=True,
                verify=False,
                proxies=proxies,
                headers=headers,
            ) as client:
                r = await client.get(url, params=params)

                if r.status_code == 200:
                    rotator.report_success(proxy_url)
                    try:
                        data = r.json()
                    except Exception:
                        text = r.text.strip().lstrip("\ufeff")
                        try:
                            data = json.loads(text)
                        except Exception:
                            rotator.report_failure(proxy_url)
                            continue
                    cache.set(ck, data, ttl)
                    return data
                elif r.status_code == 429:
                    retry_after = float(r.headers.get("Retry-After", "120"))
                    rotator.report_rate_limited(proxy_url, retry_after)
                    import asyncio as _aio
                    await _aio.sleep(min(retry_after, 15.0))
                elif r.status_code in (403, 503):
                    rotator.report_failure(proxy_url)
                    logger.debug("nepalipaisa %s attempt %d: HTTP %s (anti-bot/geo-block)", url, attempt + 1, r.status_code)
                else:
                    rotator.report_failure(proxy_url)
                    logger.debug("nepalipaisa %s attempt %d: HTTP %s", url, attempt + 1, r.status_code)

        except Exception as exc:  # noqa: BLE001
            rotator.report_failure(proxy_url)
            last_exc = exc
            logger.debug("nepalipaisa %s attempt %d error: %s", url, attempt + 1, exc)

    return None


async def _get_html(url: str, ttl: float = _TTL_LIVE) -> Optional[str]:
    """GET a URL as HTML with Nepal proxy, jitter, retry, and TTL cache."""
    cache = get_cache()
    ck = f"nepalipaisa::html::{url}"
    cached = cache.get(ck)
    if cached is not None:
        return cached

    rotator = get_rotator()
    last_exc: Optional[Exception] = None

    for attempt in range(_MAX_RETRIES):
        headers, proxy_url = rotator.next_async()
        headers["Accept"] = "text/html,application/xhtml+xml,*/*;q=0.8"
        headers["Referer"] = "https://www.nepalipaisa.com/"
        proxies = rotator.httpx_proxies(proxy_url)

        await rotator.exponential_jitter(attempt, base_ms=500.0, cap_ms=8_000.0)

        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT,
                follow_redirects=True,
                verify=False,
                proxies=proxies,
                headers=headers,
            ) as client:
                r = await client.get(url)
                if r.status_code == 200:
                    rotator.report_success(proxy_url)
                    html = r.text
                    cache.set(ck, html, ttl)
                    return html
                elif r.status_code == 429:
                    retry_after = float(r.headers.get("Retry-After", "120"))
                    rotator.report_rate_limited(proxy_url, retry_after)
                    import asyncio as _aio
                    await _aio.sleep(min(retry_after, 15.0))
                else:
                    rotator.report_failure(proxy_url)
                    logger.debug("nepalipaisa HTML attempt %d: HTTP %s (url=%s)", attempt + 1, r.status_code, url)

        except Exception as exc:  # noqa: BLE001
            rotator.report_failure(proxy_url)
            last_exc = exc
            logger.debug("nepalipaisa HTML attempt %d: %s", attempt + 1, exc)

    return None


# ─── data extraction helpers ─────────────────────────────────────────────────

def _extract_next_data(html: str) -> Optional[Any]:
    """
    Extract JSON data embedded by Next.js / React SSR into the page HTML.

    Tries (in order):
      1. <script id="__NEXT_DATA__" ...>{ ... }</script>
      2. window.__REACT_DATA__ = { ... }
      3. window.__INITIAL_STATE__ = { ... }
      4. data-props='{ ... }' attribute on the root <div>
    """
    patterns = [
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>\s*(\{.+?\})\s*</script>',
        r'window\.__REACT_DATA__\s*=\s*(\{.+?\})\s*;',
        r'window\.__INITIAL_STATE__\s*=\s*(\{.+?\})\s*;',
        r'window\.__APP_DATA__\s*=\s*(\{.+?\})\s*;',
        r'data-props=["\'](\{.+?\})["\']',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                continue
    return None


def _find_stock_list(obj: Any, depth: int = 0) -> Optional[List[Dict]]:
    """
    Recursively search a parsed JSON object for a list of stock dicts.
    Heuristic: a list with ≥5 items each containing a symbol-like key.
    """
    if depth > 8:
        return None

    if isinstance(obj, list) and len(obj) >= 5:
        # Check if looks like stock data
        sample = obj[0] if obj else {}
        if isinstance(sample, dict):
            keys = {k.lower() for k in sample}
            sym_keys = {"symbol", "ticker", "scrip", "stock", "s", "sym"}
            price_keys = {"ltp", "price", "close", "lasttradedprice", "lastprice", "rate"}
            if sym_keys & keys and price_keys & keys:
                return obj
    elif isinstance(obj, dict):
        for v in obj.values():
            result = _find_stock_list(v, depth + 1)
            if result is not None:
                return result
    return None


def _normalize_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Normalize a raw nepalipaisa API row to our canonical format.

    Handles both the new /api/GetStockLive format:
        stockSymbol, closingPrice, maxPrice, minPrice, openingPrice,
        previousClosing, differenceRs, percentChange, volume, amount,
        noOfTransactions, companyName
    and legacy formats with symbol/ltp/high/low field names.
    """
    sym = str(
        row.get("stockSymbol") or row.get("symbol") or row.get("ticker")
        or row.get("scrip") or row.get("Script") or row.get("s") or ""
    ).strip().upper()
    if not sym or not re.match(r"^[A-Z0-9]{2,12}$", sym):
        return None

    # LTP: new API uses closingPrice; legacy uses ltp/price/close
    ltp = _sf(
        row.get("closingPrice") or row.get("ltp") or row.get("LTP")
        or row.get("lastTradedPrice") or row.get("price") or row.get("closePrice")
        or row.get("Rate") or row.get("close") or row.get("LastPrice")
    )
    if not ltp:
        return None

    # Previous close: new API uses previousClosing
    prev = _sf(
        row.get("previousClosing") or row.get("previousClose") or row.get("prevClose")
        or row.get("PrevClose") or row.get("lastClose") or row.get("PreviousClose") or ltp
    )
    pct = _sf(
        row.get("percentChange") or row.get("pChange") or row.get("percent_change")
        or row.get("PercentageChange") or row.get("Change%")
        or (((ltp - prev) / prev * 100.0) if prev else 0.0)
    )

    return {
        "symbol":         sym,
        "name":           str(
            row.get("companyName") or row.get("name") or row.get("CompanyName") or sym
        ).strip(),
        "ltp":            ltp,
        "previous_close": prev,
        "change":         round(ltp - prev, 2),
        "percent_change": round(pct, 2),
        # new API: maxPrice/minPrice/openingPrice; legacy: high/low/open
        "open":           _sf(row.get("openingPrice") or row.get("open") or row.get("openPrice") or row.get("Open")),
        "high":           _sf(row.get("maxPrice") or row.get("high") or row.get("highPrice") or row.get("High")),
        "low":            _sf(row.get("minPrice") or row.get("low")  or row.get("lowPrice")  or row.get("Low")),
        "volume":         _si(row.get("volume") or row.get("qty") or row.get("Quantity") or row.get("totalTradeQty")),
        # new API: amount = turnover (NPR traded value)
        "turnover":       _sf(row.get("amount") or row.get("turnover") or row.get("tradedValue") or row.get("Turnover")),
        "trades":         _si(row.get("noOfTransactions") or row.get("trades") or row.get("totalTrades") or row.get("NoOfTrans")),
        "source":         "nepalipaisa",
    }


# ─── safe helpers ────────────────────────────────────────────────────────────

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


# ─── public API ──────────────────────────────────────────────────────────────

def _unwrap_rows(data: Any, url: str) -> List[Dict[str, Any]]:
    """
    Unwrap API envelopes and return a list of raw stock dicts.

    Handles:
      - New nepalipaisa format: {"statusCode":200, "result": {"stocks": [...]}}
      - Generic REST envelopes: {"data": [...]} / {"result": [...]} / [...]
      - ASP.NET double-encoded: {"d": "<json-string>"}
    """
    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    # New format: result.stocks
    result_obj = data.get("result") or data.get("Result") or {}
    if isinstance(result_obj, dict):
        stocks = result_obj.get("stocks") or result_obj.get("Stocks") or []
        if isinstance(stocks, list) and stocks:
            return stocks

    # Generic envelopes
    rows = (
        data.get("data") or data.get("Data")
        or data.get("stocks") or data.get("Stocks")
        or data.get("d") or []
    )
    # ASP.NET double-encoded JSON string
    if isinstance(rows, str):
        try:
            rows = json.loads(rows)
        except Exception:
            rows = []

    return rows if isinstance(rows, list) else []


async def get_live_market() -> List[Dict[str, Any]]:
    """
    Fetch live NEPSE market data from nepalipaisa.com.

    Strategy (in priority order):
      1. Primary: /api/GetStockLive?stockSymbol= — confirmed working globally,
         returns all ~345 stocks with full OHLCV in one call (no proxy needed).
      2. Fallback JSON API candidates (GetTodaySharePrice etc.).
      3. Homepage HTML → __NEXT_DATA__ extraction.

    Uses Nepal-IP proxy pool (NEPAL_PROXY_LIST) when configured.
    Returns [] on failure — never raises.
    """
    # ── Strategy 1: primary endpoint — direct (no proxy), confirmed working ────
    data = await _get_json_direct(_PRIMARY_URL, params={"stockSymbol": ""}, ttl=_TTL_LIVE)
    if data:
        rows = _unwrap_rows(data, _PRIMARY_URL)
        if isinstance(rows, list) and len(rows) >= 5:
            result = [r for r in (_normalize_row(row) for row in rows if isinstance(row, dict)) if r]
            if len(result) >= 5:
                logger.info("nepalipaisa: fetched %d symbols via GetStockLive", len(result))
                return result

    # ── Strategy 2: fallback JSON API endpoints ───────────────────────────────
    for url in _API_CANDIDATES:
        data = await _get_json(url, ttl=_TTL_LIVE)
        if not data:
            continue
        rows = _unwrap_rows(data, url)
        if isinstance(rows, list) and len(rows) >= 5:
            result = [r for r in (_normalize_row(row) for row in rows if isinstance(row, dict)) if r]
            if len(result) >= 5:
                logger.info("nepalipaisa: fetched %d symbols via JSON API %s", len(result), url)
                return result

    # ── Strategy 3: extract from page HTML ───────────────────────────────────
    html = await _get_html(BASE + "/", ttl=_TTL_LIVE)
    if html:
        embedded = _extract_next_data(html)
        if embedded:
            raw_list = _find_stock_list(embedded)
            if raw_list:
                result = [r for r in (_normalize_row(row) for row in raw_list if isinstance(row, dict)) if r]
                if result:
                    logger.info("nepalipaisa: fetched %d symbols via page HTML __NEXT_DATA__", len(result))
                    return result

    logger.warning("nepalipaisa: primary + %d fallback endpoints + HTML all failed", len(_API_CANDIDATES))
    return []


async def get_live_quote(symbol: str) -> Optional[Dict[str, Any]]:
    """Single-symbol live quote (O(N) scan, result is cached)."""
    sym_u = (symbol or "").strip().upper()
    for row in await get_live_market():
        if row.get("symbol", "").upper() == sym_u:
            return row
    return None


async def get_freshness() -> Dict[str, Any]:
    """Check reachability and return a freshness dict for the health endpoint."""
    live = await get_live_market()
    return {
        "source": "nepalipaisa",
        "live_symbol_count": len(live) if live else 0,
        "reachable": len(live) > 0,
    }
