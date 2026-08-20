"""
Resilient HTTP client with anti-ban protections.

Features
--------
1.  Per-host token-bucket rate limiter (thread-safe).
2.  User-Agent rotation from a realistic pool.
3.  Exponential backoff + jitter on 429 / 5xx.
4.  Honours the ``Retry-After`` response header.
5.  Circuit breaker: N consecutive failures → cool-down.
6.  Optional proxy rotation (HTTP/HTTPS/SOCKS5) — the caller supplies a
    list of proxies via env var ``SCRAPER_PROXIES`` or constructor arg.
7.  Request payload cache integration hook (see ``services.data.raw_cache``).

Usage
-----
    from app.services.http import get_resilient_client

    client = get_resilient_client()
    resp = client.get("https://www.nepalstock.com.np/api/nots/...")
    data = resp.json()

This module is deliberately framework-agnostic so it can be used by any
scraper (nepalstock, merolagani, nepse Unofficial API, etc.).
"""
from __future__ import annotations

import logging
import os
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────────
# Exceptions
# ────────────────────────────────────────────────────────────────────────────
class CircuitBreakerOpen(RuntimeError):
    """Too many consecutive failures — requests are being short-circuited."""


class RateLimitExceeded(RuntimeError):
    """Upstream returned 429 and the retry budget is exhausted."""


class UpstreamUnavailable(RuntimeError):
    """Upstream failed after all retries (5xx, timeout, connection)."""


# ────────────────────────────────────────────────────────────────────────────
# Configuration
# ────────────────────────────────────────────────────────────────────────────
REALISTIC_USER_AGENTS: List[str] = [
    # Chrome / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Chrome / macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Firefox / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
    "Gecko/20100101 Firefox/125.0",
    # Safari / macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    # Edge / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.2478.67",
]


@dataclass
class HostPolicy:
    """Per-host rate limit & circuit-breaker state."""
    min_interval: float = 1.2              # seconds between requests
    max_retries: int = 3
    circuit_threshold: int = 5             # consecutive failures before opening
    circuit_cooldown: float = 300.0        # seconds
    # runtime state:
    last_request_ts: float = 0.0
    consecutive_failures: int = 0
    circuit_opened_at: Optional[float] = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


