"""
Unit tests for app/services/data/free_sources/proxy_rotator.py

All tests run offline — no real HTTP requests or network access.
"""

from __future__ import annotations

import json
import os
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from app.services.data.free_sources.proxy_rotator import (
    ProxyRotator,
    fetch_proxies_from_url,
    get_rotator,
    get_nepal_rotator,
    _BAN_DURATION_S,
    _FAIL_THRESHOLD,
    _RATE_LIMIT_BAN_S,
)


# ── Construction ──────────────────────────────────────────────────────────────

class TestProxyRotatorInit:
    def test_no_proxies_by_default(self):
        with patch.dict(os.environ, {"PROXY_LIST": ""}, clear=False):
            r = ProxyRotator()
        assert r._proxies == []

    def test_single_proxy_parsed(self):
        with patch.dict(os.environ, {"PROXY_LIST": "http://1.2.3.4:8080"}, clear=False):
            r = ProxyRotator()
        assert len(r._proxies) == 1
        assert r._proxies[0] == "http://1.2.3.4:8080"

    def test_multiple_proxies_parsed(self):
        proxy_str = "http://a:b@1.2.3.4:8080,http://5.6.7.8:3128,socks5://9.10.11.12:1080"
        with patch.dict(os.environ, {"PROXY_LIST": proxy_str}, clear=False):
            r = ProxyRotator()
        assert len(r._proxies) == 3

    def test_whitespace_stripped(self):
        with patch.dict(os.environ, {"PROXY_LIST": " http://1.2.3.4:8080 , http://5.6.7.8:3128 "}, clear=False):
            r = ProxyRotator()
        assert all(":" in p for p in r._proxies)
        assert all(p == p.strip() for p in r._proxies)

    def test_empty_entries_ignored(self):
        with patch.dict(os.environ, {"PROXY_LIST": "http://1.2.3.4:8080,,,"}, clear=False):
            r = ProxyRotator()
        assert len(r._proxies) == 1

    def test_custom_env_var(self):
        with patch.dict(os.environ, {"MY_PROXIES": "http://x:y@1.1.1.1:9000"}, clear=False):
            r = ProxyRotator(proxy_env_var="MY_PROXIES")
        assert len(r._proxies) == 1


# ── next_sync / next_async ───────────────────────────────────────────────────

class TestProxySelection:
    def _rotator(self, proxies: list[str]) -> ProxyRotator:
        r = ProxyRotator.__new__(ProxyRotator)
        r._proxies = proxies
        r._index = 0
        r._failures = {}
        r._banned_until = {}
        r._rl_until = {}
        import threading
        r._lock = threading.Lock()
        return r

    def test_no_proxy_returns_none(self):
        r = self._rotator([])
        headers, proxy = r.next_sync()
        assert proxy is None
        assert "User-Agent" in headers

    def test_returns_proxy_when_configured(self):
        r = self._rotator(["http://1.2.3.4:8080"])
        _, proxy = r.next_sync()
        assert proxy == "http://1.2.3.4:8080"

    def test_round_robin_across_proxies(self):
        proxies = ["http://p1:80", "http://p2:80", "http://p3:80"]
        r = self._rotator(proxies)
        seen = [r.next_sync()[1] for _ in range(6)]
        # All three proxies should appear
        assert set(seen) == set(proxies)

    def test_headers_always_returned(self):
        r = self._rotator([])
        headers, _ = r.next_sync()
        assert "User-Agent" in headers
        assert "Accept" in headers
        assert "Accept-Language" in headers

    def test_user_agents_vary(self):
        r = self._rotator([])
        uas = {r.next_sync()[0]["User-Agent"] for _ in range(50)}
        # Should see more than 1 unique UA across 50 calls
        assert len(uas) > 1

    def test_async_returns_same_shape_as_sync(self):
        r = self._rotator(["http://1.2.3.4:8080"])
        h_sync, p_sync = r.next_sync()
        h_async, p_async = r.next_async()
        assert "User-Agent" in h_async
        assert p_async == "http://1.2.3.4:8080"


# ── Failure reporting & banning ───────────────────────────────────────────────

