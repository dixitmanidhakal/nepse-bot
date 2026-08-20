"""
In-Memory TTL Cache
===================

Thread-safe, per-process cache used to deduplicate upstream fetches to
GitHub-raw JSON/CSV and HTML-scrape endpoints.

Serverless note:
    On Vercel each warm Lambda container keeps this cache alive, so within a
    warm lifetime (usually 5–15 minutes) we avoid refetching large payloads
    like `nepse_data.json` (145 KB) dozens of times.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Tuple, Optional


class TTLCache:
    """Very small TTL cache — `get_or_set(key, loader, ttl_s)` is the happy path."""

    def __init__(self) -> None:
        self._store: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        now = time.time()
        with self._lock:
            entry = self._store.get(key)
            if not entry:
                return None
            expires_at, value = entry
            if expires_at < now:
                self._store.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any, ttl_s: float) -> None:
        expires_at = time.time() + max(0.0, ttl_s)
        with self._lock:
            self._store[key] = (expires_at, value)

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def get_or_set(
        self,
        key: str,
        loader: Callable[[], Any],
        ttl_s: float | None = None,
        *,
        ttl: float | None = None,
    ) -> Any:
        effective_ttl = ttl if ttl is not None else (ttl_s if ttl_s is not None else 60.0)
        cached = self.get(key)
        if cached is not None:
            return cached
        value = loader()
        # Only cache truthy results — empty containers from transient failures
        # must not poison the cache for the full TTL window.
        if value:
            self.set(key, value, effective_ttl)
        return value

    async def aget_or_set(
        self,
        key: str,
        aloader: Callable[[], Any],
        ttl_s: float | None = None,
        *,
        ttl: float | None = None,
    ) -> Any:
        """Async variant — `aloader` must be an awaitable-returning zero-arg callable.

        Accepts both positional `ttl_s` and keyword `ttl` for convenience.

        Only caches truthy results — empty lists/dicts from transient network
        errors are NOT stored, ensuring the next call retries the upstream source.
        """
        effective_ttl = ttl if ttl is not None else (ttl_s if ttl_s is not None else 60.0)
        cached = self.get(key)
        if cached is not None:
            return cached
        value = await aloader()
        # Only persist non-None AND non-empty results; empty containers ([] / {})
        # from failed fetches must NOT poison the cache for the full TTL window.
        if value:
            self.set(key, value, effective_ttl)
        return value

    def stats(self) -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            live = [k for k, (exp, _) in self._store.items() if exp >= now]
            return {"size": len(self._store), "live": len(live), "keys": live[:25]}


_GLOBAL_CACHE = TTLCache()


def get_cache() -> TTLCache:
    """Return the process-wide TTLCache singleton."""
    return _GLOBAL_CACHE
