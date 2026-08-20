"""
Proxy Rotator
=============

Rotating proxy / user-agent pool for NEPSE web scrapers.

Configuration (environment variables or .env):
    PROXY_LIST   – comma-separated proxy URLs, e.g.:
                   "http://user:pass@1.2.3.4:8080,http://5.6.7.8:3128"
                   If empty / unset → check PROXY_LIST_URL before going direct.

    PROXY_LIST_URL – URL of a free proxy list to fetch on startup (and refresh
                     periodically).  If PROXY_LIST is also set, both sources are
                     merged.  Two response formats are supported:

                     • Plain text (one entry per line):
                         ip:port
                         ip:port
                       Each entry is wrapped as http://ip:port automatically.
                       Supports ProxyScrape, GitHub raw lists, etc.

                     • JSON with a "data" array of {ip, port} objects
                       (GeoNode API format):
                         {"data": [{"ip": "1.2.3.4", "port": "8080"}, ...]}

                     Example plain-text sources (free, no auth required):
                       https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=5000
                       https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt
                       https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt

                     Example JSON source:
                       https://proxylist.geonode.com/api/proxy-list?limit=100&sort_by=lastChecked&sort_type=desc&protocols=http

    PROXY_LIST_REFRESH_INTERVAL – how often (seconds) to re-fetch PROXY_LIST_URL.
                                   Default: 3600 (1 hour).  Set to 0 to disable.

    NEPAL_PROXY_LIST – comma-separated Nepal-IP proxies (geo-blocked scrapers).
                       Falls back to PROXY_LIST if unset.

Features:
  - Round-robin proxy rotation across the configured pool.
  - Temporary ban (60 s) of proxies that return network errors 3+ times.
  - Rate-limit ban (300 s default) for proxies that get 429 responses.
  - Exponential backoff between retry attempts (200 ms → 400 ms → 800 ms …).
  - 30 rotating User-Agent strings (common desktop + mobile browsers).
  - Varied Accept-Language headers to avoid fingerprinting.
  - Thread-safe and async-safe.
  - Optional auto-refresh of a remote free-proxy list (PROXY_LIST_URL).

Usage:
    from .proxy_rotator import ProxyRotator

    rotator = ProxyRotator()          # singleton typically held by each scraper

    # async context
    headers, proxy_url = rotator.next_async()
    await rotator.jitter(min_ms, max_ms)               # random sleep
    await rotator.exponential_jitter(attempt)           # 200·2^attempt ms

    rotator.report_success(proxy)
    rotator.report_failure(proxy)
    rotator.report_rate_limited(proxy, retry_after_s)   # for 429 responses
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
import threading
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Rotating user agents ──────────────────────────────────────────────────────
_USER_AGENTS: List[str] = [
    # Chrome Windows (most common desktop)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    # Chrome macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Firefox Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    # Firefox macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.3; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13.6; rv:122.0) Gecko/20100101 Firefox/122.0",
    # Safari macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 12_7_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.4 Safari/605.1.15",
    # Edge Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    # Chrome Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Firefox Linux
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0",
    # Mobile — Android Chrome
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.6261.119 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.230 Mobile Safari/537.36",
    # Mobile — iPhone Safari
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    # Mobile — Firefox Android
    "Mozilla/5.0 (Android 14; Mobile; rv:124.0) Gecko/124.0 Firefox/124.0",
    # Older Chrome to blend into logs
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36",
]

# Varied Accept-Language headers to avoid fingerprinting
_ACCEPT_LANGUAGES: List[str] = [
    "en-US,en;q=0.9,ne;q=0.8",
    "en-GB,en;q=0.9",
    "en-US,en;q=0.8",
    "en;q=0.9,ne;q=0.7",
    "en-US,en;q=0.9,hi;q=0.7",
    "en-AU,en;q=0.9",
    "en-CA,en;q=0.8,fr;q=0.5",
    "en-IN,en;q=0.9",
]

# ── Remote proxy-list fetcher ─────────────────────────────────────────────────

def fetch_proxies_from_url(url: str, timeout: int = 10) -> List[str]:
    """
    Fetch a free proxy list from a remote URL.

    Supports two response formats:

    * **Plain text** (one entry per line, ProxyScrape / GitHub raw lists):
      ``ip:port`` lines.  Entries without a scheme are wrapped as
      ``http://ip:port``.

    * **GeoNode JSON** (``{"data": [{"ip": "...", "port": "..."}, ...]}``)

    Returns a deduplicated list of ``scheme://ip:port`` strings.
    Returns an empty list on any network or parse error (logged as WARNING).
    """
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; nepse-bot/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_bytes = resp.read(1_048_576)  # max 1 MB
        raw = raw_bytes.decode("utf-8", errors="replace").strip()
    except Exception as exc:
        logger.warning("fetch_proxies_from_url: failed to fetch %s — %s", url, exc)
        return []

    # ── Try JSON (GeoNode) first ──────────────────────────────────────────
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
            proxies: List[str] = []
            for entry in data["data"]:
                ip = entry.get("ip", "").strip()
                port = str(entry.get("port", "")).strip()
                if ip and port:
                    proxies.append(f"http://{ip}:{port}")
            if proxies:
                logger.info(
                    "fetch_proxies_from_url: loaded %d proxies (JSON) from %s",
                    len(proxies),
                    url,
                )
                return proxies
    except (json.JSONDecodeError, TypeError, KeyError):
        pass  # fall through to plain-text parsing

    # ── Plain text (one entry per line) ──────────────────────────────────
    proxies = []
    seen: set = set()
    for line in raw.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        # Strip inline comments (e.g. "1.2.3.4:8080  # fast")
        entry = entry.split("#")[0].strip()
        if not entry:
            continue
        # Add scheme if missing
        if "://" not in entry:
            entry = f"http://{entry}"
        if entry not in seen:
            seen.add(entry)
            proxies.append(entry)

    if proxies:
        logger.info(
            "fetch_proxies_from_url: loaded %d proxies (text) from %s",
            len(proxies),
            url,
        )
    else:
        logger.warning("fetch_proxies_from_url: no proxies parsed from %s", url)
    return proxies


# Seconds before a network-failed proxy is retried
_BAN_DURATION_S: float = 60.0
# Consecutive network failures before a proxy is banned
_FAIL_THRESHOLD: int = 3
# Default rate-limit ban duration (applies on 429 responses)
_RATE_LIMIT_BAN_S: float = 300.0


class ProxyRotator:
    """
    Thread-safe proxy + user-agent rotator.

    If no proxy list is configured all public methods still work — they
    just return `proxy=None` so httpx goes direct.

    Handles two distinct failure modes:
      * Network errors   → `report_failure()`    → 60 s ban after 3 failures
      * HTTP 429         → `report_rate_limited()` → 300 s ban immediately
    """

    def __init__(self, proxy_env_var: str = "PROXY_LIST") -> None:
        raw = os.environ.get(proxy_env_var, "").strip()
        static_proxies: List[str] = (
            [p.strip() for p in raw.split(",") if p.strip()] if raw else []
        )

        self._index: int = 0
        self._failures: Dict[str, int] = {}        # proxy → consecutive failures
        self._banned_until: Dict[str, float] = {}  # proxy → epoch (network ban)
        self._rl_until: Dict[str, float] = {}      # proxy → epoch (rate-limit ban)
        self._lock = threading.Lock()

        # ── Remote proxy-list URL ──────────────────────────────────────────
        self._proxy_list_url: Optional[str] = os.environ.get(
            "PROXY_LIST_URL", ""
        ).strip() or None

        refresh_raw = os.environ.get("PROXY_LIST_REFRESH_INTERVAL", "3600").strip()
        try:
            self._refresh_interval: float = max(0.0, float(refresh_raw))
        except ValueError:
            self._refresh_interval = 3600.0

        url_proxies: List[str] = []
        if self._proxy_list_url:
            url_proxies = fetch_proxies_from_url(self._proxy_list_url)

        # Merge: static list first (higher priority / authenticated proxies),
        # then remote list; deduplicate while preserving order.
        seen: set = set(static_proxies)
        merged = list(static_proxies)
        for p in url_proxies:
            if p not in seen:
                seen.add(p)
                merged.append(p)
        self._proxies: List[str] = merged

        if self._proxies:
            logger.info(
                "ProxyRotator: %d proxies loaded (%d static, %d from URL)",
                len(self._proxies),
                len(static_proxies),
                len(url_proxies),
            )
        else:
            logger.debug("ProxyRotator: no proxies configured — going direct")

        # ── Background refresh thread ──────────────────────────────────────
        if self._proxy_list_url and self._refresh_interval > 0:
            t = threading.Thread(
                target=self._refresh_loop,
                name="proxy-rotator-refresh",
                daemon=True,
            )
            t.start()
            logger.debug(
                "ProxyRotator: refresh thread started (interval=%.0fs, url=%s)",
                self._refresh_interval,
                self._proxy_list_url,
            )

    # ── background refresh ────────────────────────────────────────────────────

    def _refresh_loop(self) -> None:
        """Background thread: re-fetches PROXY_LIST_URL every refresh_interval s."""
        while True:
            time.sleep(self._refresh_interval)
            try:
                new_proxies = fetch_proxies_from_url(self._proxy_list_url)  # type: ignore[arg-type]
                if new_proxies:
                    with self._lock:
                        # Keep static proxies at front; replace the URL-sourced tail
                        static_raw = os.environ.get("PROXY_LIST", "").strip()
                        static: List[str] = (
                            [p.strip() for p in static_raw.split(",") if p.strip()]
                            if static_raw
                            else []
                        )
                        seen: set = set(static)
                        merged = list(static)
                        for p in new_proxies:
                            if p not in seen:
                                seen.add(p)
                                merged.append(p)
                        self._proxies = merged
                        # Purge ban state for proxies no longer in pool
                        current = set(merged)
                        for d in (self._failures, self._banned_until, self._rl_until):
                            for k in list(d.keys()):
                                if k not in current:
                                    del d[k]
                    logger.info(
                        "ProxyRotator: refreshed pool → %d proxies", len(self._proxies)
                    )
            except Exception as exc:
                logger.warning("ProxyRotator: refresh error — %s", exc)

    # ── proxy selection ──────────────────────────────────────────────────────

    def _pick_proxy(self) -> Optional[str]:
        """
        Pick the next available proxy. Skips both network-banned and
        rate-limited proxies. Returns None if the pool is empty.
        """
        if not self._proxies:
            return None
        now = time.time()
        n = len(self._proxies)
        for _ in range(n):
            candidate = self._proxies[self._index % n]
            self._index = (self._index + 1) % n
            net_ok = self._banned_until.get(candidate, 0) <= now
            rl_ok = self._rl_until.get(candidate, 0) <= now
            if net_ok and rl_ok:
                return candidate
        # All proxies unavailable — pick whichever recovers soonest
        best = min(
            self._proxies,
            key=lambda p: max(
                self._banned_until.get(p, 0),
                self._rl_until.get(p, 0),
            ),
        )
        logger.warning("ProxyRotator: all proxies unavailable; using %s anyway", best)
        return best

    def _random_ua(self) -> str:
        return random.choice(_USER_AGENTS)

    def _random_accept_language(self) -> str:
        return random.choice(_ACCEPT_LANGUAGES)

    # ── public API ───────────────────────────────────────────────────────────

    def _build_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": self._random_ua(),
            "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.9",
            "Accept-Language": self._random_accept_language(),
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "DNT": "1",
            "Upgrade-Insecure-Requests": "1",
        }

    def next_sync(self) -> Tuple[Dict[str, str], Optional[str]]:
        """Returns (headers_dict, proxy_url_or_None). For synchronous code."""
        with self._lock:
            proxy = self._pick_proxy()
        return self._build_headers(), proxy

    def next_async(self) -> Tuple[Dict[str, str], Optional[str]]:
        """Same as next_sync but safe to call from async context (non-blocking)."""
        with self._lock:
            proxy = self._pick_proxy()
        return self._build_headers(), proxy

    def report_failure(self, proxy: Optional[str]) -> None:
        """Record a network failure for a proxy. Bans it if threshold exceeded."""
        if not proxy:
            return
        with self._lock:
            self._failures[proxy] = self._failures.get(proxy, 0) + 1
            if self._failures[proxy] >= _FAIL_THRESHOLD:
                self._banned_until[proxy] = time.time() + _BAN_DURATION_S
                logger.warning(
                    "ProxyRotator: proxy %s network-banned for %.0fs after %d failures",
                    proxy,
                    _BAN_DURATION_S,
                    self._failures[proxy],
                )

    def report_rate_limited(
        self, proxy: Optional[str], retry_after_s: float = _RATE_LIMIT_BAN_S
    ) -> None:
        """
        Record a 429 Too Many Requests response for a proxy.

        The proxy is excluded from the pool for `retry_after_s` seconds
        (default 300 s = 5 minutes). Unlike network failures this is not
        cumulative — one 429 is enough to trigger the cooldown.
        """
        if not proxy:
            return
        cooldown = max(retry_after_s, 30.0)  # never less than 30 s
        with self._lock:
            self._rl_until[proxy] = time.time() + cooldown
        logger.warning(
            "ProxyRotator: proxy %s rate-limited, cooling down %.0fs",
            proxy,
            cooldown,
        )

    def report_success(self, proxy: Optional[str]) -> None:
        """Reset all failure / rate-limit counters after a successful request."""
        if not proxy:
            return
        with self._lock:
            self._failures.pop(proxy, None)
            self._banned_until.pop(proxy, None)
            self._rl_until.pop(proxy, None)

    # ── jitter helpers ───────────────────────────────────────────────────────

    @staticmethod
    async def jitter(min_ms: float = 0.0, max_ms: float = 300.0) -> None:
        """Async sleep for a random duration in [min_ms, max_ms] milliseconds."""
        delay = random.uniform(min_ms, max_ms) / 1000.0
        await asyncio.sleep(delay)

    @staticmethod
    async def exponential_jitter(
        attempt: int,
        base_ms: float = 200.0,
        cap_ms: float = 10_000.0,
    ) -> None:
        """
        Exponential backoff with full jitter: sleeps a random duration in
        [base_ms, min(base_ms * 2^attempt, cap_ms)] milliseconds.

        Attempt 0 → 200–400 ms
        Attempt 1 → 200–800 ms
        Attempt 2 → 200–1600 ms (capped at cap_ms)
        """
        upper = min(base_ms * (2 ** attempt), cap_ms)
        delay = random.uniform(base_ms, upper) / 1000.0
        await asyncio.sleep(delay)

    @staticmethod
    def jitter_sync(min_ms: float = 0.0, max_ms: float = 300.0) -> None:
        """Synchronous random sleep for [min_ms, max_ms] milliseconds."""
        import time as _time
        delay = random.uniform(min_ms, max_ms) / 1000.0
        _time.sleep(delay)

    def httpx_proxies(self, proxy_url: Optional[str]) -> Optional[Dict[str, str]]:
        """
        Returns an httpx-compatible proxies dict, or None for direct.
        Usage: httpx.AsyncClient(proxies=rotator.httpx_proxies(proxy_url))
        """
        if not proxy_url:
            return None
        return {"http://": proxy_url, "https://": proxy_url}


# Module-level singleton so all scrapers share the same pool.
_default_rotator: Optional[ProxyRotator] = None

# Separate singleton for Nepal-IP proxies (used by geo-blocked sources).
# Configure via NEPAL_PROXY_LIST env var (comma-separated proxy URLs with
# Nepal exit nodes, e.g. SOCKS5 or HTTP proxies routed through Nepal).
# Falls back silently to direct requests when not configured.
_nepal_rotator: Optional[ProxyRotator] = None


def get_rotator() -> ProxyRotator:
    """Return (creating if needed) the process-wide ProxyRotator singleton."""
    global _default_rotator
    if _default_rotator is None:
        _default_rotator = ProxyRotator()
    return _default_rotator


def get_nepal_rotator() -> ProxyRotator:
    """
    Return a ProxyRotator that routes through Nepal-IP proxies.

    Reads from the NEPAL_PROXY_LIST environment variable.
    If not set, falls back to PROXY_LIST (i.e. same as get_rotator()).
    This is used by geo-blocked scrapers (nepsealpha, nepsealpha, etc.)
    to appear as a Nepal IP to those servers.

    Configure NEPAL_PROXY_LIST with proxies that have Nepal exit nodes, e.g.:
      NEPAL_PROXY_LIST=socks5://user:pass@np-proxy.example.com:1080,http://1.2.3.4:8080
    """
    global _nepal_rotator
    if _nepal_rotator is None:
        import os
        nepal_raw = os.environ.get("NEPAL_PROXY_LIST", "").strip()
        if nepal_raw:
            _nepal_rotator = ProxyRotator(proxy_env_var="NEPAL_PROXY_LIST")
            logger.info("Nepal ProxyRotator: %d Nepal-IP proxies loaded", len(_nepal_rotator._proxies))
        else:
            # No Nepal-specific proxies — fall back to default pool
            logger.debug(
                "NEPAL_PROXY_LIST not set; geo-blocked scrapers will go direct "
                "(set NEPAL_PROXY_LIST=<nepal-ip-proxy-urls> to enable geo-bypass)"
            )
            _nepal_rotator = get_rotator()
    return _nepal_rotator
