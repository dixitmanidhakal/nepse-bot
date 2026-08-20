"""
MeroLagani Scraper
==================

Scrapes live stock market data from merolagani.com — a popular free NEPSE
data aggregator accessible from outside Nepal.

Endpoints used:
  GET  /LatestMarket.aspx
       → HTML table of all live stock prices (all listed symbols).
       Columns: Symbol, LTP, % Change, High, Low, Volume, PClose
       This replaced the old TechnicalHandler.ashx JSON API (now broken).

  GET  /StockQuote.aspx?symbol={SYMBOL}
       → HTML page for per-symbol quote + fundamentals fallback

Features:
  - Proxy rotation via ProxyRotator (configure PROXY_LIST env var).
  - Random jitter between requests to avoid rate-limiting.
  - Retry with exponential backoff (up to 3 attempts per call).
  - Per-response TTL cache via the shared TTLCache.
  - Returns [] / {} on any failure — never raises to callers.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional

import httpx

from .cache import get_cache
from .proxy_rotator import get_rotator

logger = logging.getLogger(__name__)

BASE = "https://merolagani.com"
_TIMEOUT = 15.0
_MAX_RETRIES = 3
_TTL_LIVE = 45.0       # seconds – live market rotates ~45 s
_TTL_STATIC = 300.0    # seconds – fundamentals / company info

# Referer / Origin for merolagani requests
_EXTRA_HEADERS = {
    "Referer": "https://merolagani.com/",
    "Origin":  "https://merolagani.com",
    "sec-fetch-site": "same-origin",
    "sec-fetch-mode": "cors",
    "sec-fetch-dest": "empty",
}


# ─── low-level fetch ─────────────────────────────────────────────────────────

async def _get_html(
    path: str,
    params: Optional[Dict[str, Any]] = None,
    ttl: float = _TTL_LIVE,
) -> Optional[str]:
    """GET {BASE}/{path} as raw HTML text with proxy rotation, jitter, retry, and cache."""
    cache = get_cache()
    cache_key = f"merolagani::html::{path}::{sorted((params or {}).items())}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    rotator = get_rotator()
    last_exc: Optional[Exception] = None

    for attempt in range(_MAX_RETRIES):
        headers, proxy_url = rotator.next_async()
        headers.update(_EXTRA_HEADERS)
        headers["Accept"] = "text/html,application/xhtml+xml,*/*;q=0.8"
        proxies = rotator.httpx_proxies(proxy_url)

        # Exponential backoff: 200–400 ms → 200–800 ms → 200–1600 ms
        await rotator.exponential_jitter(attempt, base_ms=200.0, cap_ms=5_000.0)

        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT,
                follow_redirects=True,
                verify=False,
                proxies=proxies,
                headers=headers,
            ) as client:
                url = f"{BASE}/{path.lstrip('/')}"
                r = await client.get(url, params=params)

                if r.status_code == 200 and "404" not in str(r.url):
                    rotator.report_success(proxy_url)
                    html = r.text
                    cache.set(cache_key, html, ttl)
                    return html
                elif r.status_code == 429:
                    retry_after = float(r.headers.get("Retry-After", "120"))
                    rotator.report_rate_limited(proxy_url, retry_after)
                    logger.warning(
                        "merolagani %s attempt %d: 429 rate-limited, backing off %.0fs",
                        path, attempt + 1, retry_after,
                    )
                    import asyncio as _aio
                    await _aio.sleep(min(retry_after, 15.0))
                elif r.status_code in (403, 503):
                    rotator.report_failure(proxy_url)
                    logger.warning(
                        "merolagani %s attempt %d: HTTP %s (anti-bot)",
                        path, attempt + 1, r.status_code,
                    )
                else:
                    rotator.report_failure(proxy_url)
                    logger.warning(
                        "merolagani %s attempt %d: HTTP %s (url=%s)",
                        path, attempt + 1, r.status_code, r.url,
                    )

        except Exception as exc:  # noqa: BLE001
            rotator.report_failure(proxy_url)
            last_exc = exc
            logger.debug(
                "merolagani %s attempt %d error: %s",
                path, attempt + 1, exc,
            )

    # ── Direct fallback ─────────────────────────────────────────────────────
    # merolagani.com is globally accessible (not geo-blocked). If all proxy
    # attempts failed (e.g. free proxies blocked by Cloudflare), try once
    # more with a direct connection so data is never completely lost.
    logger.debug("merolagani %s: proxy retries exhausted — trying direct connection", path)
    try:
        direct_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "DNT": "1",
        }
        direct_headers.update(_EXTRA_HEADERS)
        async with httpx.AsyncClient(
            timeout=_TIMEOUT,
            follow_redirects=True,
            verify=False,
            headers=direct_headers,
        ) as direct_client:
            url = f"{BASE}/{path.lstrip('/')}"
            r = await direct_client.get(url, params=params)
            if r.status_code == 200 and "404" not in str(r.url):
                html = r.text
                cache.set(cache_key, html, ttl)
                logger.info("merolagani %s: direct fallback succeeded (%d bytes)", path, len(html))
                return html
            logger.debug("merolagani %s: direct fallback HTTP %s", path, r.status_code)
    except Exception as direct_exc:
        logger.debug("merolagani %s: direct fallback failed: %s", path, direct_exc)

    logger.warning("merolagani %s: all %d retries failed (%s)", path, _MAX_RETRIES, last_exc)
    return None


def _parse_latest_market_table(html: str) -> List[Dict[str, Any]]:
    """
    Parse the HTML table from /LatestMarket.aspx.

    Confirmed column order (verified 2026-05-27):
      0: Symbol
      1: LTP
      2: % Change
      3: High
      4: Low
      5: Previous Close (or open — value varies)
      6: Volume (Qty.)
    """
    try:
        import re as _re

        rows = _re.findall(r"<tr[^>]*>(.*?)</tr>", html, _re.DOTALL)
        result: List[Dict[str, Any]] = []

        for row in rows:
            cells = _re.findall(r"<td[^>]*>(.*?)</td>", row, _re.DOTALL)
            clean = [_re.sub(r"<[^>]+>", "", c).strip().replace(",", "") for c in cells]

            # Need at least 7 cells; col0 must be a valid symbol (2-12 uppercase chars)
            if len(clean) < 7:
                continue
            sym = clean[0].upper()
            if not sym or not _re.match(r"^[A-Z0-9]{2,12}$", sym):
                continue

            ltp = _sf(clean[1])
            if not ltp:
                continue

            result.append({
                "symbol":         sym,
                "name":           sym,
                "ltp":            ltp,
                "percent_change": _sf(clean[2]),
                "high":           _sf(clean[3]),
                "low":            _sf(clean[4]),
                "previous_close": _sf(clean[5]) or None,
                "volume":         _si(clean[6]),
                "source":         "merolagani",
            })

        return result

    except Exception as exc:  # noqa: BLE001
        logger.debug("merolagani table parse error: %s", exc)
        return []


# ─── safe helpers ────────────────────────────────────────────────────────────

def _sf(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _si(v: Any, default: int = 0) -> int:
    try:
        return int(float(v)) if v is not None else default
    except (TypeError, ValueError):
        return default


# ─── public API ──────────────────────────────────────────────────────────────

async def get_live_market() -> List[Dict[str, Any]]:
    """
    Fetch today's live prices for all listed securities from merolagani.com.

    Uses /LatestMarket.aspx (HTML table) — the old TechnicalHandler.ashx
    JSON API was removed by merolagani (now returns 302 → 404).

    Returns:
        list of normalized stock dicts with keys:
        symbol, name, ltp, previous_close, percent_change,
        high, low, volume, source
    """
    html = await _get_html("LatestMarket.aspx", ttl=_TTL_LIVE)
    if not html:
        logger.warning("merolagani: LatestMarket.aspx unavailable")
        return []

    result = _parse_latest_market_table(html)
    if not result:
        logger.warning("merolagani: could not parse LatestMarket.aspx table")
        return []

    logger.info("merolagani: fetched %d live symbols", len(result))
    return result


async def get_live_quote(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Return a single-symbol live quote by scanning the full live list.
    O(N) but the result is cached so the list is fetched only once per TTL.
    """
    sym_u = (symbol or "").strip().upper()
    for row in await get_live_market():
        if row.get("symbol", "").upper() == sym_u:
            return row
    return None