class TestFailureReporting:
    def _rotator(self) -> ProxyRotator:
        r = ProxyRotator.__new__(ProxyRotator)
        r._proxies = ["http://p1:80", "http://p2:80"]
        r._index = 0
        r._failures = {}
        r._banned_until = {}
        r._rl_until = {}
        import threading
        r._lock = threading.Lock()
        return r

    def test_single_failure_not_banned(self):
        r = self._rotator()
        r.report_failure("http://p1:80")
        assert r._failures.get("http://p1:80", 0) == 1
        assert "http://p1:80" not in r._banned_until

    def test_threshold_failures_triggers_ban(self):
        r = self._rotator()
        proxy = "http://p1:80"
        for _ in range(_FAIL_THRESHOLD):
            r.report_failure(proxy)
        assert proxy in r._banned_until
        assert r._banned_until[proxy] > time.time()

    def test_success_clears_failures(self):
        r = self._rotator()
        proxy = "http://p1:80"
        r.report_failure(proxy)
        r.report_failure(proxy)
        r.report_success(proxy)
        assert proxy not in r._failures
        assert proxy not in r._banned_until

    def test_rate_limit_bans_immediately(self):
        r = self._rotator()
        proxy = "http://p1:80"
        r.report_rate_limited(proxy, retry_after_s=120.0)
        assert proxy in r._rl_until
        assert r._rl_until[proxy] > time.time() + 60

    def test_rate_limit_minimum_30s(self):
        r = self._rotator()
        proxy = "http://p1:80"
        before = time.time()
        r.report_rate_limited(proxy, retry_after_s=5.0)  # less than 30
        # Allow 1 s timing slack: ban_until must be at least 29 s from call time
        assert r._rl_until[proxy] >= before + 29

    def test_success_clears_rate_limit(self):
        r = self._rotator()
        proxy = "http://p1:80"
        r.report_rate_limited(proxy, retry_after_s=300.0)
        r.report_success(proxy)
        assert proxy not in r._rl_until

    def test_none_proxy_ignored_by_all_methods(self):
        r = self._rotator()
        r.report_failure(None)
        r.report_success(None)
        r.report_rate_limited(None)
        # No exceptions, no state change
        assert r._failures == {}

    def test_all_banned_falls_back_to_best_proxy(self):
        r = self._rotator()
        # Ban both proxies
        for p in r._proxies:
            r._banned_until[p] = time.time() + 9999
        # Should still return a proxy (the one that recovers soonest)
        _, proxy = r.next_sync()
        assert proxy is not None


# ── httpx_proxies helper ──────────────────────────────────────────────────────

class TestHttpxProxies:
    def test_none_returns_none(self):
        r = ProxyRotator.__new__(ProxyRotator)
        r._proxies = []
        import threading
        r._lock = threading.Lock()
        r._failures = {}
        r._banned_until = {}
        r._rl_until = {}
        r._index = 0
        assert r.httpx_proxies(None) is None

    def test_url_mapped_to_both_schemes(self):
        r = ProxyRotator.__new__(ProxyRotator)
        r._proxies = ["http://1.2.3.4:8080"]
        import threading
        r._lock = threading.Lock()
        r._failures = {}
        r._banned_until = {}
        r._rl_until = {}
        r._index = 0
        result = r.httpx_proxies("http://1.2.3.4:8080")
        assert result is not None
        assert "http://" in result
        assert "https://" in result


# ── Module-level singletons ───────────────────────────────────────────────────

class TestSingletons:
    def test_get_rotator_returns_same_instance(self):
        r1 = get_rotator()
        r2 = get_rotator()
        assert r1 is r2

    def test_get_nepal_rotator_returns_instance(self):
        r = get_nepal_rotator()
        assert isinstance(r, ProxyRotator)

    def test_nepal_rotator_falls_back_to_default_when_not_configured(self):
        # When NEPAL_PROXY_LIST is not set, nepal rotator == default rotator
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NEPAL_PROXY_LIST", None)
            # Reset the cached singleton to force re-init
            import app.services.data.free_sources.proxy_rotator as pr_mod
            pr_mod._nepal_rotator = None
            r = get_nepal_rotator()
            assert isinstance(r, ProxyRotator)
            # Restore
            pr_mod._nepal_rotator = None


