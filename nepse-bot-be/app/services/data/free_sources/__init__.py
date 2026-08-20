"""
Free Data Sources Package
=========================

Cascading data-provider stack used when nepse-bot runs from environments
that cannot reach nepalstock.com.np directly (e.g. Vercel US, non-Nepal hosts).

Source priorities (cascade order — real-time first):
    1. `merolagani`    – /LatestMarket.aspx HTML table scrape (real-time).
                         Accessible from outside Nepal; uses PROXY_LIST rotation.
    2. `sharesansar`   – HTML scrape of sharesansar.com (real-time, always accessible).
                         Uses PROXY_LIST rotation.
    3. `nepalipaisa`   – API endpoint probing + __NEXT_DATA__ extraction.
                         Works when API endpoints are discovered; full access
                         requires NEPAL_PROXY_LIST (Nepal-exit proxies).
    4. `nepsetrading`  – Next.js _next/data + __NEXT_DATA__ + API probing.
                         Works best with NEPAL_PROXY_LIST configured.
    5. `nepsealpha`    – Direct JSON API scrape (real-time, OHLCV history).
                         Geo-blocked for non-Nepal IPs — requires NEPAL_PROXY_LIST.
    6. `yonepse`       – GitHub-Actions JSON scraper (~15 min lag, last resort).
                         Covers: live market, indices, sector indices, top stocks,
                         supply/demand, market status, brokers, disclosures, notices,
                         dividends, upcoming IPO, all securities. Always reachable.
    7. `samirwagle`    – GitHub-Actions scraper with daily floorsheet CSVs and
                         per-symbol price / dividend / right-share CSVs.
    8. Bundled SQLite (historical) – last-resort offline fallback.

Proxy rotation:
    PROXY_LIST         – General proxy pool used by merolagani, sharesansar.
                         Format: comma-separated proxy URLs.
                         Example: PROXY_LIST=http://1.2.3.4:8080,http://5.6.7.8:3128

    NEPAL_PROXY_LIST   – Nepal-IP proxy pool used by geo-blocked / SPA sources:
                         nepsealpha, nepalipaisa, nepsetrading.
                         These sources are geo-blocked or return no parseable data
                         from non-Nepal IPs. Use proxies with Nepal exit nodes:
                         Example: NEPAL_PROXY_LIST=socks5://user:pass@np.proxy.com:1080
                         Falls back to PROXY_LIST / direct if not set.

    Without PROXY_LIST or NEPAL_PROXY_LIST, all requests go direct. The scrapers
    include random jitter (50–500 ms) between requests regardless.

All upstream responses go through `cache.TTLCache` to keep Vercel cold starts
cheap and stay within free-tier request budgets.
"""

from .cache import TTLCache, get_cache
from .proxy_rotator import ProxyRotator, get_rotator, get_nepal_rotator
from . import yonepse, samirwagle, merolagani, nepsealpha, sharesansar
from . import nepalipaisa, nepsetrading, aggregator

__all__ = [
    "TTLCache", "get_cache",
    "ProxyRotator", "get_rotator", "get_nepal_rotator",
    "yonepse", "samirwagle",
    "merolagani", "nepsealpha", "sharesansar",
    "nepalipaisa", "nepsetrading",
    "aggregator",
]
