"""
High-frequency market-depth poller.

Runs as a single asyncio task during NEPSE trading hours (11:00–15:00 NPT)
and keeps an in-memory ring buffer of the latest depth snapshots for a
watchlist of symbols. Outside market hours the loop idles and the buffer
is marked stale.

Design:

- **Watchlist-driven**: only polls symbols added via ``set_watchlist``
  (caller typically passes the top-N recommendation picks + pinned list).
- **Per-symbol cadence** ``POLL_INTERVAL_SECONDS`` (default 2 s).
- **Global token-bucket rate limiter** caps the aggregate request rate at
  ``POLL_GLOBAL_RPS`` so growing the watchlist cannot cause upstream bans.
- **Ring buffer** (``collections.deque``) keeps the last ``BUFFER_SIZE``
  snapshots per symbol.
- **Durable**: writes to ``raw_cache`` once per minute per symbol so REST
  readers still see recent data after a poller restart.
- **Fail-quiet**: a single source failure never kills the loop;
  ``fetch_live_depth`` itself walks the source chain.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Deque, Dict, Iterable, List, Optional, Set

from app.services.data import market_hours
from app.services.data import raw_cache
from app.services.data.depth_live_sources import fetch_live_depth

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Environment-driven config                                                    #
# --------------------------------------------------------------------------- #

def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return float(raw) if raw is not None and raw != "" else default
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw is not None and raw != "" else default
    except (TypeError, ValueError):
        return default


POLL_INTERVAL_SECONDS = max(1.0, _env_float("DEPTH_POLL_INTERVAL_SECONDS", 2.0))
POLL_GLOBAL_RPS = max(0.5, _env_float("DEPTH_POLL_GLOBAL_RPS", 4.0))
BUFFER_SIZE = max(10, _env_int("DEPTH_BUFFER_SIZE", 120))
STALE_AFTER_SECONDS = max(5.0, _env_float("DEPTH_STALE_AFTER_SECONDS", 15.0))
DB_SNAPSHOT_EVERY_SECONDS = max(10.0, _env_float("DEPTH_DB_SNAPSHOT_EVERY_SECONDS", 60.0))
POLL_ENABLED = (os.getenv("DEPTH_POLLER_ENABLED", "true").lower() in {"1", "true", "yes", "on"})


# --------------------------------------------------------------------------- #
# Token bucket (per-process, async-safe)                                       #
# --------------------------------------------------------------------------- #

class _TokenBucket:
    def __init__(self, rate_per_second: float, capacity: Optional[float] = None):
        self.rate = float(rate_per_second)
        self.capacity = float(capacity or max(1.0, self.rate))
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._last
                self._last = now
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                needed = 1.0 - self._tokens
                wait = needed / self.rate
            # unreachable
                await asyncio.sleep(wait)


# --------------------------------------------------------------------------- #
# Ring buffer                                                                  #
# --------------------------------------------------------------------------- #

@dataclass
class DepthSnapshot:
    symbol: str
    payload: Dict[str, Any]      # full snapshot incl. metrics
    received_at: float           # monotonic seconds (for age)
    received_wall: str           # ISO UTC


class DepthRingBuffer:
    def __init__(self, size: int = BUFFER_SIZE):
        self._size = size
        self._buf: Dict[str, Deque[DepthSnapshot]] = {}
        self._lock = asyncio.Lock()

    async def push(self, symbol: str, payload: Dict[str, Any]) -> None:
        async with self._lock:
            q = self._buf.setdefault(symbol.upper(), deque(maxlen=self._size))
            q.append(
                DepthSnapshot(
                    symbol=symbol.upper(),
                    payload=payload,
                    received_at=time.monotonic(),
                    received_wall=datetime.now(timezone.utc).isoformat(),
                )
            )

    def latest(self, symbol: str) -> Optional[DepthSnapshot]:
        q = self._buf.get(symbol.upper())
        if not q:
            return None
        return q[-1]

    def history(self, symbol: str, limit: int = 60) -> List[DepthSnapshot]:
        q = self._buf.get(symbol.upper())
        if not q:
            return []
        data = list(q)
        return data[-limit:] if limit > 0 else data

    def symbols(self) -> List[str]:
        return sorted(self._buf.keys())

    def clear(self, symbol: Optional[str] = None) -> None:
        if symbol:
            self._buf.pop(symbol.upper(), None)
        else:
            self._buf.clear()


# --------------------------------------------------------------------------- #
# Poller                                                                       #
# --------------------------------------------------------------------------- #

class DepthPoller:
    def __init__(
        self,
        *,
        interval_seconds: float = POLL_INTERVAL_SECONDS,
        global_rps: float = POLL_GLOBAL_RPS,
        buffer_size: int = BUFFER_SIZE,
    ):
        self.interval = interval_seconds
        self.buffer = DepthRingBuffer(size=buffer_size)
        self._bucket = _TokenBucket(rate_per_second=global_rps, capacity=global_rps)
        self._watchlist: Set[str] = set()
        self._watchlist_lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._last_db_write: Dict[str, float] = {}
        self._stats: Dict[str, Any] = {
            "polls_attempted": 0,
            "polls_succeeded": 0,
            "polls_failed": 0,
            "last_cycle_seconds": None,
            "started_at": None,
            "last_poll_npt": None,
        }

    # -- watchlist management -------------------------------------------------
    async def set_watchlist(self, symbols: Iterable[str]) -> List[str]:
        cleaned = {s.strip().upper() for s in symbols if s and s.strip()}
        async with self._watchlist_lock:
            self._watchlist = cleaned
        return sorted(cleaned)

    async def add(self, symbol: str) -> List[str]:
        async with self._watchlist_lock:
            self._watchlist.add(symbol.strip().upper())
            return sorted(self._watchlist)

    async def remove(self, symbol: str) -> List[str]:
        async with self._watchlist_lock:
            self._watchlist.discard(symbol.strip().upper())
            return sorted(self._watchlist)

    def watchlist(self) -> List[str]:
        return sorted(self._watchlist)

    # -- lifecycle ------------------------------------------------------------
    def is_running(self) -> bool:
        return bool(self._task and not self._task.done())

    async def start(self) -> None:
        if self.is_running():
            return
        self._stop.clear()
        self._stats["started_at"] = datetime.now(timezone.utc).isoformat()
        self._task = asyncio.create_task(self._run_forever(), name="depth-poller")
        logger.info(
            "DepthPoller started interval=%.2fs rps=%.2f buffer=%d",
            self.interval, self._bucket.rate, self.buffer._size,
        )

    async def stop(self) -> None:
        self._stop.set()
        t = self._task
        self._task = None
        if t:
            try:
                await asyncio.wait_for(t, timeout=5.0)
            except asyncio.TimeoutError:
                t.cancel()
            except Exception:
                pass
        logger.info("DepthPoller stopped")

    # -- main loop ------------------------------------------------------------
    async def _run_forever(self) -> None:
        try:
            while not self._stop.is_set():
                status = market_hours.session_status()
                self._stats["last_poll_npt"] = status.now_npt.isoformat()

                if not status.is_poll_window:
                    # Sleep until next poll window, but wake every 60 s to
                    # keep stats fresh and pick up config changes.
                    sleep_for = min(60.0, max(10.0, status.seconds_to_open or 60.0))
                    await self._sleep(sleep_for)
                    continue

                cycle_start = time.monotonic()
                async with self._watchlist_lock:
                    symbols = sorted(self._watchlist)

                for sym in symbols:
                    if self._stop.is_set():
                        break
                    await self._bucket.acquire()
                    await self._poll_once(sym)

                cycle_elapsed = time.monotonic() - cycle_start
                self._stats["last_cycle_seconds"] = round(cycle_elapsed, 3)
                # Aim for one cycle per `interval`; if symbols took longer,
                # immediately start the next cycle.
                gap = self.interval - cycle_elapsed
                if gap > 0:
                    await self._sleep(gap)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("DepthPoller crashed: %s", exc)

    async def _poll_once(self, symbol: str) -> None:
        self._stats["polls_attempted"] += 1
        try:
            snap = await asyncio.to_thread(fetch_live_depth, symbol)
        except Exception as exc:
            self._stats["polls_failed"] += 1
            logger.debug("depth poll %s raised: %s", symbol, exc)
            return
        if not snap:
            self._stats["polls_failed"] += 1
            return
        await self.buffer.push(symbol, snap)
        self._stats["polls_succeeded"] += 1

        # Durable snapshot every DB_SNAPSHOT_EVERY_SECONDS per symbol.
        now = time.monotonic()
        if now - self._last_db_write.get(symbol, 0.0) >= DB_SNAPSHOT_EVERY_SECONDS:
            self._last_db_write[symbol] = now
            try:
                raw_cache.save(
                    dataset="market_depth_live",
                    source=snap.get("source") or "depth-poller",
                    symbol=symbol,
                    payload=snap,
                )
            except Exception as exc:  # pragma: no cover
                logger.debug("raw_cache save failed for %s: %s", symbol, exc)

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=max(0.05, seconds))
        except asyncio.TimeoutError:
            pass

    # -- public readers -------------------------------------------------------
    def latest_for(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Return the freshest depth snapshot with age + staleness info."""
        snap = self.buffer.latest(symbol)
        if not snap:
            return None
        age = time.monotonic() - snap.received_at
        status = market_hours.session_status()
        is_stale = (not status.is_open) or (age > STALE_AFTER_SECONDS)
        out = dict(snap.payload)
        out["age_seconds"] = round(age, 3)
        out["received_at"] = snap.received_wall
        out["is_stale"] = bool(is_stale)
        out["market_state"] = status.reason
        return out

    def depth_for_engine(self, symbols: Iterable[str]) -> Dict[str, Dict[str, Any]]:
        """
        Shape the ring-buffer contents for
        ``enhanced_recommendation_engine.rank_universe``'s
        ``depth_by_symbol`` argument. Stale entries are omitted so the
        engine renormalises weights away from depth automatically.
        """
        out: Dict[str, Dict[str, Any]] = {}
        for s in symbols:
            latest = self.latest_for(s)
            if latest and not latest.get("is_stale"):
                out[s.upper()] = latest
        return out

    def stats(self) -> Dict[str, Any]:
        return dict(self._stats) | {
            "interval_seconds": self.interval,
            "global_rps": self._bucket.rate,
            "buffer_size": self.buffer._size,
            "watchlist": self.watchlist(),
            "running": self.is_running(),
        }


# --------------------------------------------------------------------------- #
# Singleton accessor                                                           #
# --------------------------------------------------------------------------- #

_poller: Optional[DepthPoller] = None


def get_depth_poller() -> DepthPoller:
    global _poller
    if _poller is None:
        _poller = DepthPoller()
    return _poller


__all__ = [
    "DepthPoller",
    "DepthRingBuffer",
    "DepthSnapshot",
    "get_depth_poller",
    "POLL_INTERVAL_SECONDS",
    "POLL_GLOBAL_RPS",
    "BUFFER_SIZE",
    "STALE_AFTER_SECONDS",
    "POLL_ENABLED",
]
