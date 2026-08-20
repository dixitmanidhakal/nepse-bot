#!/usr/bin/env python3
"""
Backfill SQLite stock_prices from SamirWagle GitHub CSVs.

Fetches all missing bars after the DB cutoff date (April 15, 2026)
for every symbol in the DB, writing directly into nepse_data_public.db.

Usage:
    python scripts/backfill_sqlite_from_samirwagle.py
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import sqlite3
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("backfill")

DB_PATH = Path(
    "/Users/dixitmanidhakal/Documents/nepse-bot-root"
    "/nepse-quant-terminal/data/nepse_data_public.db"
)
SAMIRWAGLE_BASE = (
    "https://raw.githubusercontent.com/SamirWagle/Nepse-All-Scraper/main/data"
)
CUTOFF_DATE = date(2026, 4, 15)          # DB last update
MAX_CONCURRENT = 8                        # parallel fetches
TIMEOUT = 25.0
HEADERS = {
    "User-Agent": "nepse-bot/1.0 (+github.com/dixitmanidhakal)",
    "Accept": "text/csv,*/*",
}


# ──────────────────────────────────────────────────────────────────────────────
# Fetch helpers
# ──────────────────────────────────────────────────────────────────────────────

async def fetch_symbol_csv(client: httpx.AsyncClient, symbol: str) -> Optional[str]:
    url = f"{SAMIRWAGLE_BASE}/company-wise/{symbol.upper()}/prices.csv"
    try:
        r = await client.get(url, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.text
        logger.debug("%s: HTTP %d", symbol, r.status_code)
        return None
    except Exception as e:
        logger.debug("%s: fetch error %s", symbol, e)
        return None


def parse_csv_rows(text: str, cutoff: date) -> List[Tuple]:
    """
    Return (date, open, high, low, close, volume) tuples for rows after cutoff.
    SamirWagle field names: date, open, high, low, ltp, percent_change, qty, turnover
    """
    rows = []
    try:
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            raw_date = (row.get("date") or "")[:10]
            if not raw_date:
                continue
            try:
                d = date.fromisoformat(raw_date)
            except ValueError:
                continue
            if d <= cutoff:
                continue

            def f(k: str) -> Optional[float]:
                v = row.get(k)
                if v is None or v.strip() == "":
                    return None
                try:
                    return float(v)
                except ValueError:
                    return None

            close = f("ltp") or f("close")
            if close is None:
                continue

            rows.append((
                raw_date,
                f("open"),
                f("high"),
                f("low"),
                close,
                f("qty") or f("volume"),
            ))
    except Exception as e:
        logger.debug("CSV parse error: %s", e)
    return rows


# ──────────────────────────────────────────────────────────────────────────────
# DB helpers
# ──────────────────────────────────────────────────────────────────────────────

def get_all_symbols(db_path: Path) -> List[str]:
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "SELECT DISTINCT symbol FROM stock_prices "
        "WHERE symbol IS NOT NULL AND symbol != '' ORDER BY symbol"
    )
    symbols = [r[0] for r in cur.fetchall()]
    conn.close()
    return symbols


def get_symbol_cutoff(conn: sqlite3.Connection, symbol: str) -> date:
    """Return the actual last date for this symbol (may be earlier than global cutoff)."""
    row = conn.execute(
        "SELECT MAX(date) FROM stock_prices WHERE symbol = ?", (symbol,)
    ).fetchone()
    if row and row[0]:
        try:
            return date.fromisoformat(str(row[0])[:10])
        except ValueError:
            pass
    return CUTOFF_DATE


def upsert_rows(conn: sqlite3.Connection, symbol: str, rows: List[Tuple]) -> int:
    if not rows:
        return 0
    sql = (
        "INSERT OR REPLACE INTO stock_prices "
        "(symbol, date, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    data = [(symbol, d, o, h, l, c, v) for d, o, h, l, c, v in rows]
    conn.executemany(sql, data)
    conn.commit()
    return len(data)


# ──────────────────────────────────────────────────────────────────────────────
# Worker
# ──────────────────────────────────────────────────────────────────────────────

async def process_symbol(
    client: httpx.AsyncClient,
    db_path: Path,
    symbol: str,
    sem: asyncio.Semaphore,
) -> Tuple[str, int, str]:
    """Returns (symbol, rows_added, status)."""
    async with sem:
        text = await fetch_symbol_csv(client, symbol)
        if text is None:
            return (symbol, 0, "no_csv")

        # Get per-symbol actual cutoff (some symbols may be more stale)
        conn = sqlite3.connect(db_path)
        cutoff = get_symbol_cutoff(conn, symbol)

        rows = parse_csv_rows(text, cutoff)
        if not rows:
            conn.close()
            return (symbol, 0, "up_to_date")

        n = upsert_rows(conn, symbol, rows)
        conn.close()
        return (symbol, n, "ok")


async def run_backfill(symbols: List[str], db_path: Path):
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    total_inserted = 0
    ok = no_csv = up_to_date = errors = 0
    start = time.time()

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        tasks = [
            process_symbol(client, db_path, sym, sem)
            for sym in symbols
        ]
        done = 0
        for coro in asyncio.as_completed(tasks):
            sym, n, status = await coro
            done += 1
            total_inserted += n
            if status == "ok":
                ok += 1
                logger.info("[%d/%d] %-12s +%d rows", done, len(symbols), sym, n)
            elif status == "no_csv":
                no_csv += 1
                logger.debug("[%d/%d] %-12s no CSV", done, len(symbols), sym)
            elif status == "up_to_date":
                up_to_date += 1
            else:
                errors += 1
                logger.warning("[%d/%d] %-12s error: %s", done, len(symbols), sym, status)

            if done % 50 == 0:
                elapsed = time.time() - start
                logger.info(
                    "── Progress: %d/%d (%.0fs) | +%d rows so far ──",
                    done, len(symbols), elapsed, total_inserted,
                )

    elapsed = time.time() - start
    logger.info("=" * 60)
    logger.info("BACKFILL COMPLETE in %.1f s", elapsed)
    logger.info("  Symbols processed : %d", len(symbols))
    logger.info("  Rows inserted      : %d", total_inserted)
    logger.info("  With new data      : %d", ok)
    logger.info("  Already up-to-date : %d", up_to_date)
    logger.info("  No CSV found       : %d", no_csv)
    logger.info("  Errors             : %d", errors)


def main():
    if not DB_PATH.exists():
        logger.error("DB not found: %s", DB_PATH)
        sys.exit(1)

    symbols = get_all_symbols(DB_PATH)
    logger.info("DB: %s", DB_PATH)
    logger.info("Found %d symbols to backfill (cutoff: %s)", len(symbols), CUTOFF_DATE)

    # Quick sanity: show current max date
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT MAX(date) FROM stock_prices").fetchone()
    conn.close()
    logger.info("Current DB max date: %s", row[0] if row else "?")

    asyncio.run(run_backfill(symbols, DB_PATH))

    # Verify result
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT MAX(date), COUNT(*) FROM stock_prices").fetchone()
    conn.close()
    logger.info("After backfill — max date: %s  total rows: %s", row[0], f"{row[1]:,}")


if __name__ == "__main__":
    main()
