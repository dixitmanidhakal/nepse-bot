"""Unit tests for app.services.http.resilient_client.

These tests focus on the pure orchestration logic (rate-limiter, circuit
breaker, exponential backoff, Retry-After parsing). Network is mocked
through ``unittest.mock``.
"""
from __future__ import annotations

import time
from unittest.mock import patch, MagicMock

import pytest
import requests

from app.services.http.resilient_client import (
    CircuitBreakerOpen,
    HostPolicy,
    RateLimitExceeded,
    ResilientHTTPClient,
    UpstreamUnavailable,
)


# ────────────────────────────────────────────────────────────────────────────
# Host policy + breaker
# ────────────────────────────────────────────────────────────────────────────
class TestCircuitBreaker:
    def test_breaker_opens_after_threshold(self):
        client = ResilientHTTPClient()
        policy = client._get_policy("bad.example")
        policy.circuit_threshold = 3
        # simulate 3 consecutive failures
        for _ in range(3):
            client._record_failure("bad.example", policy)
        assert policy.circuit_opened_at is not None

    def test_breaker_raises_while_open(self):
        client = ResilientHTTPClient()
        policy = client._get_policy("bad.example")
        policy.circuit_threshold = 1
        policy.circuit_cooldown = 10.0
        client._record_failure("bad.example", policy)
        with pytest.raises(CircuitBreakerOpen):
            client._check_breaker("bad.example", policy)

    def test_breaker_half_opens_after_cooldown(self):
        client = ResilientHTTPClient()
        policy = client._get_policy("bad.example")
        policy.circuit_threshold = 1
        policy.circuit_cooldown = 0.01  # 10 ms
        client._record_failure("bad.example", policy)
        time.sleep(0.02)
        # should reset and allow a probe
        client._check_breaker("bad.example", policy)
        assert policy.circuit_opened_at is None

    def test_success_resets_failure_count(self):
        client = ResilientHTTPClient()
        policy = client._get_policy("ok.example")
        policy.consecutive_failures = 2
        client._record_success(policy)
        assert policy.consecutive_failures == 0


# ────────────────────────────────────────────────────────────────────────────
# Rate limiter
# ────────────────────────────────────────────────────────────────────────────
class TestRateLimiter:
    def test_second_request_waits(self):
        client = ResilientHTTPClient()
        policy = client._get_policy("rate.example")
        policy.min_interval = 0.1

        t0 = time.monotonic()
        client._wait_rate_limit(policy)
        client._wait_rate_limit(policy)
        elapsed = time.monotonic() - t0
        # second call must have slept at least min_interval
        assert elapsed >= 0.1


# ────────────────────────────────────────────────────────────────────────────
# Retry-After parsing
# ────────────────────────────────────────────────────────────────────────────
class TestRetryAfter:
    def test_parses_numeric_seconds(self):
        resp = MagicMock(headers={"Retry-After": "7"})
        assert ResilientHTTPClient._parse_retry_after(resp) == 7.0

    def test_missing_header_returns_none(self):
        resp = MagicMock(headers={})
        assert ResilientHTTPClient._parse_retry_after(resp) is None

    def test_invalid_value_returns_none(self):
        resp = MagicMock(headers={"Retry-After": "not-a-number"})
        assert ResilientHTTPClient._parse_retry_after(resp) is None


# ────────────────────────────────────────────────────────────────────────────
# Backoff
# ────────────────────────────────────────────────────────────────────────────
class TestBackoff:
    def test_exponential_growth(self):
        # expected ranges including up to 0.5s jitter
        assert 1.0 <= ResilientHTTPClient._backoff(0) <= 1.5
        assert 2.0 <= ResilientHTTPClient._backoff(1) <= 2.5
        assert 4.0 <= ResilientHTTPClient._backoff(2) <= 4.5

    def test_capped_at_30s(self):
        # attempt=10 → 2**10 = 1024, capped to 30
        assert 30.0 <= ResilientHTTPClient._backoff(10) <= 30.5


