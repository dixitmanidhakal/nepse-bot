"""
ShareSansar Scraper
===================

Scrapes live NEPSE market data from sharesansar.com — a comprehensive,
publicly accessible NEPSE data site reachable from outside Nepal.

Endpoints used:
  GET  /today-share-price              → HTML table: today's share prices
  GET  /live-trading                   → HTML table: live trading (intraday)
  GET  /company/{symbol}              → HTML page: company profile + fundamentals
  GET  /floorsheet                    → HTML table: today's floorsheet

AJAX / JSON endpoints (used when available):
  POST /company/index                 → JSON list (pagination ajax)
  GET  /api/...                       → If any undocumented API is discovered

Features:
  - Proxy rotation via ProxyRotator (configure PROXY_LIST env var).
  - Random jitter (50–500 ms) between requests.
  - Retry with up to 3 attempts per call.
  - Per-response TTL cache via the shared TTLCache.
  - BeautifulSoup HTML parsing with graceful degradation.
  - Returns [] / {} on any failure — never raises to callers.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from .cache import get_cache
from .proxy_rotator import get_rotator

logger = logging.getLogger(__name__)

BASE = "https://www.sharesansar.com"
_TIMEOUT = 20.0
_MAX_RETRIES = 3
_TTL_LIVE = 45.0
_TTL_STATIC = 300.0

_EXTRA_HEADERS = {
    "Referer": "https://www.sharesansar.com/",
    "Origin":  "https://www.sharesansar.com",
    "sec-fetch-site": "same-origin",
    "sec-fetch-mode": "navigate",
    "sec-fetch-dest": "document",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
}

# Cookie jar shared across requests within the same scraper session.
# Persisting cookies from the initial page load prevents some anti-bot
# challenges that require a valid session cookie to be set first.
_cookie_jar: httpx.Cookies = httpx.Cookies()


# ─── low-level helpers ────────────────────────────────────────────────────────

async def _get_html(
    path: str,
    params: Optional[Dict[str, Any]] = None,
    ttl: float = _TTL_LIVE,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """
    GET {BASE}/{path} as HTML with proxy rotation, exponential backoff,
    cookie persistence, 429 rate-limit handling, and TTL cache.
    """
    cache = get_cache()
    cache_key = f"sharesansar::html::{path}::{sorted((params or {}).items())}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    rotator = get_rotator()
    last_exc: Optional[Exception] = None

    for attempt in range(_MAX_RETRIES):
        headers, proxy_url = rotator.next_async()
        headers["Accept"] = "text/html,application/xhtml+xml,*/*;q=0.8"
        headers.update(_EXTRA_HEADERS)
        if extra_headers:
            headers.update(extra_headers)
        proxies = rotator.httpx_proxies(proxy_url)

        # HTML scraping needs more human-like delays
        await rotator.exponential_jitter(attempt, base_ms=600.0, cap_ms=10_000.0)

        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT,
                follow_redirects=True,
                verify=False,
                proxies=proxies,
                headers=headers,
                cookies=_cookie_jar,  # persist session cookies
            ) as client:
                url = f"{BASE}/{path.lstrip('/')}"
                r = await client.get(url, params=params)

                # Merge any new cookies from the response into our jar
                _cookie_jar.update(r.cookies)

                if r.status_code == 200:
                    rotator.report_success(proxy_url)
                    html = r.text
                    cache.set(cache_key, html, ttl)
                    return html
                elif r.status_code == 429:
                    retry_after = float(r.headers.get("Retry-After", "120"))
                    rotator.report_rate_limited(proxy_url, retry_after)
                    logger.warning(
                        "sharesansar %s attempt %d: 429 rate-limited, cooling %.0fs",
                        path, attempt + 1, retry_after,
                    )
                    await asyncio.sleep(min(retry_after, 15.0))
                elif r.status_code in (403, 503):
                    rotator.report_failure(proxy_url)
                    logger.warning(
                        "sharesansar %s attempt %d: HTTP %s (anti-bot block)",
                        path, attempt + 1, r.status_code,
                    )
                else:
                    rotator.report_failure(proxy_url)
                    logger.warning(
                        "sharesansar %s attempt %d: HTTP %s", path, attempt + 1, r.status_code
                    )

        except Exception as exc:  # noqa: BLE001
            rotator.report_failure(proxy_url)
            last_exc = exc
            logger.debug("sharesansar %s attempt %d: %s", path, attempt + 1, exc)

    logger.warning("sharesansar %s: all retries failed (%s)", path, last_exc)
    return None


def _sf(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        cleaned = str(v).replace(",", "").strip()
        return float(cleaned) if cleaned else default
    except (TypeError, ValueError):
        return default


def _si(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        cleaned = str(v).replace(",", "").strip()
        return int(float(cleaned)) if cleaned else default
    except (TypeError, ValueError):
        return default


def _clean_text(t: Any) -> str:
    return re.sub(r"\s+", " ", str(t or "").strip())


# ─── table parsers ────────────────────────────────────────────────────────────

def _parse_table(html: str, table_id: Optional[str] = None) -> List[Dict[str, str]]:
    """
    Parse an HTML table into a list of row dicts keyed by column headers.
    If table_id is given, find that specific <table id="...">.
    Otherwise parse the first <table> in the page.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.warning("sharesansar: BeautifulSoup not installed; cannot parse HTML")
        return []

    try:
        soup = BeautifulSoup(html, "html.parser")
        if table_id:
            table = soup.find("table", {"id": table_id})
        else:
            table = soup.find("table")

        if not table:
            return []

        # Extract headers
        header_row = table.find("tr")
        if not header_row:
            return []
        headers = [
            _clean_text(th.get_text()).lower().replace(" ", "_").replace("/", "_")
            for th in header_row.find_all(["th", "td"])
        ]
        if not headers:
            return []

        rows: List[Dict[str, str]] = []
        for tr in table.find_all("tr")[1:]:
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue
            row = {}
            for i, cell in enumerate(cells):
                key = headers[i] if i < len(headers) else f"col{i}"
                row[key] = _clean_text(cell.get_text())
            rows.append(row)

        return rows

    except Exception as exc:  # noqa: BLE001
        logger.debug("sharesansar table parse error: %s", exc)
        return []


