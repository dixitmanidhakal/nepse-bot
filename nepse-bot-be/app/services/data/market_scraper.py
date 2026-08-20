"""
Market Data Scraper
===================
Fetches live NEPSE market data from multiple sources every 5 minutes
during trading hours and persists results to the live_market_cache table.

Source rotation:
  Each scrape cycle uses a DIFFERENT "primary" source so no single website
  gets hit on every cycle. Order rotates: merolagani → nepsealpha →
  sharesansar → yonepse → merolagani → …

  If the primary source fails or returns empty, the scraper falls through
  to the next source (same cascade logic as the aggregator) and writes
  whichever source succeeds.

IP rotation:
  All direct-scrape sources (merolagani, nepsealpha, sharesansar) already
  use the ProxyRotator (configure PROXY_LIST env var for real proxies).
  Without proxies the scrapers go direct but with random User-Agent and
  request jitter to reduce fingerprinting.

NEPSE trading window (NST = UTC+5:45):
  10:30–15:15 NST  →  04:45–09:30 UTC  (Mon–Fri)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("market_scraper")

# Source names in round-robin order
_SOURCES = ["merolagani", "nepsealpha", "sharesansar", "yonepse"]
_source_index = 0  # module-level counter; advanced after each successful scrape


def _next_source_order() -> List[str]:
    """Return the source list starting from the next rotation slot."""
    global _source_index
    idx = _source_index
    _source_index = (_source_index + 1) % len(_SOURCES)
    rotated = _SOURCES[idx:] + _SOURCES[:idx]
    return rotated


# ─── async fetch ─────────────────────────────────────────────────────────────

async def _fetch_from_source(source: str) -> List[Dict[str, Any]]:
    """Call the appropriate scraper module and return normalised rows."""
    from app.services.data.free_sources import merolagani, nepsealpha, sharesansar, yonepse

    if source == "merolagani":
        return await merolagani.get_live_market()
    if source == "nepsealpha":
        return await nepsealpha.get_live_market()
    if source == "sharesansar":
        return await sharesansar.get_live_market()
    # yonepse
    return await yonepse.get_live_market()


def _parse_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        return f if f == f else None   # NaN guard
    except (TypeError, ValueError):
        return None


def _first(*keys, row: Dict[str, Any]) -> Any:
    """
    Return the first value from row that is NOT None (0 and 0.0 are valid).
    This avoids the Python `or` falsy-trap where 0 would be skipped.
    """
    for k in keys:
        v = row.get(k)
        if v is not None:
            return v
    return None


def _normalise_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Extract canonical fields from any scraper's output dict.
    All scrapers normalise to the same field names before reaching here,
    but we also handle the raw API field aliases as a safety net.

    Returns None if symbol or ltp cannot be resolved.
    """
    sym = (
        row.get("symbol") or row.get("Symbol") or row.get("ticker") or ""
    ).strip().upper()
    if not sym:
        return None

    # ltp: never 0 in practice, so `or` is safe here
    ltp = _parse_float(
        row.get("ltp") or row.get("LTP") or row.get("close")
        or row.get("lastTradedPrice") or row.get("lastTradePrice")
    )
    if ltp is None:
        return None

    # Use _first() for numeric fields that can legitimately be 0
    return {
        "symbol":         sym,
        "ltp":            ltp,
        "open_price":     _parse_float(_first("open", "openPrice", "open_price", row=row)),
        "high_price":     _parse_float(_first("high", "highPrice", "high_price", row=row)),
        "low_price":      _parse_float(_first("low", "lowPrice", "low_price", row=row)),
        "previous_close": _parse_float(_first("previous_close", "previousclose", "previousClose", "prev_close", row=row)),
        "percent_change": _parse_float(_first("percent_change", "percentchange", "percentChange", "change_percent", row=row)),
        "volume":         _parse_float(_first("volume", "totalVolume", "total_volume", "qty", "traded_quantity", row=row)),
        "turnover":       _parse_float(_first("turnover", "totalTurnover", "total_turnover", "traded_value", row=row)),
    }


# ─── database upsert ─────────────────────────────────────────────────────────

