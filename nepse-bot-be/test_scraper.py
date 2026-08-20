"""
Critical-path tests for NepalStockScraper (no network required).
Tests: rate limiting, helpers, caching, mock-data methods, error handling.
"""
import time
import sys

from app.services.nepse_api_client import NepalStockScraper, create_api_client

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
errors = []


def check(label, condition):
    status = PASS if condition else FAIL
    print(f"  {label}: [{status}]")
    if not condition:
        errors.append(label)


# ─────────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("TEST 1: Factory + class structure")
print("=" * 60)
scraper = create_api_client("nepse")
check("class is NepalStockScraper", type(scraper).__name__ == "NepalStockScraper")
check("base_url correct", scraper.base_url == "https://www.nepalstock.com.np/api")
check("timeout is int > 0", isinstance(scraper.timeout, int) and scraper.timeout > 0)
check("request_delay == 1.0", scraper._request_delay == 1.0)
check("ID_MAPPING has 25 entries", len(scraper.ID_MAPPING) == 25)
check("payload cache starts None", scraper._payload is None)
check("securities cache starts None", scraper._securities is None)

# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("TEST 2: Rate limiting (_throttle)")
print("=" * 60)
t0 = time.time()
scraper._throttle()
scraper._throttle()
elapsed = time.time() - t0
check(f"two throttle calls >= 1.0s (took {elapsed:.2f}s)", elapsed >= 1.0)

# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("TEST 3: _safe_float helper")
print("=" * 60)
cases = [
    (None,      0.0),
    ("",        0.0),
    ("123.45",  123.45),
    (0,         0.0),
    ("abc",     0.0),
    (99,        99.0),
    (1200.50,   1200.50),
]
for val, expected in cases:
    result = NepalStockScraper._safe_float(val)
    check(f"_safe_float({val!r}) == {expected}", result == expected)

# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("TEST 4: _safe_int helper")
print("=" * 60)
cases_int = [
    (None,  0),
    ("",    0),
    ("42",  42),
    (3.9,   3),
    ("xyz", 0),
    (500,   500),
]
for val, expected in cases_int:
    result = NepalStockScraper._safe_int(val)
    check(f"_safe_int({val!r}) == {expected}", result == expected)

# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("TEST 5: fetch_market_depth (no network — always empty)")
print("=" * 60)
depth = scraper.fetch_market_depth("NABIL")
check("symbol == NABIL",         depth["symbol"] == "NABIL")
check("buy_orders is []",        depth["buy_orders"] == [])
check("sell_orders is []",       depth["sell_orders"] == [])
check("timestamp present",       "timestamp" in depth)
check("note present",            "note" in depth)

# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("TEST 6: Payload caching")
print("=" * 60)
s = create_api_client("nepse")
s._payload = 198          # pre-seed cache
result = s._fetch_payload()
check("cached payload returned without network call", result == 198)
check("cache still set after call", s._payload == 198)

# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("TEST 7: Securities caching")
print("=" * 60)
s2 = create_api_client("nepse")
mock_secs = [{"symbol": "NABIL", "lastTradedPrice": 1200}]
s2._securities = mock_secs
result = s2._get_securities()
check("cached list returned without network call", result is mock_secs)
check("cache still set after call", s2._securities is mock_secs)

# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("TEST 8: fetch_stock_fundamentals (mock cache)")
print("=" * 60)
s3 = create_api_client("nepse")
s3._securities = [
    {
        "symbol": "NABIL", "eps": 45.5, "peRatio": 26.3,
        "bookValue": 250.0, "pbRatio": 4.8, "roe": 18.2,
        "debtToEquity": 1.5, "dividendYield": 5.2,
        "marketCap": 50_000_000_000,
    }
]
r = s3.fetch_stock_fundamentals("NABIL")
check("symbol == NABIL",          r["symbol"] == "NABIL")
check("eps == 45.5",              r["eps"] == 45.5)
check("pe_ratio == 26.3",         r["pe_ratio"] == 26.3)
check("book_value == 250.0",      r["book_value"] == 250.0)
check("no error key",             "error" not in r)

# Case-insensitive symbol lookup
r_lower = s3.fetch_stock_fundamentals("nabil")
check("lowercase symbol lookup",  r_lower["symbol"] == "NABIL")

# Missing symbol
r_miss = s3.fetch_stock_fundamentals("UNKNOWN")
check("missing symbol → error key", "error" in r_miss)

# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("TEST 9: fetch_stock_list (mock cache)")
print("=" * 60)
s4 = create_api_client("nepse")
s4._securities = [
    {
        "symbol": "NABIL", "securityName": "Nabil Bank",
        "sectorName": "Commercial Banks",
        "lastTradedPrice": 1200.0, "previousClose": 1180.0,
        "openPrice": 1185.0, "highPrice": 1210.0, "lowPrice": 1175.0,
        "closePrice": 1200.0, "totalTradeQuantity": 50000,
        "totalTurnover": 60_000_000, "totalTrades": 350,
    },
    # Row with no symbol — should be skipped
    {"securityName": "No Symbol Row"},
]
stocks = s4.fetch_stock_list()
check("only 1 valid stock returned (empty symbol skipped)", len(stocks) == 1)
st = stocks[0]
check("symbol == NABIL",          st["symbol"] == "NABIL")
check("name == Nabil Bank",       st["name"] == "Nabil Bank")
check("sector correct",           st["sector"] == "Commercial Banks")
check("ltp == 1200.0",            st["ltp"] == 1200.0)
check("open_price == 1185.0",     st["open_price"] == 1185.0)
check("high_price == 1210.0",     st["high_price"] == 1210.0)
check("low_price == 1175.0",      st["low_price"] == 1175.0)
check("volume == 50000",          st["volume"] == 50000)
check("change == 20.0",           st["change"] == 20.0)
expected_pct = round(20.0 / 1180.0 * 100, 2)
check(f"change_percent == {expected_pct}", st["change_percent"] == expected_pct)

# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("TEST 10: Live endpoint — health_check (expects 401 outside Nepal)")
print("=" * 60)
s5 = create_api_client("nepse")
result = s5.health_check()
# From outside Nepal this returns False (401), which is correct error handling
check(
    "health_check returns bool (True=Nepal, False=outside Nepal)",
    isinstance(result, bool)
)
print(f"  health_check() returned: {result} "
      f"({'API accessible' if result else 'geo-blocked / outside Nepal — expected'})")

# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("TEST 11: close() clears caches")
print("=" * 60)
s6 = create_api_client("nepse")
s6._payload = 198
s6._securities = [{"symbol": "TEST"}]
s6.close()
check("payload cache cleared after close()", s6._payload is None)
check("securities cache cleared after close()", s6._securities is None)

# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("TEST 12: create_api_client invalid provider")
print("=" * 60)
try:
    create_api_client("invalid_provider")
    check("ValueError raised for bad provider", False)
except ValueError:
    check("ValueError raised for bad provider", True)

# ─────────────────────────────────────────────────────────────────────────────
scraper.close()
print()
print("=" * 60)
if errors:
    print(f"RESULT: {len(errors)} test(s) FAILED: {errors}")
    sys.exit(1)
else:
    print(f"RESULT: All tests PASSED!")
print("=" * 60)
