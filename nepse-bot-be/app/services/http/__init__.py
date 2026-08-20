"""HTTP layer with anti-ban protections (rate limiting, backoff, proxy rotation)."""
from app.services.http.resilient_client import (
    ResilientHTTPClient,
    CircuitBreakerOpen,
    RateLimitExceeded,
    UpstreamUnavailable,
    get_resilient_client,
)

__all__ = [
    "ResilientHTTPClient",
    "CircuitBreakerOpen",
    "RateLimitExceeded",
    "UpstreamUnavailable",
    "get_resilient_client",
]