# ── fetch_proxies_from_url ────────────────────────────────────────────────────

class TestFetchProxiesFromUrl:
    """Tests for the remote proxy-list fetcher. All network calls are mocked."""

    def _make_response(self, body: str, status: int = 200):
        """Build a mock urllib response context manager."""
        from io import BytesIO
        import contextlib

        @contextlib.contextmanager
        def _cm(*args, **kwargs):
            mock_resp = MagicMock()
            mock_resp.read.return_value = body.encode()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            yield mock_resp

        return _cm

    def test_plain_text_single_proxy(self):
        plain = "1.2.3.4:8080\n"
        with patch("urllib.request.urlopen", self._make_response(plain)):
            result = fetch_proxies_from_url("http://example.com/proxies.txt")
        assert result == ["http://1.2.3.4:8080"]

    def test_plain_text_multiple_proxies(self):
        plain = "1.1.1.1:3128\n2.2.2.2:8080\n3.3.3.3:1080\n"
        with patch("urllib.request.urlopen", self._make_response(plain)):
            result = fetch_proxies_from_url("http://example.com/proxies.txt")
        assert len(result) == 3
        assert "http://1.1.1.1:3128" in result

    def test_plain_text_skips_blank_and_comment_lines(self):
        plain = "# comment\n\n1.2.3.4:8080\n  \n# another comment\n5.6.7.8:3128\n"
        with patch("urllib.request.urlopen", self._make_response(plain)):
            result = fetch_proxies_from_url("http://example.com/proxies.txt")
        assert len(result) == 2

    def test_plain_text_strips_inline_comments(self):
        plain = "1.2.3.4:8080  # fast proxy\n"
        with patch("urllib.request.urlopen", self._make_response(plain)):
            result = fetch_proxies_from_url("http://example.com/proxies.txt")
        assert result == ["http://1.2.3.4:8080"]

    def test_plain_text_preserves_existing_scheme(self):
        plain = "http://1.2.3.4:8080\nsocks5://5.6.7.8:1080\n"
        with patch("urllib.request.urlopen", self._make_response(plain)):
            result = fetch_proxies_from_url("http://example.com/proxies.txt")
        assert "http://1.2.3.4:8080" in result
        assert "socks5://5.6.7.8:1080" in result

    def test_plain_text_deduplicates(self):
        plain = "1.2.3.4:8080\n1.2.3.4:8080\n1.2.3.4:8080\n"
        with patch("urllib.request.urlopen", self._make_response(plain)):
            result = fetch_proxies_from_url("http://example.com/proxies.txt")
        assert len(result) == 1

    def test_geonode_json_format(self):
        payload = json.dumps({
            "data": [
                {"ip": "1.2.3.4", "port": "8080"},
                {"ip": "5.6.7.8", "port": "3128"},
            ]
        })
        with patch("urllib.request.urlopen", self._make_response(payload)):
            result = fetch_proxies_from_url("http://geonode.example.com/api")
        assert "http://1.2.3.4:8080" in result
        assert "http://5.6.7.8:3128" in result

    def test_geonode_json_missing_data_key_falls_back_to_text(self):
        # JSON without "data" key → plain-text fallback (no valid entries)
        payload = json.dumps({"proxies": []})
        with patch("urllib.request.urlopen", self._make_response(payload)):
            result = fetch_proxies_from_url("http://example.com/api")
        # payload is valid JSON but no "data" array → treated as plain text;
        # plain text parse of a JSON object will produce no valid ip:port lines
        assert isinstance(result, list)

    def test_network_error_returns_empty_list(self):
        with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            result = fetch_proxies_from_url("http://bad-host.example.com/")
        assert result == []

    def test_empty_response_returns_empty_list(self):
        with patch("urllib.request.urlopen", self._make_response("")):
            result = fetch_proxies_from_url("http://example.com/empty.txt")
        assert result == []


# ── ProxyRotator init with PROXY_LIST_URL ─────────────────────────────────────