async def get_company_quote(symbol: str) -> Dict[str, Any]:
    """
    Scrape the per-symbol StockQuote page on merolagani.com for company-level
    fundamental data (EPS, PE, book value, 52-week range, market cap).

    This is a heavier HTML scrape and uses a longer TTL (5 min).
    Returns {} on failure.
    """
    try:
        from bs4 import BeautifulSoup  # optional dependency
    except ImportError:
        logger.debug("merolagani company_quote: BeautifulSoup not installed")
        return {}

    html = await _get_html(
        "StockQuote.aspx",
        params={"symbol": symbol.upper()},
        ttl=300.0,
    )
    if not html:
        return {}

    try:
        soup = BeautifulSoup(html, "html.parser")
        result: Dict[str, Any] = {"symbol": symbol.upper(), "source": "merolagani_html"}

        # Look for data table rows (common pattern on merolagani quote pages)
        for row in soup.select("table tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) >= 2:
                key = cells[0].get_text(strip=True).lower().replace(" ", "_")
                val = cells[1].get_text(strip=True).replace(",", "")
                if key and val:
                    result[key] = val

        # Try to extract structured data from known CSS selectors
        ltp_el = soup.select_one(".ltp, .last-price, [data-ltp]")
        if ltp_el:
            result["ltp"] = _sf(ltp_el.get_text(strip=True).replace(",", ""))

        return result

    except Exception as exc:  # noqa: BLE001
        logger.debug("merolagani HTML parse error for %s: %s", symbol, exc)
        return {}


async def get_market_summary() -> Dict[str, Any]:
    """
    Market summary is no longer available via merolagani (old JSON API removed).
    Returns {} — callers should use another source for index/summary data.
    """
    return {}


async def get_top_stocks() -> Dict[str, List[Dict[str, Any]]]:
    """
    Fetch top gainers / losers / turnover from merolagani.
    Returns dict with keys: top_gainer, top_loser, top_turnover.
    Each value is a list of stock dicts.
    """
    live = await get_live_market()
    if not live:
        return {"top_gainer": [], "top_loser": [], "top_turnover": []}

    by_pct = sorted(live, key=lambda r: r.get("percent_change", 0.0), reverse=True)
    by_turnover = sorted(live, key=lambda r: r.get("turnover", 0.0), reverse=True)

    return {
        "top_gainer":  by_pct[:10],
        "top_loser":   by_pct[-10:][::-1],
        "top_turnover": by_turnover[:10],
        "source": "merolagani",
    }
