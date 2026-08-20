"""
Raw market-data payload cache (SQLite) with TTL support.

Every outbound scrape writes a copy of the raw JSON payload here, keyed by
(dataset, source, symbol, business_date). Readers can pull the last cached
payload that is still inside its TTL window, avoiding a redundant remote
fetch.

Storage format is deliberately simple — a single SQLite file next to the
PostgreSQL app DB so the cache survives restarts but never contaminates
the analytical tables.

Design goals
------------
*   Fast, process-shared cache.
*   Zero ORM dependencies — uses stdlib ``sqlite3``.
*   Callers never ``raise``; fall back to the real scraper on any cache
    miss or cache error.
*   Thread-safe (file-level lock via SQLite's built-in locking).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_CACHE_PATH = Path(
    os.getenv(
        "RAW_CACHE_PATH",
        str(Path(__file__).resolve().parents[3] / "data" / "raw_cache.sqlite"),
    )
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_data_raw (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset        TEXT    NOT NULL,
    source         TEXT    NOT NULL,
    symbol         TEXT,
    business_date  TEXT,
    fetched_at_utc TEXT    NOT NULL,
    payload_json   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_mdr_lookup
    ON market_data_raw (dataset, source, symbol, business_date, fetched_at_utc DESC);
CREATE INDEX IF NOT EXISTS ix_mdr_symbol_dataset
    ON market_data_raw (symbol, dataset, fetched_at_utc DESC);
"""

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    DEFAULT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DEFAULT_CACHE_PATH), timeout=10.0)
    conn.executescript(_SCHEMA)
    return conn


def save(
    *,
    dataset: str,
    source: str,
    payload: Any,
    symbol: Optional[str] = None,
    business_date: Optional[str] = None,
) -> None:
    """Persist a raw payload. Swallows any error."""
    try:
        blob = json.dumps(payload, default=str)
        now = datetime.now(timezone.utc).isoformat()
        with _lock, _connect() as conn:
            conn.execute(
                "INSERT INTO market_data_raw "
                "(dataset, source, symbol, business_date, fetched_at_utc, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (dataset, source, symbol, business_date, now, blob),
            )
    except Exception as exc:  # pragma: no cover — cache is best-effort
        logger.debug("raw_cache.save skipped: %s", exc)


def load_latest(
    *,
    dataset: str,
    source: Optional[str] = None,
    symbol: Optional[str] = None,
    business_date: Optional[str] = None,
    max_age_seconds: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """
    Return the freshest cached payload that matches the filters and is
    inside ``max_age_seconds``. Returns None on miss or expiry.

    Payload envelope:
        {
          "payload": <original JSON>,
          "fetched_at_utc": "2026-04-24T09:00:00+00:00",
          "age_seconds": 42.3,
        }
    """
    try:
        where = ["dataset = ?"]
        params: list[Any] = [dataset]
        if source is not None:
            where.append("source = ?")
            params.append(source)
        if symbol is not None:
            where.append("symbol = ?")
            params.append(symbol)
        if business_date is not None:
            where.append("business_date = ?")
            params.append(business_date)

        sql = (
            "SELECT payload_json, fetched_at_utc FROM market_data_raw "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY fetched_at_utc DESC LIMIT 1"
        )
        with _lock, _connect() as conn:
            row = conn.execute(sql, params).fetchone()
        if not row:
            return None
        payload_json, fetched_at_utc = row
        fetched_dt = datetime.fromisoformat(fetched_at_utc)
        age = (datetime.now(timezone.utc) - fetched_dt).total_seconds()
        if max_age_seconds is not None and age > max_age_seconds:
            return None
        return {
            "payload": json.loads(payload_json),
            "fetched_at_utc": fetched_at_utc,
            "age_seconds": age,
        }
    except Exception as exc:  # pragma: no cover
        logger.debug("raw_cache.load_latest miss (%s)", exc)
        return None


def prune(max_age_seconds: float = 7 * 24 * 3600) -> int:
    """Delete rows older than ``max_age_seconds``. Returns deleted count."""
    try:
        cutoff_epoch = time.time() - max_age_seconds
        cutoff_iso = datetime.fromtimestamp(
            cutoff_epoch, tz=timezone.utc
        ).isoformat()
        with _lock, _connect() as conn:
            cursor = conn.execute(
                "DELETE FROM market_data_raw WHERE fetched_at_utc < ?",
                (cutoff_iso,),
            )
            return cursor.rowcount or 0
    except Exception as exc:  # pragma: no cover
        logger.debug("raw_cache.prune failed: %s", exc)
        return 0
