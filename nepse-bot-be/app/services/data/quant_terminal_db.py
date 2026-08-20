"""
Quant-Terminal SQLite Adapter (read-only)

Provides a thin read-only wrapper around the SQLite database produced by
the sibling `nepse-quant-terminal` project (see its `setup_data.py`).

This is an *opt-in, offline* fallback used by the nepse-bot backend when
the live NEPSE scraper returns no data (for example when running from
outside Nepal where the official API is geo-restricted).

- Enabled when `settings.quant_terminal_db_path` is set AND the file exists.
- Opens SQLite in read-only URI mode (`mode=ro`).
- Schema assumed: `stock_prices(symbol, date, open, high, low, close, volume)`.

No writes, no network, no scraping — this module only reads a local file.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings

logger = logging.getLogger(__name__)


def get_db_path() -> Optional[Path]:
    """Return the configured quant-terminal DB path if it exists, else None."""
    raw = settings.quant_terminal_db_path
    if not raw:
        return None
    p = Path(raw).expanduser()
    if p.exists():
        return p
    # If a relative path was given, resolve it against the backend project root
    # (parent of `app/`). This keeps local dev and Vercel deploys consistent.
    if not p.is_absolute():
        project_root = Path(__file__).resolve().parents[3]
        candidate = (project_root / p).resolve()
        if candidate.exists():
            return candidate
    logger.debug(f"Quant terminal DB not found at {p}")
    return None


def is_available() -> bool:
    """True when the quant-terminal SQLite DB is reachable."""
    return get_db_path() is not None


def _connect_ro(path: Path) -> sqlite3.Connection:
    """Open a read-only SQLite connection via URI mode."""
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_ohlcv(
    symbol: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """
    Fetch historical OHLCV rows for `symbol` from the quant-terminal DB.

    Returns a list of dicts in the same shape as NepalStockScraper.fetch_stock_ohlcv:
        {"date": "YYYY-MM-DD", "open": float, "high": float, "low": float,
         "close": float, "volume": int, "raw_data": <row dict>}

    Empty list if the DB is not configured, symbol not found, or no rows.
    """
    path = get_db_path()
    if path is None:
        return []

    try:
        conn = _connect_ro(path)
    except sqlite3.Error as exc:
        logger.warning(f"Quant terminal DB open failed: {exc}")
        return []

    try:
        query = (
            "SELECT symbol, date, open, high, low, close, volume "
            "FROM stock_prices WHERE symbol = ?"
        )
        params: List[Any] = [symbol.upper()]

        if start_date is not None:
            query += " AND date >= ?"
            params.append(_as_date_str(start_date))
        if end_date is not None:
            query += " AND date <= ?"
            params.append(_as_date_str(end_date))

        query += " ORDER BY date ASC"

        cur = conn.execute(query, params)
        rows = cur.fetchall()
    except sqlite3.Error as exc:
        logger.warning(f"Quant terminal DB query failed for {symbol}: {exc}")
        return []
    finally:
        conn.close()

    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        out.append({
            "date": _as_date_str(d.get("date")),
            "open": _safe_float(d.get("open")),
            "high": _safe_float(d.get("high")),
            "low": _safe_float(d.get("low")),
            "close": _safe_float(d.get("close")),
            "volume": _safe_int(d.get("volume")),
            "raw_data": d,
        })
    logger.info(f"Quant terminal DB: returned {len(out)} OHLCV rows for {symbol}")
    return out


def list_symbols() -> List[str]:
    """Return the distinct symbols in the quant-terminal DB."""
    path = get_db_path()
    if path is None:
        return []
    try:
        conn = _connect_ro(path)
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM stock_prices ORDER BY symbol"
        ).fetchall()
        conn.close()
        return [r["symbol"] for r in rows if r["symbol"]]
    except sqlite3.Error as exc:
        logger.warning(f"list_symbols failed: {exc}")
        return []


def _as_date_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:10]
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default
