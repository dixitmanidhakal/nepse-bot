"""
SamirWagle Nepse-All-Scraper Client
===================================

Secondary free data source. Daily GitHub-Actions scraper, committed once
per trading day around 21:07 UTC (~03:00 Kathmandu).

Covers:
    - Full daily floorsheet per trading day (CSV, ~80 k rows, ~4 MB gzipped).
    - Per-symbol historical prices (OHLCV).
    - Per-symbol cash / bonus dividends.
    - Per-symbol right-share history.

Schemas (verified 2026-04-28):

    floorsheet_YYYY-MM-DD.csv
        columns: date, sn, contract_no, stock_symbol, buyer, seller,
                 quantity, rate, amount

    company-wise/{SYMBOL}/prices.csv
        columns: date, open, high, low, ltp, percent_change, qty, turnover
        (most recent rows first)

    company-wise/{SYMBOL}/dividend.csv
        columns: fiscal_year, bonus_share, cash_dividend, total_dividend,
                 book_closure_date

    company-wise/{SYMBOL}/right-share.csv
        columns: fiscal_year, right_ratio, book_closure_date
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

from .cache import get_cache

logger = logging.getLogger(__name__)

BASE = "https://raw.githubusercontent.com/SamirWagle/Nepse-All-Scraper/main/data"
GITHUB_API = "https://api.github.com/repos/SamirWagle/Nepse-All-Scraper"

FLOORSHEET_TTL = 3600.0           # full floorsheet changes at most once/day
SYMBOL_SERIES_TTL = 21600.0       # per-symbol prices updated daily
COMPANY_LIST_TTL = 86400.0        # ~1 day

_HEADERS = {
    "User-Agent": "nepse-bot/1.0 (+https://github.com/dixitmanidhakal)",
    "Accept": "text/csv, application/json, */*",
}


# -------------------------- core fetch --------------------------

async def _fetch_text(path: str, ttl: float, timeout: float = 20.0) -> Optional[str]:
    """
    Fetch `{BASE}/{path}` as plain text (CSV). Caches on success.
    Returns None on non-200 or network errors.
    """
    cache = get_cache()
    key = f"samirwagle::text::{path}"

    async def loader() -> Optional[str]:
        url = f"{BASE}/{path}"
        try:
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=True, headers=_HEADERS
            ) as client:
                r = await client.get(url)
                if r.status_code != 200:
                    logger.info(
                        "samirwagle %s: HTTP %s", path, r.status_code
                    )
                    return None
                return r.text
        except Exception as exc:  # noqa: BLE001
            logger.warning("samirwagle %s: %s", path, exc)
            return None

    return await cache.aget_or_set(key, loader, ttl)


async def _fetch_json(path: str, ttl: float) -> Optional[Any]:
    cache = get_cache()
    key = f"samirwagle::json::{path}"

    async def loader() -> Optional[Any]:
        url = f"{BASE}/{path}"
        try:
            async with httpx.AsyncClient(
                timeout=15.0, follow_redirects=True, headers=_HEADERS
            ) as client:
                r = await client.get(url)
                if r.status_code != 200:
                    return None
                return r.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("samirwagle %s: %s", path, exc)
            return None

    return await cache.aget_or_set(key, loader, ttl)


def _parse_csv(text: str) -> List[Dict[str, str]]:
    if not text:
        return []
    reader = csv.DictReader(io.StringIO(text))
    return [dict(row) for row in reader]


def _to_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(float(str(v).replace(",", "")))
    except (TypeError, ValueError):
        return None


# -------------------------- company list --------------------------

async def get_company_list() -> List[Dict[str, Any]]:
    data = await _fetch_json("company_list.json", ttl=COMPANY_LIST_TTL)
    if isinstance(data, list):
        return data
    return []


# -------------------------- prices --------------------------

async def get_symbol_prices(symbol: str) -> List[Dict[str, Any]]:
    """
    Return full historical OHLCV series for one symbol, most-recent first.
    Each row:
        {date: "YYYY-MM-DD", open, high, low, ltp, percent_change, qty, turnover}
    """
    if not symbol:
        return []
    text = await _fetch_text(
        f"company-wise/{symbol.upper()}/prices.csv",
        ttl=SYMBOL_SERIES_TTL,
    )
    if not text:
        return []
    out: List[Dict[str, Any]] = []
    for row in _parse_csv(text):
        out.append(
            {
                "date": (row.get("date") or "")[:10],
                "open": _to_float(row.get("open")),
                "high": _to_float(row.get("high")),
                "low": _to_float(row.get("low")),
                "ltp": _to_float(row.get("ltp")),
                "percent_change": _to_float(row.get("percent_change")),
                "qty": _to_int(row.get("qty")),
                "turnover": _to_float(row.get("turnover")),
            }
        )
    # SamirWagle's CSV can have two blocks (historical newest-first + a smaller
    # appended block of more recent data added at the bottom). Sort descending
    # by date so that rows[0] is always the most-recent bar and the API
    # `limit` slice never silently drops the newer appended data.
    out.sort(key=lambda r: r["date"] or "", reverse=True)
    return out


async def get_symbol_dividends(symbol: str) -> List[Dict[str, Any]]:
    if not symbol:
        return []
    text = await _fetch_text(
        f"company-wise/{symbol.upper()}/dividend.csv",
        ttl=COMPANY_LIST_TTL,
    )
    if not text:
        return []
    out = []
    for row in _parse_csv(text):
        out.append(
            {
                "fiscal_year": row.get("fiscal_year"),
                "bonus_share": _to_float(row.get("bonus_share")),
                "cash_dividend": _to_float(row.get("cash_dividend")),
                "total_dividend": _to_float(row.get("total_dividend")),
                "book_closure_date": row.get("book_closure_date"),
            }
        )
    return out


async def get_symbol_rights(symbol: str) -> List[Dict[str, Any]]:
    if not symbol:
        return []
    text = await _fetch_text(
        f"company-wise/{symbol.upper()}/right-share.csv",
        ttl=COMPANY_LIST_TTL,
    )
    if not text:
        return []
    return _parse_csv(text)


# -------------------------- floorsheet --------------------------

async def get_floorsheet_for_date(target: str | date) -> List[Dict[str, Any]]:
    """
    Return full floorsheet (all trades) for a given date.
    `target` can be a datetime.date or a 'YYYY-MM-DD' string.
    """
    if isinstance(target, date):
        date_str = target.isoformat()
    else:
        date_str = str(target)[:10]

    text = await _fetch_text(
        f"floorsheet/floorsheet_{date_str}.csv",
        ttl=FLOORSHEET_TTL,
        timeout=30.0,
    )
    if not text:
        return []
    out: List[Dict[str, Any]] = []
    for row in _parse_csv(text):
        out.append(
            {
                "date": row.get("date"),
                "sn": _to_int(row.get("sn")),
                "contract_no": row.get("contract_no"),
                "symbol": row.get("stock_symbol"),
                "buyer": _to_int(row.get("buyer")),
                "seller": _to_int(row.get("seller")),
                "quantity": _to_int(row.get("quantity")),
                "rate": _to_float(row.get("rate")),
                "amount": _to_float(row.get("amount")),
            }
        )
    return out


async def get_floorsheet_for_symbol(
    symbol: str,
    target: str | date,
) -> List[Dict[str, Any]]:
    """Filter one day's floorsheet to a single symbol."""
    sym_u = (symbol or "").upper()
    rows = await get_floorsheet_for_date(target)
    return [r for r in rows if str(r.get("symbol", "")).upper() == sym_u]


