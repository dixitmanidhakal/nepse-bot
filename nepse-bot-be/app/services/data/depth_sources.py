"""
Market-depth data sources with automatic fallback chain.

The public nepalstock.com REST scraper does *not* expose the order book,
so we use higher-fidelity (but geo-restricted) sources in order of
preference:

    1. NepseUnofficialApi — official public endpoint, returns full depth.
    2. MeroLagani HTML widget — best-effort scrape, top 5 rows.
    3. SharesanSar HTML widget — best-effort scrape, top 5 rows.

Every successful fetch is mirrored to ``raw_cache`` so subsequent requests
inside the TTL window short-circuit. When **all** sources fail the caller
receives ``UpstreamUnavailable`` so the HTTP layer can return 503.

The module purposely never returns mock/fake data — the user explicitly
asked for a hard refusal instead of synthesized numbers.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

from app.services.data import raw_cache
from app.services.http import (
    CircuitBreakerOpen,
    RateLimitExceeded,
    UpstreamUnavailable,
    get_resilient_client,
)

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# Cache TTL for successful depth fetches
# Depth changes second-by-second during market hours, so 30s is aggressive
# enough to stay fresh while preventing the dashboard's polling from
# hammering upstream.
# ────────────────────────────────────────────────────────────────────────────
DEPTH_CACHE_TTL_SECONDS = 30.0


# ════════════════════════════════════════════════════════════════════════════
# Source 1 — NepseUnofficialApi (official endpoint, Nepal geo-fence)
# ════════════════════════════════════════════════════════════════════════════
_nepse_client = None


def _get_nepse_client():
    global _nepse_client
    if _nepse_client is None:
        from nepse import Nepse  # type: ignore
        _nepse_client = Nepse()
        _nepse_client.setTLSVerification(False)
    return _nepse_client


def _fetch_from_nepse_lib(symbol: str) -> Optional[Dict[str, Any]]:
    try:
        client = _get_nepse_client()
        payload = client.getSymbolMarketDepth(symbol)
    except Exception as exc:
        logger.info("nepse-lib depth fetch failed for %s: %s", symbol, exc)
        return None

    if not isinstance(payload, dict):
        return None

    buy_rows: List[Dict[str, Any]] = []
    sell_rows: List[Dict[str, Any]] = []

    # Payload shape (from NepseUnofficialApi 0.6.x):
    #   { "marketDepth": {
    #         "buyMarketDepthList":  [{orderBookOrderPrice, quantity, orderCount}],
    #         "sellMarketDepthList": [...],
    #     }, ...}
    depth_block = payload.get("marketDepth") or payload
    buy_list = depth_block.get("buyMarketDepthList") or []
    sell_list = depth_block.get("sellMarketDepthList") or []

    for row in buy_list[:5]:
        buy_rows.append({
            "price":    float(row.get("orderBookOrderPrice") or 0),
            "quantity": int(row.get("quantity") or 0),
            "orders":   int(row.get("orderCount") or 0),
        })
    for row in sell_list[:5]:
        sell_rows.append({
            "price":    float(row.get("orderBookOrderPrice") or 0),
            "quantity": int(row.get("quantity") or 0),
            "orders":   int(row.get("orderCount") or 0),
        })

    if not buy_rows and not sell_rows:
        return None

    return {
        "symbol":      symbol,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "buy_orders":  buy_rows,
        "sell_orders": sell_rows,
        "source":      "nepalstock",
    }


# ════════════════════════════════════════════════════════════════════════════
# Source 2 — MeroLagani HTML widget
# ════════════════════════════════════════════════════════════════════════════
def _fetch_from_merolagani(symbol: str) -> Optional[Dict[str, Any]]:
    url = f"https://merolagani.com/CompanyDetail.aspx?symbol={symbol}"
    try:
        resp = get_resilient_client().get(url)
    except (CircuitBreakerOpen, RateLimitExceeded, UpstreamUnavailable) as exc:
        logger.info("merolagani depth fetch failed for %s: %s", symbol, exc)
        return None
    except Exception as exc:
        logger.info("merolagani depth unexpected error for %s: %s", symbol, exc)
        return None

    if resp.status_code != 200:
        return None

    rows = _parse_merolagani_depth_html(resp.text)
    if rows is None:
        return None
    buy_rows, sell_rows = rows
    if not buy_rows and not sell_rows:
        return None

    return {
        "symbol":      symbol,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "buy_orders":  buy_rows,
        "sell_orders": sell_rows,
        "source":      "merolagani",
    }


def _parse_merolagani_depth_html(html: str):
    """
    Parse MeroLagani's market-depth table. Schema as of 2026:
        <table id="ctl00_ContentPlaceHolder1_CompanyDetail1_divMarketDepth" ...>
            <tbody>
                <tr><td>buy_orders</td><td>buy_qty</td><td>buy_price</td>
                    <td>sell_price</td><td>sell_qty</td><td>sell_orders</td></tr>
                ... (5 rows) ...
    If the table isn't present (non-trading hours or site redesign), return
    None so the caller can move on to the next source.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
        candidates = soup.select("table.table-market-depth, table#depth, "
                                 "table[id*='MarketDepth']")
        buy_rows, sell_rows = [], []
        for table in candidates:
            rows = [tr.find_all("td") for tr in table.find_all("tr") if tr.find_all("td")]
            # must have at least 5 rows × 6 columns to be the depth widget
            if len(rows) < 3 or any(len(r) < 6 for r in rows[:3]):
                continue
            for cells in rows[:5]:
                try:
                    buy_orders_n = int(cells[0].get_text(strip=True).replace(",", "") or 0)
                    buy_qty      = int(cells[1].get_text(strip=True).replace(",", "") or 0)
                    buy_price    = float(cells[2].get_text(strip=True).replace(",", "") or 0)
                    sell_price   = float(cells[3].get_text(strip=True).replace(",", "") or 0)
                    sell_qty     = int(cells[4].get_text(strip=True).replace(",", "") or 0)
                    sell_orders_n = int(cells[5].get_text(strip=True).replace(",", "") or 0)
                except (ValueError, IndexError):
                    continue
                if buy_price > 0:
                    buy_rows.append({"price": buy_price, "quantity": buy_qty,
                                     "orders": buy_orders_n})
                if sell_price > 0:
                    sell_rows.append({"price": sell_price, "quantity": sell_qty,
                                      "orders": sell_orders_n})
            if buy_rows or sell_rows:
                return buy_rows, sell_rows
        return None
    except Exception as exc:  # pragma: no cover — site-shape changes
        logger.debug("merolagani parse failed: %s", exc)
        return None


