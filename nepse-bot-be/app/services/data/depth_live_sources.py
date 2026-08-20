"""
Low-latency market-depth source adapters.

These adapters are used by the high-frequency depth poller during market
hours (11:00–15:00 NPT). They are intentionally kept **separate** from the
classic ``depth_sources.py`` chain so the battle-tested caching path
(used by REST callers) stays untouched.

Primary chain for the live poller (in priority order):
  1. Chukul        — https://chukul.com       (configurable base URL)
  2. NepseAlpha    — https://nepsealpha.com    (configurable base URL)
  3. NepseUnofficial lib (already used by depth_sources.py)
  4. Merolagani HTML widget (already used by depth_sources.py)

Endpoints for Chukul / NepseAlpha are not officially documented. We expose
them as env-configurable templates so you can point the scraper at the
exact URL their site uses without redeploying:

    CHUKUL_DEPTH_URL_TEMPLATE    (e.g. "https://chukul.com/api/depth/{symbol}")
    NEPSEALPHA_DEPTH_URL_TEMPLATE (e.g. "https://nepsealpha.com/api/depth?symbol={symbol}")

If the template is empty the source is skipped.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Shared session-less httpx calls with short timeouts                         #
# --------------------------------------------------------------------------- #

_DEFAULT_TIMEOUT = httpx.Timeout(connect=2.0, read=2.5, write=2.0, pool=2.0)
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (nepse-bot live depth poller; contact=admin@localhost)"
    ),
    "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _get(url: str, headers: Optional[Dict[str, str]] = None) -> Optional[httpx.Response]:
    try:
        hdrs = dict(_DEFAULT_HEADERS)
        if headers:
            hdrs.update(headers)
        with httpx.Client(timeout=_DEFAULT_TIMEOUT, follow_redirects=True, verify=False) as c:
            r = c.get(url, headers=hdrs)
            return r
    except Exception as exc:
        logger.debug("live depth GET failed url=%s err=%s", url, exc)
        return None


# --------------------------------------------------------------------------- #
# Row normalisation                                                           #
# --------------------------------------------------------------------------- #

def _row(price: float, quantity: float, orders: float = 0) -> Dict[str, Any]:
    return {
        "price": float(price or 0.0),
        "quantity": int(quantity or 0),
        "orders": int(orders or 0),
    }


def _snapshot(
    symbol: str,
    buy: List[Dict[str, Any]],
    sell: List[Dict[str, Any]],
    source: str,
) -> Optional[Dict[str, Any]]:
    if not buy and not sell:
        return None
    return {
        "symbol": symbol.upper(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "buy_orders": buy[:5],
        "sell_orders": sell[:5],
        "source": source,
    }


def _depth_metrics(snap: Dict[str, Any]) -> Dict[str, Any]:
    """Derive the depth features the engine reads (walls, imbalance, spread)."""
    buys = snap.get("buy_orders") or []
    sells = snap.get("sell_orders") or []
    total_buy = sum(int(r.get("quantity") or 0) for r in buys)
    total_sell = sum(int(r.get("quantity") or 0) for r in sells)

    denom = total_buy + total_sell
    imbalance = (total_buy - total_sell) / denom if denom else 0.0

    # Wall = any row with quantity >= 3× average of the side (>=5 rows)
    def _has_wall(rows: List[Dict[str, Any]]) -> bool:
        qtys = [int(r.get("quantity") or 0) for r in rows if int(r.get("quantity") or 0) > 0]
        if len(qtys) < 3:
            return False
        avg = sum(qtys) / len(qtys)
        return any(q >= 3.0 * avg for q in qtys)

    has_bid_wall = _has_wall(buys)
    has_ask_wall = _has_wall(sells)

    best_bid = max((float(r.get("price") or 0.0) for r in buys), default=0.0)
    best_ask = min(
        (float(r.get("price") or 0.0) for r in sells if float(r.get("price") or 0.0) > 0),
        default=0.0,
    )
    if best_bid > 0 and best_ask > 0 and best_ask >= best_bid:
        spread = best_ask - best_bid
        mid = (best_ask + best_bid) / 2.0
        spread_pct = (spread / mid) * 100.0 if mid > 0 else 0.0
    else:
        spread = 0.0
        spread_pct = 0.0

    return {
        "has_bid_wall": has_bid_wall,
        "has_ask_wall": has_ask_wall,
        "order_imbalance": round(float(imbalance), 6),
        "bid_ask_spread": round(float(spread), 4),
        "bid_ask_spread_percent": round(float(spread_pct), 4),
        "total_buy_quantity": total_buy,
        "total_sell_quantity": total_sell,
        "best_bid": best_bid,
        "best_ask": best_ask,
    }


# --------------------------------------------------------------------------- #
# Source 1 — Chukul                                                           #
# --------------------------------------------------------------------------- #

def fetch_chukul(symbol: str) -> Optional[Dict[str, Any]]:
    template = _env(
        "CHUKUL_DEPTH_URL_TEMPLATE",
        "https://chukul.com/api/data/market_depth/{symbol}",
    )
    if not template:
        return None
    url = template.format(symbol=symbol.upper())
    headers = {"Referer": "https://chukul.com/"}
    resp = _get(url, headers=headers)
    if resp is None or resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    # Chukul payloads commonly wrap rows under "buy"/"sell" or "buyOrders"/"sellOrders"
    buy_list = (
        data.get("buy_orders")
        or data.get("buyOrders")
        or data.get("buy")
        or (data.get("data") or {}).get("buy_orders")
        or []
    )
    sell_list = (
        data.get("sell_orders")
        or data.get("sellOrders")
        or data.get("sell")
        or (data.get("data") or {}).get("sell_orders")
        or []
    )

    buy = [
        _row(
            r.get("price") or r.get("orderBookOrderPrice"),
            r.get("quantity") or r.get("qty"),
            r.get("orders") or r.get("orderCount") or 0,
        )
        for r in buy_list
    ]
    sell = [
        _row(
            r.get("price") or r.get("orderBookOrderPrice"),
            r.get("quantity") or r.get("qty"),
            r.get("orders") or r.get("orderCount") or 0,
        )
        for r in sell_list
    ]
    return _snapshot(symbol, buy, sell, "chukul")


# --------------------------------------------------------------------------- #
# Source 2 — NepseAlpha                                                       #
# --------------------------------------------------------------------------- #

def fetch_nepsealpha(symbol: str) -> Optional[Dict[str, Any]]:
    template = _env(
        "NEPSEALPHA_DEPTH_URL_TEMPLATE",
        "https://www.nepsealpha.com/trading/1/market-depth?symbol={symbol}",
    )
    if not template:
        return None
    url = template.format(symbol=symbol.upper())
    headers = {"Referer": "https://nepsealpha.com/"}
    resp = _get(url, headers=headers)
    if resp is None or resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    buy_list = (
        data.get("buyOrders")
        or data.get("buy_orders")
        or data.get("bids")
        or (data.get("data") or {}).get("bids")
        or []
    )
    sell_list = (
        data.get("sellOrders")
        or data.get("sell_orders")
        or data.get("asks")
        or (data.get("data") or {}).get("asks")
        or []
    )

    def _pick(r: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(r, list) and len(r) >= 2:
            # [price, qty, orders]
            return _row(r[0], r[1], r[2] if len(r) > 2 else 0)
        return _row(
            r.get("price") or r.get("p"),
            r.get("quantity") or r.get("q") or r.get("size"),
            r.get("orders") or r.get("n") or 0,
        )

    buy = [_pick(r) for r in buy_list if r]
    sell = [_pick(r) for r in sell_list if r]
    return _snapshot(symbol, buy, sell, "nepsealpha")


# --------------------------------------------------------------------------- #
# Source 3 — NepseUnofficial (lazy import; reuses existing lib path)          #
# --------------------------------------------------------------------------- #

def fetch_nepse_unofficial(symbol: str) -> Optional[Dict[str, Any]]:
    try:
        from app.services.data import depth_sources as _legacy
        return _legacy._fetch_from_nepse_lib(symbol.upper())
    except Exception as exc:
        logger.debug("nepse-unofficial fetch failed for %s: %s", symbol, exc)
        return None


# --------------------------------------------------------------------------- #
# Source 4 — Merolagani (HTML, only as last resort; slow)                    #
# --------------------------------------------------------------------------- #

def fetch_merolagani(symbol: str) -> Optional[Dict[str, Any]]:
    try:
        from app.services.data import depth_sources as _legacy
        return _legacy._fetch_from_merolagani(symbol.upper())
    except Exception as exc:
        logger.debug("merolagani fetch failed for %s: %s", symbol, exc)
        return None


# --------------------------------------------------------------------------- #
# Orchestrator                                                                #
# --------------------------------------------------------------------------- #

SourceFn = Callable[[str], Optional[Dict[str, Any]]]


def _sources_in_priority() -> List[Tuple[str, SourceFn]]:
    return [
        ("chukul", fetch_chukul),
        ("nepsealpha", fetch_nepsealpha),
        ("nepse-unofficial", fetch_nepse_unofficial),
        ("merolagani", fetch_merolagani),
    ]


def fetch_live_depth(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Try every source in priority order. Returns the first non-empty
    snapshot with computed metrics attached, or ``None`` when every
    source is unavailable. Never raises.
    """
    start = time.monotonic()
    tried: List[str] = []
    for name, fn in _sources_in_priority():
        tried.append(name)
        try:
            snap = fn(symbol)
        except Exception as exc:
            logger.debug("live depth %s raised for %s: %s", name, symbol, exc)
            continue
        if not snap:
            continue
        metrics = _depth_metrics(snap)
        snap.update(metrics)
        snap["elapsed_ms"] = round((time.monotonic() - start) * 1000.0, 1)
        snap["sources_tried"] = tried
        return snap

    logger.debug("all live depth sources failed for %s (tried=%s)", symbol, tried)
    return None


__all__ = [
    "fetch_live_depth",
    "fetch_chukul",
    "fetch_nepsealpha",
    "fetch_nepse_unofficial",
    "fetch_merolagani",
]
