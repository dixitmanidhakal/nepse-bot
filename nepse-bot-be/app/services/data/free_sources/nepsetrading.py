"""
NepseTrading Scraper
====================

Scrapes live NEPSE market data from nepsetrading.com.

The site is a Next.js App Router application with React Server Components.
Stock data is server-side rendered (SSR) into the HTML response for
/market/stocks as RSC (React Server Component) payload blobs.

  Technique 0 — Public REST API (PRIMARY, confirmed working 2026-06, no auth):
    GET  https://api.nepsetrading.com/live-stock-data
         → 363 stocks, fields: symbol, date, open, high, low, close, volume,
           previous_close, point_change, percentage_change
    GET  https://api.nepsetrading.com/explore-market/stocks/all?limit=500
         → 374 stocks, paginated (limit=500 gives all in one request)
    Both accessible globally without auth or Nepal IP.

  Technique 1 — RSC payload extraction (HTML fallback):
    GET  /market/stocks        HTML page
    Next.js App Router embeds SSR data in `self.__next_f.push([1,"..."])`
    script blocks. The stock data JSON array contains:
      {symbol, fullname, latesttransactionprice, open, high, low, volume,
       previousclosing, percentagechange, sector, sub_index}
    Returns ~271 active traded stocks.

  Technique 2 — __NEXT_DATA__ / homepage HTML fallback.

  Technique 3 — API endpoint probing (last resort).

  Nepal-IP proxy:
    Routes through NEPAL_PROXY_LIST proxies if configured.
    Falls back to PROXY_LIST / direct if not set.
    Note: /market/stocks is accessible globally — no Nepal proxy required.

Returns [] on any failure — never raises.
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

BASE = "https://www.nepsetrading.com"
API_BASE = "https://api.nepsetrading.com"
_TIMEOUT = 15.0
_MAX_RETRIES = 3
_TTL_LIVE = 60.0
_TTL_HTML = 90.0    # HTML page (needed to extract buildId)

# ── Confirmed public REST API endpoints (no auth, no Nepal proxy needed) ─────
# Discovered 2026-06-02 by scanning JS bundles and testing api.nepsetrading.com.
# These are the actual API endpoints the Next.js frontend calls.
_REST_PRIMARY    = f"{API_BASE}/live-stock-data"          # 363 stocks, clean OHLCV
_REST_EXPLORE    = f"{API_BASE}/explore-market/stocks/all"  # 374 stocks, paginated

_EXTRA_HEADERS_JSON = {
    "Referer":        "https://nepsetrading.com/",
    "Origin":         "https://nepsetrading.com",
    "sec-fetch-site": "same-origin",
    "sec-fetch-mode": "cors",
    "sec-fetch-dest": "empty",
}

# Ordered list of Next.js API route candidates and API sub-domain paths
_API_CANDIDATES: List[str] = [
    f"{BASE}/api/live-market",
    f"{BASE}/api/market/today",
    f"{BASE}/api/market",
    f"{BASE}/api/nepse/live",
    f"{BASE}/api/today-price",
    f"{API_BASE}/nepse/live",
    f"{API_BASE}/live-market",
    f"{API_BASE}/api/live-market",
    f"{API_BASE}/api/v1/live-market",
    f"{API_BASE}/api/v1/market/today",
    f"{API_BASE}/today-price",
]


# ─── low-level fetch ─────────────────────────────────────────────────────────

async def _fetch_direct(
    url: str,
    accept: str = "text/html,application/xhtml+xml,*/*;q=0.8",
    ttl: float = _TTL_HTML,
) -> Optional[str]:
    """
    GET a URL as HTML/text with NO proxy (direct connection) + TTL cache.
    Used for /market/stocks which is confirmed to work from outside Nepal.
    """
    cache = get_cache()
    ck = f"nepsetrading::direct::{url}::{accept}"
    cached = cache.get(ck)
    if cached is not None:
        return cached

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": accept,
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nepsetrading.com/",
        **_EXTRA_HEADERS_JSON,
    }

    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT,
            follow_redirects=True,
            verify=False,
            headers=headers,
        ) as client:
            r = await client.get(url)
            if r.status_code == 200:
                text = r.text
                cache.set(ck, text, ttl)
                logger.debug("nepsetrading direct: %s → %d (len=%d)", url, r.status_code, len(text))
                return text
            else:
                logger.debug("nepsetrading direct: %s → HTTP %s", url, r.status_code)
    except Exception as exc:
        logger.debug("nepsetrading direct: %s error: %s", url, exc)

    return None


async def _fetch_json_direct(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    ttl: float = _TTL_LIVE,
) -> Optional[Any]:
    """
    GET a URL as JSON with NO proxy (direct connection) + TTL cache.
    Used for REST API endpoints confirmed to work from outside Nepal.
    """
    cache = get_cache()
    ck = f"nepsetrading::json_direct::{url}::{sorted((params or {}).items())}"
    cached = cache.get(ck)
    if cached is not None:
        return cached

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nepsetrading.com/",
        "Origin": "https://www.nepsetrading.com",
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
                    try:
                        data = json.loads(r.text.strip().lstrip("\ufeff"))
                    except Exception:
                        return None
                cache.set(ck, data, ttl)
                logger.debug("nepsetrading json_direct: %s → %d", url, r.status_code)
                return data
            else:
                logger.debug("nepsetrading json_direct: %s → HTTP %s", url, r.status_code)
    except Exception as exc:
        logger.debug("nepsetrading json_direct: %s error: %s", url, exc)

    return None


async def _fetch(
    url: str,
    accept: str = "application/json",
    params: Optional[Dict[str, Any]] = None,
    ttl: float = _TTL_LIVE,
) -> Optional[Any]:
    """
    GET a URL with Nepal proxy, jitter, retry, and TTL cache.
    Returns parsed JSON if Accept is application/json, otherwise raw text.
    """
    cache = get_cache()
    ck = f"nepsetrading::{url}::{accept}::{sorted((params or {}).items())}"
    cached = cache.get(ck)
    if cached is not None:
        return cached

    rotator = get_rotator()
    last_exc: Optional[Exception] = None

    for attempt in range(_MAX_RETRIES):
        headers, proxy_url = rotator.next_async()
        headers["Accept"] = accept
        if "json" in accept:
            headers.update(_EXTRA_HEADERS_JSON)
        else:
            headers["Referer"] = "https://nepsetrading.com/"

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
                    if "json" in accept or "json" in r.headers.get("content-type", ""):
                        try:
                            data = r.json()
                        except Exception:
                            try:
                                data = json.loads(r.text.strip().lstrip("\ufeff"))
                            except Exception:
                                rotator.report_failure(proxy_url)
                                continue
                    else:
                        data = r.text
                    cache.set(ck, data, ttl)
                    return data

                elif r.status_code == 429:
                    retry_after = float(r.headers.get("Retry-After", "120"))
                    rotator.report_rate_limited(proxy_url, retry_after)
                    import asyncio as _aio
                    await _aio.sleep(min(retry_after, 15.0))
                elif r.status_code in (403, 503):
                    rotator.report_failure(proxy_url)
                    logger.debug("nepsetrading %s attempt %d: HTTP %s (anti-bot)", url, attempt + 1, r.status_code)
                elif r.status_code == 404:
                    rotator.report_success(proxy_url)   # reachable but path missing
                    logger.debug("nepsetrading %s: 404 — path not found", url)
                    return None                          # no point retrying
                else:
                    rotator.report_failure(proxy_url)
                    logger.debug("nepsetrading %s attempt %d: HTTP %s", url, attempt + 1, r.status_code)

        except Exception as exc:  # noqa: BLE001
            rotator.report_failure(proxy_url)
            last_exc = exc
            logger.debug("nepsetrading %s attempt %d: %s", url, attempt + 1, exc)

    return None


# ─── Next.js data extraction ─────────────────────────────────────────────────

def _extract_rsc_stocks(html: str) -> Optional[List[Dict]]:
    """
    Extract stock data from Next.js App Router RSC (React Server Component)
    payload blobs embedded in the page HTML.

    Next.js App Router embeds SSR data as:
      <script>self.__next_f.push([1,"...json-escaped-data..."])</script>

    The stock array sits inside a blob like:
      2d:["$","$L64",null,{"data":[{"symbol":"KHPL",...},...],"columns":[...]}]

    We unescape the blob (\" → ") and extract the first JSON array containing
    objects with "symbol" + "latesttransactionprice" keys.
    """
    # Collect all RSC push blobs (the content between the outer quotes)
    blobs = re.findall(
        r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*?)"\]\)',
        html,
        re.DOTALL,
    )

    for blob in blobs:
        # Skip small blobs that can't contain stock data
        if len(blob) < 500 or "symbol" not in blob:
            continue
        if "latesttransactionprice" not in blob:
            continue

        # Unescape JS string escaping: \" → "  and \n → newline
        try:
            unescaped = blob.replace('\\"', '"').replace("\\n", "\n").replace("\\'", "'")
        except Exception:
            continue

        # Find the JSON array of stock objects
        m = re.search(r'\[\{"symbol":.+?\}\]', unescaped, re.DOTALL)
        if not m:
            continue

        try:
            arr = json.loads(m.group(0))
            if isinstance(arr, list) and len(arr) >= 5:
                sample = arr[0]
                if isinstance(sample, dict) and "symbol" in sample:
                    return arr
        except Exception:
            continue

    return None


def _extract_build_id(html: str) -> Optional[str]:
    """
    Extract the Next.js buildId from the <script id="__NEXT_DATA__"> blob.
    The buildId is required to fetch _next/data/{buildId}/index.json.
    (Legacy Pages Router support.)
    """
    m = re.search(r'"buildId"\s*:\s*"([^"]+)"', html)
    return m.group(1) if m else None


def _extract_next_data(html: str) -> Optional[Any]:
    """
    Parse the <script id="__NEXT_DATA__"> JSON blob embedded in the page HTML.
    Returns the parsed object or None. (Legacy Pages Router support.)
    """
    m = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>\s*(\{.+?\})\s*</script>',
        html,
        re.DOTALL,
    )
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def _find_stock_list(obj: Any, depth: int = 0) -> Optional[List[Dict]]:
    """
    Recursively walk a parsed JSON object to find a list of stock data rows.
    Heuristic: list of ≥5 dicts each containing a symbol + price field.
    """
    if depth > 10:
        return None

    if isinstance(obj, list) and len(obj) >= 5:
        sample = obj[0] if obj else {}
        if isinstance(sample, dict):
            keys_lower = {k.lower() for k in sample}
            sym_keys = {"symbol", "ticker", "scrip", "stock", "sym", "s", "script"}
            price_keys = {"ltp", "price", "close", "lasttradedprice", "lastprice", "rate", "closeprice"}
            if sym_keys & keys_lower and price_keys & keys_lower:
                return obj
    elif isinstance(obj, dict):
        # Prioritize keys likely to hold market data
        for priority_key in ("data", "stocks", "market", "securities", "result", "pageProps"):
            if priority_key in obj:
                res = _find_stock_list(obj[priority_key], depth + 1)
                if res is not None:
                    return res
        # General search
        for v in obj.values():
            res = _find_stock_list(v, depth + 1)
            if res is not None:
                return res
    return None


# ─── data normalization ───────────────────────────────────────────────────────

def _normalize_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Normalize a raw nepsetrading row to our canonical stock format.

    Handles both the RSC payload format (primary, from /market/stocks HTML):
        symbol, fullname, latesttransactionprice, latesttransactionvolume,
        pointchange, percentagechange, open, low, high, volume,
        previousclosing, timestamp, sector, sub_index
    and legacy API/JSON formats with ltp/price/close field names.
    """
    sym = str(
        row.get("symbol") or row.get("ticker") or row.get("scrip")
        or row.get("Script") or row.get("stockSymbol") or row.get("s") or ""
    ).strip().upper()
    if not sym or not re.match(r"^[A-Z0-9]{2,12}$", sym):
        return None

    # LTP: REST /live-stock-data uses "close"; RSC uses "latesttransactionprice"; legacy "ltp"
    ltp = _sf(
        row.get("close") or row.get("latesttransactionprice") or row.get("ltp")
        or row.get("LTP") or row.get("lastTradedPrice") or row.get("price")
        or row.get("closePrice") or row.get("Rate") or row.get("LastPrice")
    )
    if not ltp:
        return None

    # Previous close: REST uses "previous_close"; RSC uses "previousclosing"
    prev = _sf(
        row.get("previous_close") or row.get("previousclosing") or row.get("previousClose")
        or row.get("prevClose") or row.get("PreviousClose") or row.get("lastClose")
        or row.get("previousDayClose") or ltp
    )
    # Percentage change: REST uses "percentage_change"; RSC uses "percentagechange"
    pct = _sf(
        row.get("percentage_change") or row.get("percentagechange")
        or row.get("percentChange") or row.get("pChange") or row.get("PercentChange")
        or row.get("percent_change") or row.get("changePercent")
        or (((ltp - prev) / prev * 100.0) if prev else 0.0)
    )

    return {
        "symbol":         sym,
        "name":           str(
            row.get("fullname") or row.get("name") or row.get("companyName")
            or row.get("CompanyName") or sym
        ).strip(),
        "ltp":            ltp,
        "previous_close": prev,
        "change":         round(ltp - prev, 2),
        "percent_change": round(pct, 2),
        "open":           _sf(row.get("open") or row.get("openPrice") or row.get("Open")),
        "high":           _sf(row.get("high") or row.get("highPrice") or row.get("High")),
        "low":            _sf(row.get("low")  or row.get("lowPrice")  or row.get("Low")),
        # RSC has both "volume" and "latesttransactionvolume"; prefer volume
        "volume":         _si(
            row.get("volume") or row.get("latesttransactionvolume")
            or row.get("qty") or row.get("Quantity") or row.get("totalTradeQuantity")
        ),
        "turnover":       _sf(row.get("turnover") or row.get("tradedValue") or row.get("Turnover")),
        "trades":         _si(row.get("trades") or row.get("totalTrades") or row.get("noOfTrans")),
        "sector":         str(row.get("sector") or row.get("sub_index") or "").strip(),
        "source":         "nepsetrading",
    }