# ════════════════════════════════════════════════════════════════════════════
# Orchestrator
# ════════════════════════════════════════════════════════════════════════════
# Source names are looked up via ``globals()`` at call time so that tests
# patching ``_fetch_from_nepse_lib`` / ``_fetch_from_merolagani`` take
# effect without needing to also patch ``_SOURCES``.
_SOURCE_NAMES = (
    ("nepalstock", "_fetch_from_nepse_lib"),
    ("merolagani", "_fetch_from_merolagani"),
)


def fetch_market_depth(
    symbol: str,
    *,
    allow_cache: bool = True,
    cache_ttl: float = DEPTH_CACHE_TTL_SECONDS,
) -> Dict[str, Any]:
    """
    Fetch market depth using the full fallback chain.

    Parameters
    ----------
    symbol      : stock symbol (will be uppercased).
    allow_cache : if True, return a recent cached payload (within ``cache_ttl``
                  seconds) instead of hitting upstream.
    cache_ttl   : maximum age of an acceptable cached payload, in seconds.

    Raises
    ------
    UpstreamUnavailable : every source in the chain failed. The caller
                          should translate this to HTTP 503.
    """
    symbol = symbol.strip().upper()
    if not symbol:
        raise ValueError("symbol is required")

    # 1 — cache lookup
    if allow_cache:
        hit = raw_cache.load_latest(
            dataset="market_depth",
            symbol=symbol,
            max_age_seconds=cache_ttl,
        )
        if hit and isinstance(hit.get("payload"), dict):
            payload = dict(hit["payload"])
            payload["from_cache"] = True
            payload["cache_age_seconds"] = round(hit["age_seconds"], 1)
            return payload

    # 2 — upstream sources in order
    errors: List[str] = []
    for src_name, fetcher_name in _SOURCE_NAMES:
        fetcher = globals().get(fetcher_name)
        if fetcher is None:  # pragma: no cover — defensive
            errors.append(f"{src_name}: missing fetcher {fetcher_name}")
            continue
        try:
            payload = fetcher(symbol)
        except Exception as exc:
            errors.append(f"{src_name}: {exc}")
            continue

        if payload is None:
            errors.append(f"{src_name}: empty or unavailable")
            continue

        # 3 — persist to cache for next caller
        raw_cache.save(
            dataset="market_depth",
            source=payload.get("source", src_name),
            symbol=symbol,
            payload=payload,
        )
        return payload

    raise UpstreamUnavailable(
        f"All market-depth sources failed for {symbol}: " + "; ".join(errors)
    )