def _upsert_to_db(rows: List[Dict[str, Any]], source: str) -> int:
    """
    Upsert normalised rows into live_market_cache using a single atomic
    PostgreSQL INSERT … ON CONFLICT (symbol) DO UPDATE statement.

    This avoids the race condition where concurrent scraper calls both see
    'record is None', both issue INSERT, and the second one raises
    UniqueViolation on the ix_live_market_cache_symbol unique index.
    """
    if not rows:
        return 0

    try:
        from app.database import SessionLocal
        from app.models.live_market_cache import LiveMarketCache
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        now = datetime.now(timezone.utc)
        db = SessionLocal()
        try:
            # Build dict keyed by symbol — later entries overwrite earlier ones,
            # so if the same symbol appears twice in one batch we keep the last value.
            # This prevents `ON CONFLICT DO UPDATE command cannot affect row a second time`
            # (a PostgreSQL error raised when the same target row is updated twice in
            # one INSERT … ON CONFLICT statement).
            seen: dict = {}
            for r in rows:
                seen[r["symbol"]] = {
                    "symbol":         r["symbol"],
                    "ltp":            r["ltp"],
                    "open_price":     r.get("open_price"),
                    "high_price":     r.get("high_price"),
                    "low_price":      r.get("low_price"),
                    "previous_close": r.get("previous_close"),
                    "percent_change": r.get("percent_change"),
                    "volume":         r.get("volume"),
                    "turnover":       r.get("turnover"),
                    "source":         source,
                    "scraped_at":     now,
                }
            values = list(seen.values())

            stmt = pg_insert(LiveMarketCache).values(values)
            stmt = stmt.on_conflict_do_update(
                index_elements=["symbol"],
                set_={
                    "ltp":            stmt.excluded.ltp,
                    "open_price":     stmt.excluded.open_price,
                    "high_price":     stmt.excluded.high_price,
                    "low_price":      stmt.excluded.low_price,
                    "previous_close": stmt.excluded.previous_close,
                    "percent_change": stmt.excluded.percent_change,
                    "volume":         stmt.excluded.volume,
                    "turnover":       stmt.excluded.turnover,
                    "source":         stmt.excluded.source,
                    "scraped_at":     stmt.excluded.scraped_at,
                },
            )
            db.execute(stmt)
            db.commit()
            return len(values)
        finally:
            db.close()
    except Exception as exc:
        logger.error("live_market_cache DB upsert failed: %s", exc)
        return 0


# ─── main scrape entry-point ─────────────────────────────────────────────────

async def scrape_live_market() -> Dict[str, Any]:
    """
    One scrape cycle. Called by APScheduler every 5 minutes.

    Rotates the primary source each call. Falls through to secondary sources
    if the primary returns empty. Writes results to live_market_cache table.

    Returns a status dict (for logging).
    """
    order = _next_source_order()
    used_source: Optional[str] = None
    saved: int = 0

    for source in order:
        try:
            logger.debug("market_scraper: trying %s …", source)
            raw_rows = await _fetch_from_source(source)
            if not raw_rows:
                logger.debug("market_scraper: %s returned empty", source)
                continue

            # Normalise
            normed = [_normalise_row(r) for r in raw_rows]
            normed = [r for r in normed if r is not None]
            if not normed:
                continue

            # Persist
            saved = _upsert_to_db(normed, source)
            used_source = source
            logger.info(
                "market_scraper: saved %d symbols from %s",
                saved, source,
            )
            break

        except Exception as exc:
            logger.warning("market_scraper: %s failed — %s", source, exc)

    if used_source is None:
        logger.warning("market_scraper: all sources failed this cycle")

    return {"source": used_source, "saved": saved}


def run_scraper_sync() -> None:
    """
    Sync wrapper called by APScheduler (which runs jobs in a thread pool).
    Creates its own event loop so we never fight with FastAPI's loop.
    """
    try:
        asyncio.run(scrape_live_market())
    except Exception as exc:
        logger.error("market_scraper run_scraper_sync error: %s", exc)


# ─── read helper used by bots ─────────────────────────────────────────────────

def get_cached_prices(symbols: List[str], max_age_seconds: float = 600.0) -> Dict[str, float]:
    """
    Read the latest prices for the requested symbols from live_market_cache.

    Returns a dict {SYMBOL: ltp}. Entries older than max_age_seconds are
    excluded (default 10 minutes — safe for the 5-minute scrape interval).

    Falls back to {} if the table is empty or DB is unavailable.
    """
    if not symbols:
        return {}
    try:
        from app.database import SessionLocal
        from app.models.live_market_cache import LiveMarketCache
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
        sym_set = {s.upper() for s in symbols}

        db = SessionLocal()
        try:
            rows = (
                db.query(LiveMarketCache)
                .filter(
                    LiveMarketCache.symbol.in_(sym_set),
                    LiveMarketCache.scraped_at >= cutoff,
                    LiveMarketCache.ltp.isnot(None),
                )
                .all()
            )
            return {r.symbol: float(r.ltp) for r in rows}
        finally:
            db.close()
    except Exception as exc:
        logger.debug("get_cached_prices DB read failed: %s", exc)
        return {}