def _parse_rows(raw: Any) -> List[Dict[str, Any]]:
    """Unwrap common API envelopes and normalize a list of stock rows."""
    if isinstance(raw, dict):
        rows = (
            raw.get("data") or raw.get("Data")
            or raw.get("stocks") or raw.get("securities")
            or raw.get("result") or raw.get("Result")
            or []
        )
        if isinstance(rows, str):
            try:
                rows = json.loads(rows)
            except Exception:
                rows = []
    elif isinstance(raw, list):
        rows = raw
    else:
        return []

    result = [r for r in (_normalize_row(row) for row in rows if isinstance(row, dict)) if r]
    return result


# ─── safe helpers ─────────────────────────────────────────────────────────────

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

async def get_live_market() -> List[Dict[str, Any]]:
    """
    Fetch live NEPSE market data from nepsetrading.com.

    Strategy (in priority order):
      1. Fetch /market/stocks HTML and extract RSC payload blobs containing
         SSR stock data (App Router React Server Components). This is the
         primary technique — no proxy required, returns ~271 active stocks.
      2. Parse __NEXT_DATA__ embedded JSON from the homepage HTML
         (legacy Pages Router fallback).
      3. Probe known JSON API endpoint patterns.

    All requests route through NEPAL_PROXY_LIST proxies when configured.
    Returns [] on failure — never raises.
    """
    # ── Technique 0: REST API — direct JSON (fastest, no proxy needed) ───────
    # api.nepsetrading.com returns public JSON endpoints with no auth required.
    # /live-stock-data → 363 stocks, OHLCV; /explore-market/stocks/all → 374 stocks.

    raw = await _fetch_json_direct(_REST_PRIMARY, ttl=_TTL_LIVE)
    if isinstance(raw, list) and len(raw) >= 5:
        result = [r for r in (_normalize_row(row) for row in raw if isinstance(row, dict)) if r]
        if len(result) >= 5:
            logger.info("nepsetrading: fetched %d symbols via REST /live-stock-data (direct)", len(result))
            return result

    raw2 = await _fetch_json_direct(_REST_EXPLORE, params={"limit": 500}, ttl=_TTL_LIVE)
    if isinstance(raw2, dict):
        rows_2 = raw2.get("data") or []
        if isinstance(rows_2, list) and len(rows_2) >= 5:
            result = [r for r in (_normalize_row(row) for row in rows_2 if isinstance(row, dict)) if r]
            if len(result) >= 5:
                logger.info("nepsetrading: fetched %d symbols via REST /explore-market/stocks/all (direct)", len(result))
                return result

    # ── Technique 1: RSC payload extraction from /market/stocks HTML ─────────
    stocks_html = await _fetch_direct(BASE + "/market/stocks", ttl=_TTL_HTML)

    if isinstance(stocks_html, str) and stocks_html:
        stock_list = _extract_rsc_stocks(stocks_html)
        if stock_list:
            result = [r for r in (_normalize_row(row) for row in stock_list if isinstance(row, dict)) if r]
            if len(result) >= 5:
                logger.info("nepsetrading: fetched %d symbols via RSC /market/stocks (direct)", len(result))
                return result

    # ── Technique 2: homepage HTML — RSC or __NEXT_DATA__ fallback (direct) ──
    html = await _fetch_direct(BASE + "/", ttl=_TTL_HTML)

    if isinstance(html, str) and html:
        # Try RSC extraction on homepage too
        stock_list = _extract_rsc_stocks(html)
        if stock_list:
            result = [r for r in (_normalize_row(row) for row in stock_list if isinstance(row, dict)) if r]
            if len(result) >= 5:
                logger.info("nepsetrading: fetched %d symbols via RSC /", len(result))
                return result

        # Legacy _next/data (Pages Router)
        build_id = _extract_build_id(html)
        if build_id:
            data_url = f"{BASE}/_next/data/{build_id}/index.json"
            page_json = await _fetch(data_url, ttl=_TTL_LIVE)
            if page_json:
                stock_list = _find_stock_list(page_json)
                if stock_list:
                    result = [r for r in (_normalize_row(row) for row in stock_list if isinstance(row, dict)) if r]
                    if len(result) >= 5:
                        logger.info("nepsetrading: fetched %d symbols via _next/data/%s", len(result), build_id[:8])
                        return result

        # Legacy __NEXT_DATA__ (Pages Router)
        embedded = _extract_next_data(html)
        if embedded:
            stock_list = _find_stock_list(embedded)
            if stock_list:
                result = [r for r in (_normalize_row(row) for row in stock_list if isinstance(row, dict)) if r]
                if len(result) >= 5:
                    logger.info("nepsetrading: fetched %d symbols via __NEXT_DATA__", len(result))
                    return result

    # ── Technique 3: probe JSON API endpoints ─────────────────────────────────
    for url in _API_CANDIDATES:
        raw = await _fetch(url, ttl=_TTL_LIVE)
        if not raw:
            continue
        result = _parse_rows(raw)
        if len(result) >= 5:
            logger.info("nepsetrading: fetched %d symbols via API %s", len(result), url)
            return result

    logger.warning(
        "nepsetrading: all techniques failed — "
        "try setting NEPAL_PROXY_LIST if the site geo-blocks your IP"
    )
    return []


async def get_live_quote(symbol: str) -> Optional[Dict[str, Any]]:
    """Single-symbol live quote (O(N) scan, list cached)."""
    sym_u = (symbol or "").strip().upper()
    for row in await get_live_market():
        if row.get("symbol", "").upper() == sym_u:
            return row
    return None


async def get_freshness() -> Dict[str, Any]:
    """Check reachability and return a freshness dict for the health endpoint."""
    live = await get_live_market()
    return {
        "source": "nepsetrading",
        "live_symbol_count": len(live) if live else 0,
        "reachable": len(live) > 0,
    }
