"""
Bulk ingestion: nepse-quant-terminal SQLite -> nepse-bot PostgreSQL.

Reads the public bundled DB produced by nepse-quant-terminal's setup_data.py
and populates the bot's stocks / sectors / stock_ohlcv tables with ~6 years
of historical daily OHLCV for all 400+ NEPSE-listed securities.

Idempotent: re-running updates existing rows instead of duplicating.

Run:
    QUANT_TERMINAL_DB_PATH=/abs/path/nepse_data_public.db \\
        ./venv/bin/python scripts/ingest_from_quant_terminal.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

# Ensure we can import the app package when run from the project root
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from app.database import SessionLocal, engine
from app.models.sector import Sector
from app.models.stock import Stock
from app.models.stock_ohlcv import StockOHLCV


# --------------------------------------------------------------------------- #
# Minimal sector mapping (inlined from nepse-quant-terminal sectors.py)        #
# --------------------------------------------------------------------------- #

SECTOR_GROUPS: Dict[str, List[str]] = {
    "Commercial Banks": [
        "ADBL", "CZBIL", "EBL", "GBIME", "HBL", "KBL", "LSL", "MBL", "NABIL",
        "NBL", "NICA", "NIMB", "NMB", "PCBL", "PRVU", "RBCL", "SANIMA", "SBI",
        "SBL", "SCB",
    ],
    "Development Banks": [
        "CORBL", "EDBL", "GBBL", "GRDBL", "JBBL", "KSBBL", "LBBL", "MLBL",
        "MDB", "MNBBL", "NABBC", "SADBL", "SAPDBL", "SHINE", "SINDU",
    ],
    "Finance": [
        "BFC", "CFCL", "GFCL", "GMFIL", "GUFL", "ICFC", "JFL", "MFIL", "NFS",
        "PFL", "PROFL", "RLFL", "SFCL", "SIFC",
    ],
    "Microfinance": [
        "ACLBSL", "ALBSL", "CBBL", "CLBSL", "DDBL", "FMDBL", "FOWAD", "GBLBS",
        "GILB", "GLBSL", "ILBS", "JALPA", "JSLBB", "KMCDB", "LLBS", "MATRI",
        "MERO", "MLBSL", "MSLB", "NADEP", "NESDO", "NICLBSL", "NLBBL", "NMBMF",
        "NMFBS", "NUBL", "RSDC", "SAMAJ", "SDLBSL", "SHLB", "SKBBL", "SLBBL",
        "SLBSL", "SMATA", "SMB", "SMFBS", "SMPDA", "SWBBL", "SWMF", "ULBSL",
        "VLBS", "WNLB",
    ],
    "Life Insurance": [
        "ALICL", "CLI", "GMLI", "HLI", "ILI", "LICN", "NLIC", "PMLI", "RHPL",
        "RLI", "SJLIC", "SLICL",
    ],
    "Non-Life Insurance": [
        "HEI", "IGI", "NICL", "NIL", "NLG", "NMIC", "PRIN", "PIC", "RBCLPO",
        "SGIC", "SICL",
    ],
    "Hydropower": [
        "AHPC", "AKJCL", "AKPL", "API", "BARUN", "BNHC", "BPCL", "CHCL",
        "CHL", "DHPL", "DOLTI", "GHL", "GLH", "HDHPC", "HHL", "HPPL", "HURJA",
        "IHL", "JOSHI", "KKHC", "KPCL", "LEC", "MAKAR", "MBJC", "MEL",
        "MEN", "MHNL", "MKHC", "MKJC", "MMKJL", "MSHL", "NGPL", "NHDL",
        "NHPC", "NYADI", "PMHPL", "PPCL", "RADHI", "RHGCL", "RIDI", "RURU",
        "SAHAS", "SGHC", "SHEL", "SHPC", "SJCL", "SMH", "SMHL", "SPC",
        "SPDL", "SPHL", "SPL", "TAMOR", "TPC", "TSHL", "TVCL", "UHEWA",
        "ULHC", "UMHL", "UNHPL", "UPCL", "UPPER", "USHEC", "USHL",
    ],
    "Hotels": ["CGH", "KDL", "OHL", "SHL", "TRH"],
    "Manufacturing": ["BNL", "BNT", "HDL", "NLO", "SHIVM", "SRS", "UNL"],
    "Trading": ["BBC", "NTC", "STC"],
    "Others": ["HRL", "MKCL", "NRIC", "NRM", "NTC", "NWCL"],
}


def resolve_sector(symbol: str) -> str:
    for name, syms in SECTOR_GROUPS.items():
        if symbol in syms:
            return name
    return "Others"


# --------------------------------------------------------------------------- #
# Ingestion                                                                    #
# --------------------------------------------------------------------------- #

def ensure_sectors(db_session, symbols: Iterable[str]) -> Dict[str, int]:
    """Create Sector rows if missing; return name -> id map for touched sectors."""
    needed = {resolve_sector(s) for s in symbols}
    sector_ids: Dict[str, int] = {}
    now = datetime.utcnow()
    for name in sorted(needed):
        existing = db_session.query(Sector).filter_by(name=name).first()
        if existing:
            sector_ids[name] = existing.id
            continue
        row = Sector(
            name=name,
            code=name.upper().replace(" ", "_").replace("-", "_"),
            created_at=now,
            updated_at=now,
        )
        db_session.add(row)
        db_session.flush()
        sector_ids[name] = row.id
    db_session.commit()
    return sector_ids


def ensure_stocks(
    db_session, symbols: Iterable[str], sector_ids: Dict[str, int]
) -> Dict[str, int]:
    """Create Stock rows if missing; return symbol -> id map."""
    stock_ids: Dict[str, int] = {}
    now = datetime.utcnow()

    # Map existing symbols
    existing_rows = db_session.query(Stock.id, Stock.symbol).all()
    for sid, sym in existing_rows:
        stock_ids[sym.upper()] = sid

    new_rows = []
    for symbol in symbols:
        sym = symbol.upper()
        if sym in stock_ids:
            continue
        sector_name = resolve_sector(sym)
        new_rows.append(Stock(
            symbol=sym,
            name=sym,
            sector_id=sector_ids.get(sector_name),
            is_active=True,
            is_tradeable=True,
            created_at=now,
            updated_at=now,
        ))

    if new_rows:
        db_session.bulk_save_objects(new_rows, return_defaults=True)
        db_session.commit()
        for row in new_rows:
            stock_ids[row.symbol] = row.id

    return stock_ids


def bulk_upsert_ohlcv(conn_sqlite: sqlite3.Connection, stock_ids: Dict[str, int]) -> int:
    """Stream SQLite rows into Postgres via raw COPY-friendly INSERT ... ON CONFLICT."""
    cur = conn_sqlite.cursor()
    cur.execute(
        "SELECT symbol, date, open, high, low, close, volume "
        "FROM stock_prices ORDER BY symbol, date"
    )

    inserted = 0
    batch_size = 5000
    batch: List[Tuple] = []

    raw_conn = engine.raw_connection()
    pg_cur = raw_conn.cursor()

    # Ensure a unique index exists for UPSERT semantics
    try:
        pg_cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "ux_stock_ohlcv_stock_date ON stock_ohlcv(stock_id, date)"
        )
        raw_conn.commit()
    except Exception:
        raw_conn.rollback()

    sql = (
        "INSERT INTO stock_ohlcv "
        "(stock_id, date, open, high, low, close, volume, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW()) "
        "ON CONFLICT (stock_id, date) DO UPDATE SET "
        "  open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, "
        "  close = EXCLUDED.close, volume = EXCLUDED.volume, "
        "  updated_at = NOW()"
    )

    def flush(rows):
        nonlocal inserted
        if not rows:
            return
        pg_cur.executemany(sql, rows)
        raw_conn.commit()
        inserted += len(rows)

    started = time.time()
    for (sym, dstr, o, h, l, c, v) in cur:
        sid = stock_ids.get(str(sym).upper())
        if sid is None:
            continue
        try:
            d = datetime.strptime(dstr, "%Y-%m-%d").date() if isinstance(dstr, str) else dstr
        except Exception:
            continue
        batch.append((
            sid, d,
            float(o) if o is not None else None,
            float(h) if h is not None else None,
            float(l) if l is not None else None,
            float(c) if c is not None else None,
            float(v) if v is not None else None,
        ))
        if len(batch) >= batch_size:
            flush(batch)
            batch = []
            if inserted % 50_000 == 0:
                elapsed = time.time() - started
                print(f"  upserted {inserted:,} rows ({elapsed:.1f}s)")

    flush(batch)
    pg_cur.close()
    raw_conn.close()

    return inserted


def update_stock_snapshot_from_latest_ohlcv():
    """Refresh each stock's ltp / previous_close / last_traded_date from the most
    recent two OHLCV rows so the frontend dashboard has something to show."""
    with engine.begin() as conn:
        conn.execute(text("""
            WITH latest AS (
                SELECT DISTINCT ON (stock_id)
                    stock_id, date AS latest_date,
                    close AS latest_close, volume AS latest_volume
                FROM stock_ohlcv
                ORDER BY stock_id, date DESC
            ),
            prev AS (
                SELECT DISTINCT ON (o.stock_id)
                    o.stock_id, o.close AS prev_close
                FROM stock_ohlcv o
                JOIN latest l ON l.stock_id = o.stock_id
                WHERE o.date < l.latest_date
                ORDER BY o.stock_id, o.date DESC
            )
            UPDATE stocks s
               SET ltp = latest.latest_close,
                   previous_close = COALESCE(prev.prev_close, latest.latest_close),
                   volume = latest.latest_volume,
                   change = latest.latest_close - COALESCE(prev.prev_close, latest.latest_close),
                   change_percent = CASE
                       WHEN prev.prev_close IS NULL OR prev.prev_close = 0 THEN 0
                       ELSE ((latest.latest_close - prev.prev_close) / prev.prev_close) * 100
                   END,
                   last_traded_date = latest.latest_date,
                   updated_at = NOW()
              FROM latest
              LEFT JOIN prev ON prev.stock_id = latest.stock_id
             WHERE s.id = latest.stock_id
        """))


def main():
    src = os.environ.get("QUANT_TERMINAL_DB_PATH") or (
        "/Users/dixitmanidhakal/Documents/nepse-bot-root/nepse-quant-terminal/"
        "data/nepse_data_public.db"
    )
    if not Path(src).exists():
        print(f"ERROR: source DB not found at {src}")
        sys.exit(2)

    print(f"Source SQLite : {src}")
    print(f"Target Postgres: {engine.url}")

    conn = sqlite3.connect(src)
    symbols = [r[0] for r in conn.execute(
        "SELECT DISTINCT symbol FROM stock_prices WHERE symbol IS NOT NULL"
    ).fetchall()]
    # Skip aggregate rows and symbols that won't fit the stocks.symbol VARCHAR(20) column.
    symbols = [
        s for s in symbols
        if s
        and s.upper() != "NEPSE"
        and not s.upper().startswith("SECTOR::")
        and len(s) <= 20
    ]
    print(f"Found {len(symbols)} unique symbols in source DB")

    db = SessionLocal()
    try:
        print("Ensuring sectors...")
        sector_ids = ensure_sectors(db, symbols)
        print(f"  sectors: {len(sector_ids)} rows")

        print("Ensuring stocks...")
        stock_ids = ensure_stocks(db, symbols, sector_ids)
        print(f"  stocks: {len(stock_ids)} rows")
    finally:
        db.close()

    print("Bulk-upserting OHLCV (this may take a minute)...")
    n = bulk_upsert_ohlcv(conn, stock_ids)
    conn.close()
    print(f"OHLCV rows upserted: {n:,}")

    print("Refreshing stock snapshots from latest OHLCV...")
    update_stock_snapshot_from_latest_ohlcv()

    print("Done.")


if __name__ == "__main__":
    main()