# ────────────────────────────────────────────────────────────────────────────
# Client
# ────────────────────────────────────────────────────────────────────────────
class ResilientHTTPClient:
    """
    Thread-safe HTTP client with anti-ban protections.

    One instance per process is enough — see :func:`get_resilient_client`.
    """

    def __init__(
        self,
        *,
        user_agents: Optional[List[str]] = None,
        proxies: Optional[List[str]] = None,
        verify_tls: bool = False,
        timeout: float = 15.0,
    ):
        self._ua_pool = user_agents or REALISTIC_USER_AGENTS
        self._proxies = proxies or self._load_proxies_from_env()
        self._verify_tls = verify_tls
        self._timeout = timeout
        self._host_policies: Dict[str, HostPolicy] = {}
        self._host_lock = threading.Lock()

        # underlying requests session w/ connection-pooling
        self._session = requests.Session()
        retry_adapter = HTTPAdapter(
            max_retries=Retry(
                total=0,  # we manage retries ourselves for precise control
                connect=0,
                read=0,
                status=0,
            ),
            pool_connections=20,
            pool_maxsize=20,
        )
        self._session.mount("http://", retry_adapter)
        self._session.mount("https://", retry_adapter)

    # ── public API ──────────────────────────────────────────────────────
    def get(self, url: str, **kwargs) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        return self.request("POST", url, **kwargs)

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        """
        Perform a rate-limited, retried, UA/proxy-rotated HTTP request.

        Raises
        ------
        CircuitBreakerOpen   — host is in cool-down, try later.
        RateLimitExceeded    — 429 after all retries.
        UpstreamUnavailable  — 5xx / connection / timeout after all retries.
        """
        host = urlparse(url).netloc
        policy = self._get_policy(host)

        self._check_breaker(host, policy)
        self._wait_rate_limit(policy)

        last_exc: Optional[Exception] = None
        for attempt in range(policy.max_retries + 1):
            try:
                resp = self._do_request(method, url, **kwargs)

                # 2xx / 3xx → success
                if resp.status_code < 400:
                    self._record_success(policy)
                    return resp

                # 429 rate-limited → honour Retry-After, retry
                if resp.status_code == 429:
                    delay = self._parse_retry_after(resp) or self._backoff(attempt)
                    logger.warning(
                        "429 from %s (attempt %d/%d) — sleeping %.1fs",
                        host, attempt + 1, policy.max_retries + 1, delay,
                    )
                    time.sleep(delay)
                    continue

                # 5xx → backoff, retry
                if 500 <= resp.status_code < 600:
                    delay = self._backoff(attempt)
                    logger.warning(
                        "%d from %s (attempt %d/%d) — sleeping %.1fs",
                        resp.status_code, host, attempt + 1,
                        policy.max_retries + 1, delay,
                    )
                    time.sleep(delay)
                    continue

                # 4xx (!=429) → don't retry, return so caller can inspect
                self._record_success(policy)  # server spoke, so host is up
                return resp

            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                delay = self._backoff(attempt)
                logger.warning(
                    "%s %s on %s (attempt %d/%d) — sleeping %.1fs",
                    type(exc).__name__, url, host, attempt + 1,
                    policy.max_retries + 1, delay,
                )
                time.sleep(delay)
                continue

        # exhausted retries
        self._record_failure(host, policy)
        if last_exc is not None:
            raise UpstreamUnavailable(f"{host}: {last_exc}") from last_exc
        raise RateLimitExceeded(f"{host}: rate-limited after retries")

    def configure_host(
        self,
        host: str,
        *,
        min_interval: Optional[float] = None,
        max_retries: Optional[int] = None,
        circuit_threshold: Optional[int] = None,
        circuit_cooldown: Optional[float] = None,
    ) -> None:
        """Override the default per-host policy."""
        policy = self._get_policy(host)
        if min_interval is not None:
            policy.min_interval = float(min_interval)
        if max_retries is not None:
            policy.max_retries = int(max_retries)
        if circuit_threshold is not None:
            policy.circuit_threshold = int(circuit_threshold)
        if circuit_cooldown is not None:
            policy.circuit_cooldown = float(circuit_cooldown)

    # ── internals ───────────────────────────────────────────────────────
    def _get_policy(self, host: str) -> HostPolicy:
        with self._host_lock:
            if host not in self._host_policies:
                self._host_policies[host] = HostPolicy()
            return self._host_policies[host]

    def _check_breaker(self, host: str, policy: HostPolicy) -> None:
        with policy._lock:
            if policy.circuit_opened_at is None:
                return
            elapsed = time.monotonic() - policy.circuit_opened_at
            if elapsed >= policy.circuit_cooldown:
                logger.info("Circuit breaker half-open for %s — allowing probe", host)
                policy.circuit_opened_at = None
                policy.consecutive_failures = 0
                return
            remaining = policy.circuit_cooldown - elapsed
            raise CircuitBreakerOpen(
                f"{host}: circuit open — retry in {remaining:.0f}s"
            )

    def _wait_rate_limit(self, policy: HostPolicy) -> None:
        with policy._lock:
            now = time.monotonic()
            gap = now - policy.last_request_ts
            if gap < policy.min_interval:
                sleep_for = policy.min_interval - gap
                # small jitter so concurrent requests don't lock-step
                sleep_for += random.uniform(0.0, 0.25)
            else:
                sleep_for = 0.0
            policy.last_request_ts = now + sleep_for
        if sleep_for > 0:
            time.sleep(sleep_for)

    def _do_request(self, method: str, url: str, **kwargs) -> requests.Response:
        # rotate UA per request
        headers = kwargs.pop("headers", None) or {}
        headers.setdefault("User-Agent", random.choice(self._ua_pool))
        headers.setdefault("Accept", "application/json, text/plain, */*")
        headers.setdefault("Accept-Language", "en-US,en;q=0.9")

        # rotate proxy per request if we have any
        proxies = kwargs.pop("proxies", None)
        if proxies is None and self._proxies:
            proxy = random.choice(self._proxies)
            proxies = {"http": proxy, "https": proxy}

        kwargs.setdefault("timeout", self._timeout)
        kwargs.setdefault("verify", self._verify_tls)
        return self._session.request(
            method, url, headers=headers, proxies=proxies, **kwargs
        )

    def _record_success(self, policy: HostPolicy) -> None:
        with policy._lock:
            policy.consecutive_failures = 0
            policy.circuit_opened_at = None

    def _record_failure(self, host: str, policy: HostPolicy) -> None:
        with policy._lock:
            policy.consecutive_failures += 1
            if policy.consecutive_failures >= policy.circuit_threshold:
                policy.circuit_opened_at = time.monotonic()
                logger.error(
                    "Circuit breaker OPEN for %s (%d consecutive failures) — "
                    "cooling down %.0fs",
                    host,
                    policy.consecutive_failures,
                    policy.circuit_cooldown,
                )

    @staticmethod
    def _backoff(attempt: int) -> float:
        """Exponential backoff with jitter: 1s, 2s, 4s … plus up to 0.5s jitter."""
        base = min(2 ** attempt, 30.0)
        return base + random.uniform(0.0, 0.5)

    @staticmethod
    def _parse_retry_after(resp: requests.Response) -> Optional[float]:
        raw = resp.headers.get("Retry-After")
        if not raw:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _load_proxies_from_env() -> List[str]:
        raw = os.getenv("SCRAPER_PROXIES", "").strip()
        if not raw:
            return []
        proxies = [p.strip() for p in raw.split(",") if p.strip()]
        if proxies:
            logger.info("Loaded %d proxies from SCRAPER_PROXIES", len(proxies))
        return proxies


# ────────────────────────────────────────────────────────────────────────────
# Module-level singleton
# ────────────────────────────────────────────────────────────────────────────
_INSTANCE: Optional[ResilientHTTPClient] = None
_INSTANCE_LOCK = threading.Lock()


def get_resilient_client() -> ResilientHTTPClient:
    """Return the process-wide ResilientHTTPClient (lazy singleton)."""
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = ResilientHTTPClient()
                # pre-seed known NEPSE hosts with sensible defaults
                _INSTANCE.configure_host(
                    "www.nepalstock.com.np",
                    min_interval=1.5,
                    max_retries=3,
                    circuit_threshold=5,
                    circuit_cooldown=300.0,
                )
                _INSTANCE.configure_host(
                    "merolagani.com",
                    min_interval=2.0,
                    max_retries=2,
                    circuit_threshold=4,
                    circuit_cooldown=300.0,
                )
                _INSTANCE.configure_host(
                    "www.sharesansar.com",
                    min_interval=2.5,
                    max_retries=2,
                )
    return _INSTANCE
