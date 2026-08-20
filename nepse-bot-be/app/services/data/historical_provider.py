"""
Historical Data Provider (SQLite, read-only).

Ports the offline data-fetch pattern used by nepse-quant-terminal
(``backend/quant_pro/data_io.py`` + ``database.py``) into a lightweight
service for nepse-bot. The provider:

- Reads from the shared ``nepse_data_public.db`` SQLite file (path
  configured via ``QUANT_TERMINAL_DB_PATH`` env var).
- Serves OHLCV as **pandas DataFrames**, matching the column contract
  (``[date, open, high, low, close, volume]``) that the rest of the bot
  expects.
- Provides batch access (``load_universe``) so the recommendation engine
  can score hundreds of symbols in a single query.
- Supports a minimum history filter and date-range slicing.

No network calls, no authentication, no scraping. This is a pure
read-only adapter around the offline SQLite snapshot and is safe to call
from request-handlers.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Coroutine, Dict, Iterable, List, Optional, TypeVar

import pandas as pd

from app.config import settings

_T = TypeVar("_T")
_HP_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="hp_async"
)


def _run_async(coro: Coroutine[Any, Any, _T]) -> _T:
    """Run async coroutine safely from sync code in any thread context."""
    future = _HP_EXECUTOR.submit(asyncio.run, coro)
    return future.result(timeout=45)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SymbolMetadata:
    symbol: str
    row_count: int
    first_date: Optional[str]
    last_date: Optional[str]


class HistoricalDataProvider:
    """
    Thread-safe, read-only SQLite historical OHLCV provider.

    Usage::

        provider = get_historical_provider()
        if provider.is_available():
            df = provider.load_ohlcv("NABIL", min_rows=60)
            panel = provider.load_universe(min_rows=120)
    """

    _OHLCV_QUERY = (
        "SELECT date, open, high, low, close, volume "
        "FROM stock_prices "
        "WHERE symbol = ? "
    )

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._configured_path = db_path or settings.quant_terminal_db_path
        self._lock = threading.Lock()
        self._symbols_cache: Optional[List[str]] = None
        self._metadata_cache: Optional[Dict[str, SymbolMetadata]] = None

    # --------------------------------------------------------------------- #
    # Availability                                                          #
    # --------------------------------------------------------------------- #

    def db_path(self) -> Optional[Path]:
        if not self._configured_path:
            return None
        p = Path(self._configured_path).expanduser()
        if p.exists():
            return p
        # If a relative path was given (e.g. "data/nepse_data_public.db"),
        # resolve it against the backend project root (parent of `app/`).
        if not p.is_absolute():
            project_root = Path(__file__).resolve().parents[3]
            candidate = (project_root / p).resolve()
            if candidate.exists():
                return candidate
        return None

    def is_available(self) -> bool:
        return self.db_path() is not None

    # --------------------------------------------------------------------- #
    # Connection helper                                                     #
    # --------------------------------------------------------------------- #

    @contextmanager
    def _connect(self):
        p = self.db_path()
        if p is None:
            raise RuntimeError(
                "Historical SQLite DB not configured. "
                "Set QUANT_TERMINAL_DB_PATH in .env to enable offline OHLCV."
            )
        uri = f"file:{p}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # --------------------------------------------------------------------- #
    # Single-symbol loader                                                  #
    # --------------------------------------------------------------------- #

    def load_ohlcv(
        self,
        symbol: str,
        *,
        start_date: Optional[datetime | date | str] = None,
        end_date: Optional[datetime | date | str] = None,
        min_rows: int = 0,
    ) -> pd.DataFrame:
        """
        Load OHLCV for a single symbol as a chronologically-sorted DataFrame.

        Returns an empty DataFrame if the symbol isn't present or if
        ``min_rows`` is not met.
        """
        if not self.is_available():
            return pd.DataFrame()

        sym = str(symbol).strip().upper()
        if not sym:
            return pd.DataFrame()

        query = self._OHLCV_QUERY
        params: List = [sym]

        if start_date is not None:
            query += " AND date >= ?"
            params.append(_to_date_str(start_date))
        if end_date is not None:
            query += " AND date <= ?"
            params.append(_to_date_str(end_date))

        query += " ORDER BY date ASC"

        try:
            with self._connect() as conn:
                df = pd.read_sql_query(query, conn, params=params)
        except sqlite3.Error as exc:
            logger.warning("SQLite load failed for %s: %s", sym, exc)
            return pd.DataFrame()

        if df.empty:
            return df
        if min_rows and len(df) < min_rows:
            return pd.DataFrame()

        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

        # ── Live backfill: if DB data is stale, append from samirwagle ───────
        df = self._backfill_from_samirwagle(sym, df)

        return df

    def _backfill_from_samirwagle(self, symbol: str, df: pd.DataFrame) -> pd.DataFrame:
        """
        If the DB DataFrame ends more than 3 trading days ago, fetch the
        missing bars from the SamirWagle GitHub scraper and append them.
        This fills the gap between the static SQLite snapshot and today.
        """
        try:
            if df.empty:
                return df
            last_date = df["date"].max()
            if pd.isna(last_date):
                return df
            stale_cutoff = pd.Timestamp(datetime.utcnow().date() - timedelta(days=3))
            if last_date >= stale_cutoff:
                return df  # fresh enough

            from app.services.data.free_sources import samirwagle
            rows = _run_async(samirwagle.get_symbol_prices(symbol))

            if not rows:
                return df

            new_rows = []
            for r in rows:
                d = r.get("date", "")[:10]
                if not d:
                    continue
                try:
                    dt = pd.Timestamp(d)
                except Exception:
                    continue
                if dt <= last_date:
                    continue
                new_rows.append({
                    "date":   dt,
                    "open":   r.get("open"),
                    "high":   r.get("high"),
                    "low":    r.get("low"),
                    "close":  r.get("ltp") or r.get("close"),
                    "volume": r.get("qty") or r.get("volume"),
                })

            if not new_rows:
                return df

            extra = pd.DataFrame(new_rows)
            extra["date"] = pd.to_datetime(extra["date"], errors="coerce")
            extra = extra.dropna(subset=["date"])
            merged = pd.concat([df, extra], ignore_index=True)
            merged = merged.sort_values("date").reset_index(drop=True)
            logger.debug(
                "Backfilled %s: +%d rows (now %d total, up to %s)",
                symbol, len(new_rows), len(merged), merged["date"].max().date(),
            )
            return merged

        except Exception as exc:
            logger.debug("_backfill_from_samirwagle(%s) failed: %s", symbol, exc)
            return df

    # --------------------------------------------------------------------- #
    # Universe loader                                                       #
    # --------------------------------------------------------------------- #

    def list_symbols(self, refresh: bool = False) -> List[str]:
        """Return all distinct symbols in the SQLite DB (cached)."""
        if not self.is_available():
            return []
        if self._symbols_cache is not None and not refresh:
            return list(self._symbols_cache)
        with self._lock:
            if self._symbols_cache is not None and not refresh:
                return list(self._symbols_cache)
            try:
                with self._connect() as conn:
                    rows = conn.execute(
                        "SELECT DISTINCT symbol FROM stock_prices "
                        "WHERE symbol IS NOT NULL AND symbol != '' "
                        "ORDER BY symbol"
                    ).fetchall()
            except sqlite3.Error as exc:
                logger.warning("list_symbols failed: %s", exc)
                return []
            self._symbols_cache = [r["symbol"] for r in rows]
            return list(self._symbols_cache)

    def symbol_metadata(self, refresh: bool = False) -> Dict[str, SymbolMetadata]:
        """Per-symbol metadata (row count, first/last date). Cached."""
        if not self.is_available():
            return {}
        if self._metadata_cache is not None and not refresh:
            return dict(self._metadata_cache)
        with self._lock:
            if self._metadata_cache is not None and not refresh:
                return dict(self._metadata_cache)
            try:
                with self._connect() as conn:
                    rows = conn.execute(
                        "SELECT symbol, COUNT(*) AS n, "
                        "MIN(date) AS first_date, MAX(date) AS last_date "
                        "FROM stock_prices "
                        "WHERE symbol IS NOT NULL "
                        "GROUP BY symbol "
                        "ORDER BY symbol"
                    ).fetchall()
            except sqlite3.Error as exc:
                logger.warning("symbol_metadata failed: %s", exc)
                return {}
            meta: Dict[str, SymbolMetadata] = {}
            for r in rows:
                sym = r["symbol"]
                if not sym:
                    continue
                meta[sym] = SymbolMetadata(
                    symbol=sym,
                    row_count=int(r["n"] or 0),
                    first_date=str(r["first_date"])[:10] if r["first_date"] else None,
                    last_date=str(r["last_date"])[:10] if r["last_date"] else None,
                )
            self._metadata_cache = meta
            return dict(meta)

    def load_universe(
        self,
        symbols: Optional[Iterable[str]] = None,
        *,
        start_date: Optional[datetime | date | str] = None,
        end_date: Optional[datetime | date | str] = None,
        min_rows: int = 60,
        max_symbols: Optional[int] = None,
    ) -> Dict[str, pd.DataFrame]:
        """
        Load a universe of symbols into a dict of DataFrames.

        - If ``symbols`` is None, every symbol in the DB is loaded.
        - Only symbols with ``>= min_rows`` rows are returned.
        - ``max_symbols`` truncates the result for quick smoke-tests.
        """
        if not self.is_available():
            return {}

        if symbols is None:
            candidates = self.list_symbols()
        else:
            candidates = [str(s).strip().upper() for s in symbols if str(s).strip()]

        if max_symbols and max_symbols > 0:
            candidates = candidates[:max_symbols]

        if not candidates:
            return {}

        # Bulk-load via a single SQL query to amortise connection overhead.
        placeholders = ",".join(["?"] * len(candidates))
        query = (
            "SELECT symbol, date, open, high, low, close, volume "
            "FROM stock_prices "
            f"WHERE symbol IN ({placeholders}) "
        )
        params: List = list(candidates)
        if start_date is not None:
            query += "AND date >= ? "
            params.append(_to_date_str(start_date))
        if end_date is not None:
            query += "AND date <= ? "
            params.append(_to_date_str(end_date))
        query += "ORDER BY symbol ASC, date ASC"

        try:
            with self._connect() as conn:
                full = pd.read_sql_query(query, conn, params=params)
        except sqlite3.Error as exc:
            logger.warning("bulk OHLCV load failed: %s", exc)
            return {}

        if full.empty:
            return {}

        full["date"] = pd.to_datetime(full["date"], errors="coerce")
        full = full.dropna(subset=["date"])

        panel: Dict[str, pd.DataFrame] = {}
        for sym, grp in full.groupby("symbol", sort=False):
            if len(grp) < min_rows:
                continue
            panel[str(sym).upper()] = (
                grp.drop(columns=["symbol"]).sort_values("date").reset_index(drop=True)
            )

        # ── Inject live bar for stale symbols (1 network call for all syms) ──
        panel = self._inject_live_bars(panel)
        return panel

    def _inject_live_bars(self, panel: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
        """
        For each symbol in the panel whose last date is ≥ 1 trading day stale,
        append a synthetic bar from the live market snapshot.
        Uses a single aggregator.live_market() call shared across all symbols.
        """
        try:
            today = pd.Timestamp(datetime.utcnow().date())
            stale_cutoff = today - timedelta(days=2)

            # Check if any symbols need injection
            needs_update = {sym for sym, df in panel.items() if df["date"].max() < stale_cutoff}
            if not needs_update:
                return panel

            from app.services.data.free_sources import aggregator
            live_rows = _run_async(aggregator.live_market())

            live_map: Dict[str, dict] = {
                (r.get("symbol") or "").upper(): r
                for r in (live_rows or [])
                if r.get("symbol")
            }

            injected = 0
            for sym in needs_update:
                live = live_map.get(sym)
                if not live:
                    continue
                ltp = live.get("ltp") or live.get("close") or live.get("price")
                vol = live.get("volume") or live.get("qty") or 0
                opn = live.get("open") or ltp
                high = live.get("high") or ltp
                low  = live.get("low") or ltp
                if not ltp:
                    continue
                new_bar = pd.DataFrame([{
                    "date":   today,
                    "open":   float(opn),
                    "high":   float(high),
                    "low":    float(low),
                    "close":  float(ltp),
                    "volume": float(vol),
                }])
                df = panel[sym]
                # avoid duplicate dates
                df = df[df["date"] < today]
                panel[sym] = pd.concat([df, new_bar], ignore_index=True).sort_values("date").reset_index(drop=True)
                injected += 1

            if injected:
                logger.info("_inject_live_bars: injected today's bar for %d symbols", injected)
        except Exception as exc:
            logger.debug("_inject_live_bars failed: %s", exc)

        return panel

    # --------------------------------------------------------------------- #
    # Latest snapshot (for Dashboard)                                       #
    # --------------------------------------------------------------------- #

    def latest_snapshot(self, max_symbols: Optional[int] = None) -> pd.DataFrame:
        """
        Return the last-known close/volume per symbol as a DataFrame.

        Columns: ``symbol, date, close, previous_close, volume, change_pct``.
        """
        if not self.is_available():
            return pd.DataFrame()

        try:
            with self._connect() as conn:
                # Window-function style via GROUP BY max(date)
                df = pd.read_sql_query(
                    "SELECT sp.symbol, sp.date, sp.close, sp.volume "
                    "FROM stock_prices sp "
                    "INNER JOIN ( "
                    "  SELECT symbol, MAX(date) AS max_date FROM stock_prices GROUP BY symbol "
                    ") mx ON mx.symbol = sp.symbol AND mx.max_date = sp.date "
                    "ORDER BY sp.symbol",
                    conn,
                )
                if df.empty:
                    return df
                prev = pd.read_sql_query(
                    "SELECT symbol, date, close FROM stock_prices "
                    "WHERE (symbol, date) IN ( "
                    "  SELECT sp.symbol, MAX(sp.date) FROM stock_prices sp "
                    "  INNER JOIN ( "
                    "    SELECT symbol, MAX(date) AS max_date FROM stock_prices GROUP BY symbol "
                    "  ) mx ON mx.symbol = sp.symbol AND sp.date < mx.max_date "
                    "  GROUP BY sp.symbol "
                    ")",
                    conn,
                )
        except sqlite3.Error as exc:
            logger.warning("latest_snapshot failed: %s", exc)
            return pd.DataFrame()

        prev = prev.rename(columns={"close": "previous_close", "date": "prev_date"})
        merged = df.merge(prev[["symbol", "previous_close"]], on="symbol", how="left")
        merged["change_pct"] = (
            (merged["close"] - merged["previous_close"]) / merged["previous_close"] * 100.0
        )
        if max_symbols:
            merged = merged.head(max_symbols)
        return merged


# --------------------------------------------------------------------------- #
# Helpers / module-level singleton                                            #
# --------------------------------------------------------------------------- #

def _to_date_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


_provider_singleton: Optional[HistoricalDataProvider] = None
_singleton_lock = threading.Lock()


def get_historical_provider() -> HistoricalDataProvider:
    global _provider_singleton
    if _provider_singleton is None:
        with _singleton_lock:
            if _provider_singleton is None:
                _provider_singleton = HistoricalDataProvider()
    return _provider_singleton


__all__ = [
    "HistoricalDataProvider",
    "SymbolMetadata",
    "get_historical_provider",
]