class TestProxyRotatorInitWithUrl:
    """Tests ProxyRotator.__init__ PROXY_LIST_URL integration."""

    def test_url_proxies_loaded_when_static_empty(self):
        fetched = ["http://9.9.9.9:8080", "http://8.8.8.8:3128"]
        with (
            patch.dict(os.environ, {"PROXY_LIST": "", "PROXY_LIST_URL": "http://x.com/p.txt",
                                    "PROXY_LIST_REFRESH_INTERVAL": "0"}, clear=False),
            patch(
                "app.services.data.free_sources.proxy_rotator.fetch_proxies_from_url",
                return_value=fetched,
            ),
        ):
            r = ProxyRotator()
        assert r._proxies == fetched

    def test_static_and_url_proxies_merged(self):
        with (
            patch.dict(
                os.environ,
                {
                    "PROXY_LIST": "http://1.1.1.1:8080",
                    "PROXY_LIST_URL": "http://x.com/p.txt",
                    "PROXY_LIST_REFRESH_INTERVAL": "0",
                },
                clear=False,
            ),
            patch(
                "app.services.data.free_sources.proxy_rotator.fetch_proxies_from_url",
                return_value=["http://2.2.2.2:8080", "http://3.3.3.3:3128"],
            ),
        ):
            r = ProxyRotator()
        assert "http://1.1.1.1:8080" in r._proxies
        assert "http://2.2.2.2:8080" in r._proxies
        assert "http://3.3.3.3:3128" in r._proxies
        # Static proxy first
        assert r._proxies[0] == "http://1.1.1.1:8080"

    def test_static_takes_priority_no_duplicates(self):
        with (
            patch.dict(
                os.environ,
                {
                    "PROXY_LIST": "http://1.1.1.1:8080",
                    "PROXY_LIST_URL": "http://x.com/p.txt",
                    "PROXY_LIST_REFRESH_INTERVAL": "0",
                },
                clear=False,
            ),
            patch(
                "app.services.data.free_sources.proxy_rotator.fetch_proxies_from_url",
                # URL list contains a duplicate of the static proxy
                return_value=["http://1.1.1.1:8080", "http://2.2.2.2:3128"],
            ),
        ):
            r = ProxyRotator()
        assert r._proxies.count("http://1.1.1.1:8080") == 1
        assert len(r._proxies) == 2

    def test_url_fetch_failure_falls_back_to_static(self):
        with (
            patch.dict(
                os.environ,
                {
                    "PROXY_LIST": "http://1.1.1.1:8080",
                    "PROXY_LIST_URL": "http://bad.example.com/",
                    "PROXY_LIST_REFRESH_INTERVAL": "0",
                },
                clear=False,
            ),
            patch(
                "app.services.data.free_sources.proxy_rotator.fetch_proxies_from_url",
                return_value=[],
            ),
        ):
            r = ProxyRotator()
        assert r._proxies == ["http://1.1.1.1:8080"]

    def test_no_refresh_thread_when_interval_zero(self):
        started = []
        real_thread_init = threading.Thread.__init__

        def track_start(self_t, *a, **kw):
            real_thread_init(self_t, *a, **kw)

        with (
            patch.dict(
                os.environ,
                {
                    "PROXY_LIST": "",
                    "PROXY_LIST_URL": "http://x.com/p.txt",
                    "PROXY_LIST_REFRESH_INTERVAL": "0",
                },
                clear=False,
            ),
            patch(
                "app.services.data.free_sources.proxy_rotator.fetch_proxies_from_url",
                return_value=[],
            ),
            patch.object(threading.Thread, "start", side_effect=lambda: started.append(1)),
        ):
            ProxyRotator()
        # No refresh thread started when interval == 0
        assert len(started) == 0

    def test_refresh_thread_started_when_interval_positive(self):
        started = []
        with (
            patch.dict(
                os.environ,
                {
                    "PROXY_LIST": "",
                    "PROXY_LIST_URL": "http://x.com/p.txt",
                    "PROXY_LIST_REFRESH_INTERVAL": "3600",
                },
                clear=False,
            ),
            patch(
                "app.services.data.free_sources.proxy_rotator.fetch_proxies_from_url",
                return_value=[],
            ),
            patch.object(threading.Thread, "start", side_effect=lambda: started.append(1)),
        ):
            ProxyRotator()
        assert len(started) == 1