# ────────────────────────────────────────────────────────────────────────────
# Request dispatch with mocked session
# ────────────────────────────────────────────────────────────────────────────
class TestRequestDispatch:
    def _mock_response(self, status=200, headers=None):
        resp = MagicMock(spec=requests.Response)
        resp.status_code = status
        resp.headers = headers or {}
        return resp

    def test_success_returns_response(self):
        client = ResilientHTTPClient()
        # relax rate limit for test speed
        client.configure_host("api.example", min_interval=0.0)

        with patch.object(client._session, "request") as mock_req:
            mock_req.return_value = self._mock_response(200)
            resp = client.get("https://api.example/foo")
        assert resp.status_code == 200

    def test_retries_on_500(self):
        client = ResilientHTTPClient()
        client.configure_host("flaky.example", min_interval=0.0, max_retries=2)

        with patch.object(client._session, "request") as mock_req, \
             patch("time.sleep"):
            mock_req.side_effect = [
                self._mock_response(500),
                self._mock_response(500),
                self._mock_response(200),
            ]
            resp = client.get("https://flaky.example/x")
        assert resp.status_code == 200
        assert mock_req.call_count == 3

    def test_retries_on_429_with_retry_after(self):
        client = ResilientHTTPClient()
        client.configure_host("slow.example", min_interval=0.0, max_retries=1)

        with patch.object(client._session, "request") as mock_req, \
             patch("time.sleep") as mock_sleep:
            mock_req.side_effect = [
                self._mock_response(429, {"Retry-After": "2"}),
                self._mock_response(200),
            ]
            resp = client.get("https://slow.example/x")
        assert resp.status_code == 200
        # the 2s Retry-After should have been honoured
        assert any(call.args[0] == 2.0 for call in mock_sleep.call_args_list)

    def test_non_429_4xx_returned_without_retry(self):
        client = ResilientHTTPClient()
        client.configure_host("auth.example", min_interval=0.0)

        with patch.object(client._session, "request") as mock_req:
            mock_req.return_value = self._mock_response(403)
            resp = client.get("https://auth.example/x")
        assert resp.status_code == 403
        assert mock_req.call_count == 1  # did NOT retry

    def test_raises_upstream_unavailable_after_retries(self):
        client = ResilientHTTPClient()
        client.configure_host("dead.example", min_interval=0.0, max_retries=2,
                              circuit_threshold=99)

        with patch.object(client._session, "request") as mock_req, \
             patch("time.sleep"):
            mock_req.side_effect = requests.ConnectionError("boom")
            with pytest.raises(UpstreamUnavailable):
                client.get("https://dead.example/x")

    def test_open_circuit_short_circuits(self):
        client = ResilientHTTPClient()
        host = "broken.example"
        client.configure_host(host, min_interval=0.0,
                              circuit_threshold=1, circuit_cooldown=60.0)
        policy = client._get_policy(host)
        client._record_failure(host, policy)
        with pytest.raises(CircuitBreakerOpen):
            client.get(f"https://{host}/x")

    def test_ua_rotated(self):
        client = ResilientHTTPClient()
        client.configure_host("ua.example", min_interval=0.0)

        seen_uas = set()
        with patch.object(client._session, "request") as mock_req:
            mock_req.return_value = self._mock_response(200)
            for _ in range(5):
                client.get("https://ua.example/x")
                ua = mock_req.call_args.kwargs["headers"]["User-Agent"]
                seen_uas.add(ua)
        # with 5 requests from a 5-UA pool, we should see at least 2 different
        assert len(seen_uas) >= 2


# ────────────────────────────────────────────────────────────────────────────
# Configuration overrides
# ────────────────────────────────────────────────────────────────────────────
class TestHostConfiguration:
    def test_configure_host_creates_policy(self):
        client = ResilientHTTPClient()
        client.configure_host("new.example", min_interval=5.0)
        policy = client._get_policy("new.example")
        assert policy.min_interval == 5.0

    def test_default_host_policy_sane(self):
        policy = HostPolicy()
        assert policy.min_interval >= 1.0
        assert policy.max_retries >= 1
        assert policy.circuit_threshold >= 1