async def get_latest_floorsheet_date() -> Optional[str]:
    """
    Return the most recent trading-day CSV filename (YYYY-MM-DD).
    Walks the GitHub Contents API (cached long) and picks max.
    """
    cache = get_cache()
    key = "samirwagle::latest_floorsheet_date"

    async def loader() -> Optional[str]:
        try:
            async with httpx.AsyncClient(
                timeout=15.0, follow_redirects=True, headers=_HEADERS
            ) as client:
                r = await client.get(
                    f"{GITHUB_API}/contents/data/floorsheet?ref=main"
                )
                if r.status_code != 200:
                    return None
                names = [
                    x.get("name", "")
                    for x in r.json()
                    if isinstance(x, dict)
                ]
                dates = sorted(
                    [
                        n.replace("floorsheet_", "").replace(".csv", "")
                        for n in names
                        if n.startswith("floorsheet_") and n.endswith(".csv")
                    ]
                )
                return dates[-1] if dates else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("latest floorsheet lookup failed: %s", exc)
            return None

    return await cache.aget_or_set(key, loader, ttl=1800.0)


async def get_latest_floorsheet() -> List[Dict[str, Any]]:
    latest = await get_latest_floorsheet_date()
    if not latest:
        return []
    return await get_floorsheet_for_date(latest)


# -------------------------- freshness --------------------------

async def get_freshness() -> Dict[str, Any]:
    cache = get_cache()
    key = "samirwagle::freshness"

    async def loader() -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "last_commit_utc": None,
            "commit_age_minutes": None,
            "latest_floorsheet_date": None,
            "latest_floorsheet_age_hours": None,
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
                logger.debug("samirwagle freshness commits: %s", exc)
        latest = await get_latest_floorsheet_date()
        if latest:
            out["latest_floorsheet_date"] = latest
            try:
                dt = datetime.fromisoformat(latest).replace(tzinfo=timezone.utc)
                out["latest_floorsheet_age_hours"] = round(
                    (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0, 1
                )
            except Exception:  # noqa: BLE001
                pass
        return out

    return await cache.aget_or_set(key, loader, ttl=300.0)


def _minutes_since(iso_str: str) -> Optional[float]:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return round((datetime.now(timezone.utc) - dt).total_seconds() / 60.0, 1)
    except Exception:  # noqa: BLE001
        return None