# ─── live market ──────────────────────────────────────────────────────────────

async def get_live_market() -> List[Dict[str, Any]]:
    """
    Scrape today's share price table from sharesansar.com/today-share-price.

    Returns:
        list of normalized stock dicts with keys:
        symbol, name, ltp, previous_close, change, percent_change,
        open, high, low, volume, turnover, source
    """
    html = await _get_html("today-share-price", ttl=_TTL_LIVE)
    if not html:
        # Try live-trading as fallback
        html = await _get_html("live-trading", ttl=_TTL_LIVE)
    if not html:
        return []

    # sharesansar uses id="headFixed" or class-based tables
    rows = _parse_table(html, table_id="headFixed")
    if not rows:
        rows = _parse_table(html)
    if not rows:
        logger.warning("sharesansar: could not parse today-share-price table")
        return []

    result: List[Dict[str, Any]] = []
    for row in rows:
        # Common column names on sharesansar today-share-price:
        # symbol, ltp, point_change, % change, open, high, low, volume, prev_closing
        symbol = _clean_text(
            row.get("symbol") or row.get("ticker") or row.get("s.n.")
        ).upper()
        if not symbol or len(symbol) > 15:
            continue

        ltp = _sf(row.get("ltp") or row.get("last_traded_price") or row.get("close"))
        prev = _sf(
            row.get("prev._closing")
            or row.get("previous_close")
            or row.get("prev_closing")
            or row.get("closing_price")
            or ltp
        )
        point_chg = _sf(row.get("point_change") or row.get("change") or (ltp - prev))
        pct_chg = _sf(
            row.get("%_change")
            or row.get("percent_change")
            or row.get("percentage_change")
            or ((point_chg / prev * 100.0) if prev else 0.0)
        )

        result.append({
            "symbol": symbol,
            "name": str(row.get("company_name") or row.get("name") or symbol).strip(),
            "ltp": ltp,
            "previous_close": prev,
            "change": round(point_chg, 2),
            "percent_change": round(pct_chg, 2),
            "open": _sf(row.get("open") or row.get("open_price")),
            "high": _sf(row.get("high") or row.get("high_price")),
            "low":  _sf(row.get("low")  or row.get("low_price")),
            "volume": _si(
                row.get("volume") or row.get("total_traded_quantity") or row.get("qty.")
            ),
            "turnover": _sf(
                row.get("turnover") or row.get("total_traded_value")
            ),
            "source": "sharesansar",
        })

    logger.info("sharesansar: fetched %d live symbols", len(result))
    return result


