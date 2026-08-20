"""
Dynamic NEPSE Universe
======================
Single shared module used by ALL bots — no hardcoded symbol lists.

Universe source (priority order):
  1. SQLite historical DB (provider.list_symbols()) — every symbol that has
     historical OHLCV data, typically 200-400 NEPSE scripts.
  2. Live market API (aggregator.live_market()) — all currently trading
     symbols (~372). Used when the SQLite DB is unavailable.

Sector mapping source:
  - Live sector indices API (aggregator.sector_indices() + sector_stocks())
  - Builds a complete {symbol: sector_name} map from the NEPSE sector API.
  - Falls back to "Other" for any unmapped symbol.

Both universe list and sector map are cached in-process (5-minute TTL) to
avoid redundant DB or network calls on every 15-minute bot cycle.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import re
import threading
import time
from typing import Any, Coroutine, Dict, List, Optional, TypeVar

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# ── Safe async runner ──────────────────────────────────────────────────────────
# anyio 4.x attaches event loop context to FastAPI thread-pool threads, making
# bare asyncio.run() raise "This event loop is already running" silently inside
# the broad exception handlers.  Running in a *fresh* daemon thread (which has
# no event loop) avoids the conflict entirely.
_ASYNC_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="bot_async"
)


def run_async(coro: Coroutine[Any, Any, _T]) -> _T:
    """
    Run an async coroutine from sync code safely regardless of whether the
    caller is in an anyio/asyncio thread pool thread or a plain thread.

    Uses a dedicated ThreadPoolExecutor so the coroutine always runs in a
    thread that has no pre-existing event loop.
    """
    future = _ASYNC_EXECUTOR.submit(asyncio.run, coro)
    return future.result(timeout=60)

# ── Equity filter ──────────────────────────────────────────────────────────────
# Exclude non-equity NEPSE instruments: debentures, partial calls, mutual fund
# class-specific units, and any symbol with non-alphabetic characters.
#
# Patterns excluded:
#   *D[0-9]{2,}  → debentures (ADBLD83, MFLD85, NIMBD90, NABILPD84 …)
#   *P[0-9]+     → promoter shares / partial calls (NABILP2, PRBUPO …)
#   *F[12]       → some fund class units
#   Contains "::", space, or non-alphanumeric characters → system/aggregate rows
_DEBENTURE_RE = re.compile(r'^[A-Z]+D\d{2,}$')
_PROMO_SHARE_RE = re.compile(r'^[A-Z]+P\d+$')
_INVALID_CHARS_RE = re.compile(r'[^A-Z0-9]')


def _is_equity(symbol: str) -> bool:
    """Return True only if the symbol looks like a plain NEPSE equity."""
    if not symbol or len(symbol) < 2 or len(symbol) > 12:
        return False
    if _INVALID_CHARS_RE.search(symbol):      # contains spaces, "::", etc.
        return False
    if _DEBENTURE_RE.match(symbol):           # debenture bonds
        return False
    if _PROMO_SHARE_RE.match(symbol):         # promoter partial calls
        return False
    return True

_CACHE_TTL = 300.0  # 5 minutes

_lock = threading.Lock()

# ── Universe cache ─────────────────────────────────────────────────────────────
_universe_cache: Optional[List[str]] = None
_universe_ts: float = 0.0

# ── Sector cache ───────────────────────────────────────────────────────────────
_sector_cache: Optional[Dict[str, str]] = None
_sector_ts: float = 0.0


# ── Public API ─────────────────────────────────────────────────────────────────

def get_nepse_universe(provider=None) -> List[str]:
    """
    Return ALL NEPSE symbols that have historical OHLCV data.

    Primary:  SQLite historical DB via provider.list_symbols()
    Fallback: live market API (aggregator.live_market())

    Results are cached for 5 minutes.
    """
    global _universe_cache, _universe_ts

    now = time.monotonic()
    with _lock:
        if _universe_cache is not None and (now - _universe_ts) < _CACHE_TTL:
            return list(_universe_cache)

    symbols = _from_db(provider) or _from_live()

    with _lock:
        _universe_cache = symbols
        _universe_ts = time.monotonic()

    logger.info("NEPSE dynamic universe: %d symbols loaded", len(symbols))
    return list(symbols)


def get_sector_map(refresh: bool = False) -> Dict[str, str]:
    """
    Return a {symbol: sector_name} mapping for all known NEPSE symbols.

    Built by calling the live sector indices API then fetching the stocks
    in each sector.  Cached for 5 minutes.

    IMPORTANT: empty maps are never cached — a failed build will retry on
    the very next call, preventing cache-poisoning that silences bots.
    """
    global _sector_cache, _sector_ts

    now = time.monotonic()
    with _lock:
        if not refresh and _sector_cache is not None and (now - _sector_ts) < _CACHE_TTL:
            return dict(_sector_cache)

    smap = _build_sector_map()

    if smap:
        # Only cache a successful (non-empty) result
        with _lock:
            _sector_cache = smap
            _sector_ts = time.monotonic()
        logger.info("Sector map built and cached: %d symbols tagged", len(smap))
    else:
        logger.warning(
            "Sector map build returned empty — NOT caching; next call will retry"
        )

    return dict(smap)


def get_sector(symbol: str, sector_map: Optional[Dict[str, str]] = None) -> str:
    """
    Look up the canonical sector name for a symbol.

    Returns the canonical sector string (e.g. "Hydro Power", "Commercial Banks")
    as defined by _canon_sector(), or "Other" if the symbol is unmapped.

    Applying _canon_sector() here ensures that whatever string the sector API
    returns ("Hydropower", "Hydro Power", "hydro power", etc.) is collapsed
    to one consistent form before it reaches the RL engine's accuracy tracker.
    """
    if sector_map is None:
        sector_map = get_sector_map()
    raw = sector_map.get(symbol.upper().strip())
    if raw is None:
        return "Other"
    # Normalise via RL engine canonical alias table
    try:
        from app.components.rl_engine import _canon_sector
        return _canon_sector(raw)
    except ImportError:
        return raw


# ── Internal helpers ───────────────────────────────────────────────────────────

def _from_db(provider=None) -> Optional[List[str]]:
    """Load equity symbol list from SQLite historical DB."""
    try:
        if provider is None:
            from app.services.data.historical_provider import get_historical_provider
            provider = get_historical_provider()
        if not provider.is_available():
            return None
        syms = [s for s in provider.list_symbols() if _is_equity(s)]
        return syms if syms else None
    except Exception as exc:
        logger.warning("Universe from SQLite DB failed: %s", exc)
        return None


def _from_live() -> List[str]:
    """Fallback: get equity symbols from the live market API."""
    try:
        from app.services.data.free_sources import aggregator
        rows = run_async(aggregator.live_market())
        syms: List[str] = []
        for row in rows:
            sym = str(row.get("symbol") or row.get("Symbol") or "").strip().upper()
            if _is_equity(sym):
                syms.append(sym)
        return syms
    except Exception as exc:
        logger.warning("Universe from live market failed: %s", exc)
        return []


def _build_sector_map() -> Dict[str, str]:
    """
    Build symbol→sector by calling:
      1. aggregator.sector_indices()  → list of sector index metadata
      2. aggregator.sector_stocks(sector_name) for each sector → stocks

    Falls back to live-market-derived sector grouping if the sector API
    returns nothing useful.
    """
    smap: Dict[str, str] = {}
    try:
        from app.services.data.free_sources import aggregator

        logger.debug("_build_sector_map: calling sector_indices()...")
        sector_list = run_async(aggregator.sector_indices())
        logger.debug(
            "_build_sector_map: sector_indices() returned %d entries",
            len(sector_list) if sector_list else 0,
        )
        if not sector_list:
            logger.warning(
                "_build_sector_map: sector_indices() returned empty — "
                "falling back to live-market sector derivation"
            )
            return _build_sector_map_from_live()

        ok_sectors = 0
        fail_sectors = 0
        for idx in sector_list:
            # Resolve a human-readable sector name
            master = idx.get("sectorMaster") or {}
            if isinstance(master, dict):
                sector_name = master.get("sectorDescription") or master.get("sector") or ""
            else:
                sector_name = str(master)
            if not sector_name:
                sector_name = (
                    idx.get("indexName") or idx.get("description") or "Other"
                )

            # Key for the sector_stocks endpoint — try sectorMaster description,
            # then indexCode, then indexName
            if isinstance(idx.get("sectorMaster"), dict):
                sector_key = idx["sectorMaster"].get("sectorDescription") or sector_name
            else:
                sector_key = idx.get("indexCode") or sector_name

            try:
                stocks = run_async(aggregator.sector_stocks(sector_key))
                count = 0
                for stock in stocks if isinstance(stocks, list) else []:
                    sym = str(
                        stock.get("symbol") or stock.get("Symbol") or ""
                    ).strip().upper()
                    if sym and sym not in smap:
                        # Canonicalise sector name at storage time so all
                        # consumers ("Hydro Power" vs "Hydropower") see the same key
                        try:
                            from app.components.rl_engine import _canon_sector as _cs
                            smap[sym] = _cs(str(sector_name))
                        except ImportError:
                            smap[sym] = str(sector_name)
                        count += 1
                ok_sectors += 1
                logger.debug(
                    "_build_sector_map: sector '%s' → %d stocks", sector_name, count
                )
            except Exception as exc:
                fail_sectors += 1
                logger.warning(
                    "_build_sector_map: sector_stocks('%s') failed: %s",
                    sector_key, exc,
                )

        logger.info(
            "_build_sector_map: %d sectors OK, %d failed → %d symbols mapped",
            ok_sectors, fail_sectors, len(smap),
        )

        if not smap and ok_sectors == 0:
            logger.warning(
                "_build_sector_map: all sector_stocks calls failed — "
                "falling back to live-market sector derivation"
            )
            return _build_sector_map_from_live()

    except Exception as exc:
        logger.warning(
            "Sector map build failed: %s — trying live-market fallback", exc
        )
        return _build_sector_map_from_live()
    return smap


def _build_sector_map_from_live() -> Dict[str, str]:
    """
    Fallback sector map: derive symbol→sector from the live market snapshot.

    The live market endpoint returns rows with `symbol` and (on supported
    sources) a `sectorName` / `sector` field.  If that field is absent we
    group everything under the source-reported index, which at least keeps
    the sector_rotation_bot functional even if groupings are coarse.
    """
    smap: Dict[str, str] = {}
    try:
        from app.services.data.free_sources import aggregator
        rows = run_async(aggregator.live_market())
        for row in rows:
            sym = str(row.get("symbol") or row.get("Symbol") or "").strip().upper()
            if not sym:
                continue
            # Try various field names that different sources use
            sector = (
                row.get("sectorName")
                or row.get("sector")
                or row.get("indexName")
                or row.get("category")
                or ""
            )
            if sector and sym not in smap:
                try:
                    from app.components.rl_engine import _canon_sector as _cs
                    smap[sym] = _cs(str(sector))
                except ImportError:
                    smap[sym] = str(sector)
        logger.info(
            "_build_sector_map_from_live: mapped %d symbols from live market",
            len(smap),
        )
    except Exception as exc:
        logger.warning("_build_sector_map_from_live failed: %s", exc)
    return smap