async def get_live_quote(symbol: str) -> Optional[Dict[str, Any]]:
    """Single-symbol quote by scanning the full live list."""
    sym_u = (symbol or "").strip().upper()
    for row in await get_live_market():
        if row.get("symbol", "").upper() == sym_u:
            return row
    return None


# ─── floorsheet ───────────────────────────────────────────────────────────────

async def get_floorsheet(max_pages: int = 3) -> List[Dict[str, Any]]:
    """
    Scrape today's floorsheet from sharesansar.com/floorsheet.

    Args:
        max_pages: Maximum number of paginated pages to fetch (each ~25 rows).

    Returns:
        list of trade dicts: symbol, buyer_broker, seller_broker,
                              quantity, rate, amount, source
    """
    all_trades: List[Dict[str, Any]] = []

    for page in range(1, max_pages + 1):
        params = {"page": page} if page > 1 else None
        html = await _get_html(
            "floorsheet",
            params=params,
            ttl=120.0,
        )
        if not html:
            break

        rows = _parse_table(html)
        if not rows:
            break

        for row in rows:
            symbol = _clean_text(
                row.get("symbol") or row.get("stock_symbol") or ""
            ).upper()
            if not symbol:
                continue
            all_trades.append({
                "symbol": symbol,
                "buyer_broker": _si(
                    row.get("buyer_broker") or row.get("buyer") or 0
                ),
                "seller_broker": _si(
                    row.get("seller_broker") or row.get("seller") or 0
                ),
                "quantity": _si(
                    row.get("quantity") or row.get("contract_quantity") or 0
                ),
                "rate": _sf(
                    row.get("rate") or row.get("contract_rate") or 0
                ),
                "amount": _sf(
                    row.get("amount") or row.get("contract_amount") or 0
                ),
                "source": "sharesansar",
            })

        # Stop if this page returned fewer rows (last page)
        if len(rows) < 20:
            break

    logger.info("sharesansar: fetched %d floorsheet trades", len(all_trades))
    return all_trades


# ─── company / fundamentals ───────────────────────────────────────────────────

async def get_company_info(symbol: str) -> Dict[str, Any]:
    """
    Scrape company profile and fundamentals from sharesansar.com/company/{symbol}.

    Returns:
        dict with keys: symbol, sector, eps, pe_ratio, book_value,
                        market_cap, week52_high, week52_low, source
        Returns {} on failure.
    """
    html = await _get_html(
        f"company/{symbol.upper()}",
        ttl=_TTL_STATIC,
    )
    if not html:
        return {}

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return {}

    try:
        soup = BeautifulSoup(html, "html.parser")
        info: Dict[str, Any] = {
            "symbol": symbol.upper(),
            "source": "sharesansar_html",
        }

        # Common pattern: key-value pairs in <dt>/<dd> or table rows
        for dl in soup.select("dl"):
            dts = dl.find_all("dt")
            dds = dl.find_all("dd")
            for dt, dd in zip(dts, dds):
                key = _clean_text(dt.get_text()).lower().replace(" ", "_")
                val = _clean_text(dd.get_text()).replace(",", "")
                if key and val:
                    info[key] = val

        # Table rows fallback
        for tr in soup.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if len(cells) == 2:
                key = _clean_text(cells[0].get_text()).lower().replace(" ", "_")
                val = _clean_text(cells[1].get_text()).replace(",", "")
                if key and val:
                    info.setdefault(key, val)

        return info

    except Exception as exc:  # noqa: BLE001
        logger.debug("sharesansar company parse error for %s: %s", symbol, exc)
        return {}


# ─── indices ──────────────────────────────────────────────────────────────────

async def get_indices() -> List[Dict[str, Any]]:
    """
    Attempt to scrape the NEPSE index from the sharesansar.com home/dashboard.
    Returns list with index data dicts. Returns [] on failure.
    """
    html = await _get_html("", ttl=60.0)  # home page
    if not html:
        return []

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        indices: List[Dict[str, Any]] = []

        # Find index display elements (site-specific; adapt as needed)
        for el in soup.select(".index-value, .nepse-index, [data-index]"):
            name = _clean_text(el.get("data-index") or el.get("title") or el.get_text())
            val_el = el.find(class_=re.compile(r"value|number|current"))
            val = _sf(val_el.get_text().replace(",", "") if val_el else "0")
            if name and val:
                indices.append({
                    "index": name,
                    "currentValue": val,
                    "source": "sharesansar",
                })
        return indices

    except Exception:  # noqa: BLE001
        return []
