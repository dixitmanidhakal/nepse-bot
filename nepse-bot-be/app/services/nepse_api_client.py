"""
Nepal Stock Exchange (NEPSE) Web Scraper

Scrapes data directly from https://www.nepalstock.com.np/api/nots/...
— the same JSON REST API endpoints used by the nepalstock.com Angular frontend.

No paid API key required. Free web scraping approach.

Design principles:
  - Rate limiting  : 1 second minimum delay between requests (configurable)
                     to avoid hammering the server / getting banned.
  - Caching        : payload ID and securities list are cached per session
                     to minimise total requests.
  - SSL            : verification disabled (nepalstock.com cert issues on macOS).
  - Geo-restriction: The API is restricted to Nepal. Running from outside Nepal
                     will return 401 Unauthorized.

Endpoints used:
  GET  /nots/nepse-data/market-open          → market status + auth payload ID
  GET  /nots/securityDailyTradeStat/58       → today's live stock prices
  GET  /nots/index/history/{id}?size=N       → index / sector history
  GET  /nots/market/graphdata/{scripID}      → OHLCV chart data per stock
  POST /nots/nepse-data/floorsheet           → floorsheet (paginated, needs payload)
"""

import time
import logging
import threading
import warnings
from typing import Dict, List, Optional, Any
from datetime import datetime

import requests
import urllib3

from app.services.base_api_client import BaseAPIClient
from app.config import settings

# Suppress SSL warnings (nepalstock.com has cert issues on macOS)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

logger = logging.getLogger(__name__)

# Official nepalstock.com API base (same as the Angular frontend uses)
NEPALSTOCK_BASE = "https://www.nepalstock.com.np/api"

# All sector/index definitions from nepalstock.com
SECTOR_INDICES = [
    {"id": 51, "code": "BANKSUBIND",  "name": "Banking SubIndex"},
    {"id": 52, "code": "HOTELIND",    "name": "Hotels And Tourism Index"},
    {"id": 53, "code": "OTHERSIND",   "name": "Others Index"},
    {"id": 54, "code": "HYDPOWIND",   "name": "HydroPower Index"},
    {"id": 55, "code": "DEVBANKIND",  "name": "Development Bank Index"},
    {"id": 56, "code": "MANPROCIND",  "name": "Manufacturing And Processing"},
    {"id": 57, "code": "SENSIND",     "name": "Sensitive Index"},
    {"id": 58, "code": "NEPSE",       "name": "NEPSE Index"},
    {"id": 59, "code": "NONLIFIND",   "name": "Non Life Insurance"},
    {"id": 60, "code": "FININD",      "name": "Finance Index"},
    {"id": 61, "code": "TRDIND",      "name": "Trading Index"},
    {"id": 62, "code": "FLOATIND",    "name": "Float Index"},
    {"id": 63, "code": "SENSFLTIND",  "name": "Sensitive Float Index"},
    {"id": 64, "code": "MICRFININD",  "name": "Microfinance Index"},
    {"id": 65, "code": "LIFINSIND",   "name": "Life Insurance"},
    {"id": 66, "code": "MUTUALIND",   "name": "Mutual Fund"},
    {"id": 67, "code": "INVIDX",      "name": "Investment Index"},
]


class NepalStockScraper(BaseAPIClient):
    """
    Web scraper for www.nepalstock.com.np.

    Calls the same JSON REST API endpoints that the nepalstock.com
    Angular frontend uses internally. No paid API key required.

    Rate limiting : 1 second between requests (configurable via request_delay).
    Caching       : payload ID and securities list are cached per session.

    Note: The API is geo-restricted to Nepal. Running from outside Nepal
    will return 401 Unauthorized.

    Usage:
        scraper = NepalStockScraper()
        indices = scraper.fetch_market_indices()
        stocks  = scraper.fetch_stock_list()
    """

    # ------------------------------------------------------------------ #
    # Authentication payload ID mapping                                   #
    # Maps the 'id' field from /market-open response → POST payload value #
    # Source: https://github.com/Samrid-Pandit/nepse-api                  #
    # ------------------------------------------------------------------ #
    ID_MAPPING: Dict[int, int] = {
        3: 896,  5: 167,  7: 359,  8: 890,  11: 318,
        12: 482, 13: 574, 14: 895, 15: 582, 16: 620,
        17: 345, 18: 326, 19: 515, 23: 564, 24: 662,
        25: 198, 26: 600, 27: 511, 28: 469, 29: 537,
        30: 352, 31: 407, 32: 287, 33: 479, 34: 613,
    }

    # Browser-like headers to mimic the Angular frontend
    # Updated to Chrome 125 (2025) — older UA strings are more likely to be
    # rejected by modern WAF rules on nepalstock.com.
    HEADERS: Dict[str, str] = {
        "accept":            "application/json, text/plain, */*",
        "accept-language":   "en-US,en;q=0.9,ne;q=0.8",
        "cache-control":     "no-cache",
        "pragma":            "no-cache",
        "referer":           "https://www.nepalstock.com.np/",
        "sec-ch-ua":         '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
        "sec-ch-ua-mobile":  "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest":    "empty",
        "sec-fetch-mode":    "cors",
        "sec-fetch-site":    "same-origin",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
    }

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[int] = None,
        retry_attempts: Optional[int] = None,
        request_delay: float = 1.0,
    ):
        """
        Initialise the scraper.

        Args:
            base_url      : Override the default nepalstock.com API base URL.
            timeout       : HTTP request timeout in seconds.
            retry_attempts: Number of retry attempts (inherited, not yet used).
            request_delay : Minimum seconds between consecutive requests.
                            Default 1.0 s — be respectful, avoid bans.
        """
        super().__init__(
            base_url=base_url or NEPALSTOCK_BASE,
            timeout=timeout or settings.nepse_api_timeout,
            retry_attempts=retry_attempts or settings.nepse_api_retry_attempts,
        )
        self._request_delay = request_delay
        self._last_request_time: float = 0.0
        self._lock = threading.Lock()

        # Per-session cache
        self._payload: Optional[int] = None
        self._securities: Optional[List[Dict]] = None

        # Requests session with SSL verification disabled
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update(self.HEADERS)

        # Attempt to prime the session with cookies from the main page.
        # nepalstock.com's Angular frontend may set an XSRF-TOKEN cookie
        # via the initial page load that is needed for API requests.
        self._init_session()

        logger.info(
            "NepalStockScraper initialised "
            f"(base={self.base_url}, timeout={self.timeout}s, "
            f"delay={self._request_delay}s)"
        )

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _init_session(self) -> None:
        """
        Prime the requests session by visiting the nepalstock.com main page
        so that any server-set cookies (XSRF-TOKEN, session id, etc.) are
        captured and automatically re-sent with subsequent API requests.

        This is a best-effort step — if the site is unreachable the scraper
        will still attempt API calls (they may still succeed on Nepal IPs).
        """
        try:
            resp = self.session.get(
                "https://www.nepalstock.com.np/",
                timeout=10,
                allow_redirects=True,
            )
            xsrf = self.session.cookies.get("XSRF-TOKEN")
            if xsrf:
                self.session.headers.update({"X-XSRF-TOKEN": xsrf})
                logger.debug("NepalStockScraper: XSRF-TOKEN acquired from main page")
            else:
                logger.debug(
                    "NepalStockScraper: no XSRF-TOKEN in main-page cookies "
                    f"(status={resp.status_code})"
                )
        except Exception as exc:
            logger.debug(f"NepalStockScraper: session init skipped — {exc}")

    def _throttle(self) -> None:
        """Enforce minimum delay between requests (thread-safe)."""
        with self._lock:
            elapsed = time.time() - self._last_request_time
            if elapsed < self._request_delay:
                time.sleep(self._request_delay - elapsed)
            self._last_request_time = time.time()

    def _check_response(self, response: requests.Response) -> requests.Response:
        """
        Raise ConnectionError if the response signals an auth failure.

        nepalstock.com returns HTTP 401 or an HTML page with 'UNAUTHORIZED'
        when the request is geo-blocked or the session is missing required
        cookies/tokens.

        We check the HTTP status code first (most reliable signal) and only
        fall back to inspecting the body when the status is 2xx but the
        content-type suggests an HTML error page.
        """
        # Hard 401 / 403 — clear auth failure
        if response.status_code in (401, 403):
            raise ConnectionError(
                f"nepalstock.com returned HTTP {response.status_code}. "
                "The API requires a valid browser session cookie (XSRF-TOKEN). "
                "This usually means the session was not properly initialised or "
                "the endpoint is geo-restricted to Nepal."
            )

        # 2xx but HTML body — error page masquerading as success
        ct = response.headers.get("content-type", "")
        if response.ok and "html" in ct.lower():
            body_snippet = response.text[:200]
            if "UNAUTHORIZED" in body_snippet.upper() or "ACCESS DENIED" in body_snippet.upper():
                raise ConnectionError(
                    f"nepalstock.com returned an HTML error page "
                    f"(HTTP {response.status_code}). "
                    "Session cookie may be missing or expired."
                )

        return response

    def _get(self, endpoint: str, params: Optional[dict] = None) -> Any:
        """
        Rate-limited GET request to the nepalstock.com API.

        Args:
            endpoint: Path relative to base_url (e.g. '/nots/securityDailyTradeStat/58')
            params  : Optional query parameters dict.

        Returns:
            Parsed JSON (dict or list).

        Raises:
            ConnectionError: On geo-block, HTTP error, or network failure.
            TimeoutError   : If the request exceeds the configured timeout.
        """
        self._throttle()
        url = f"{self.base_url}{endpoint}"
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
            self._check_response(resp)
            resp.raise_for_status()
            return resp.json()
        except ConnectionError:
            raise
        except requests.exceptions.Timeout:
            raise TimeoutError(f"GET {endpoint} timed out after {self.timeout}s")
        except requests.exceptions.RequestException as exc:
            raise ConnectionError(f"GET {endpoint} failed: {exc}")

    def _post(
        self,
        endpoint: str,
        json_data: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> Any:
        """
        Rate-limited POST request to the nepalstock.com API.

        Args:
            endpoint  : Path relative to base_url.
            json_data : JSON body dict.
            params    : Optional query parameters dict.

        Returns:
            Parsed JSON (dict or list).

        Raises:
            ConnectionError: On geo-block, HTTP error, or network failure.
            TimeoutError   : If the request exceeds the configured timeout.
        """
        self._throttle()
        url = f"{self.base_url}{endpoint}"
        try:
            resp = self.session.post(
                url, json=json_data, params=params, timeout=self.timeout
            )
            self._check_response(resp)
            resp.raise_for_status()
            return resp.json()
        except ConnectionError:
            raise
        except requests.exceptions.Timeout:
            raise TimeoutError(f"POST {endpoint} timed out after {self.timeout}s")
        except requests.exceptions.RequestException as exc:
            raise ConnectionError(f"POST {endpoint} failed: {exc}")

    def _fetch_payload(self) -> int:
        """
        Fetch and cache the authentication payload ID (used in POST requests).

        Calls GET /nots/nepse-data/market-open, reads the 'id' field,
        and maps it through ID_MAPPING to get the payload value required
        by POST endpoints such as /nots/nepse-data/floorsheet.

        Returns:
            int: Payload value (e.g. 198).
        """
        if self._payload is not None:
            return self._payload

        data = self._get("/nots/nepse-data/market-open")
        raw_id = int(data.get("id", 25))
        self._payload = self.ID_MAPPING.get(raw_id, 198)
        logger.debug(f"Payload fetched: raw_id={raw_id} → payload={self._payload}")
        return self._payload

    def _get_securities(self) -> List[Dict]:
        """
        Fetch and cache today's full securities list.

        Calls GET /nots/securityDailyTradeStat/58 once per session.

        Returns:
            List of raw security dicts from nepalstock.com.
        """
        if self._securities is not None:
            return self._securities

        data = self._get("/nots/securityDailyTradeStat/58")
        self._securities = data if isinstance(data, list) else []
        logger.debug(f"Securities cached: {len(self._securities)} records")
        return self._securities

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        """Safely convert a value to float."""
        try:
            return float(value) if value is not None else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        """Safely convert a value to int."""
        try:
            return int(value) if value is not None else default
        except (TypeError, ValueError):
            return default

    # ------------------------------------------------------------------ #
    # BaseAPIClient interface                                              #
    # ------------------------------------------------------------------ #

    def fetch_market_indices(self) -> Dict[str, Any]:
        """
        Fetch NEPSE index and key sector sub-indices.

        - NEPSE main index : GET /nots/index/history/58?size=1
        - Sector sub-indices: GET /nots/index/history/{id}?size=1
          (fetched for the first 8 sectors to limit request count)

        Returns:
            {
                "nepse_index": float,
                "timestamp"  : str (ISO),
                "sub_indices": {
                    "BANKSUBIND": {"name": ..., "index": float, "current_index": float},
                    ...
                }
            }
        """
        try:
            logger.info("Fetching NEPSE market indices from nepalstock.com...")

            # ── NEPSE main index (id=58) ──────────────────────────────── #
            nepse_raw = self._get("/nots/index/history/58", params={"size": 1})
            nepse_index = 0.0

            if isinstance(nepse_raw, dict):
                content = nepse_raw.get("content") or []
                if content:
                    row = content[0]
                    nepse_index = self._safe_float(
                        row.get("currentValue") or row.get("close")
                    )
            elif isinstance(nepse_raw, list) and nepse_raw:
                row = nepse_raw[0]
                nepse_index = self._safe_float(
                    row.get("currentValue") or row.get("close")
                )

            logger.info(f"NEPSE Index: {nepse_index}")

            # ── Sector sub-indices (skip NEPSE itself, limit to 8) ────── #
            sub_indices: Dict[str, Any] = {}
            key_sectors = [s for s in SECTOR_INDICES if s["id"] != 58][:8]

            for sector in key_sectors:
                try:
                    sector_raw = self._get(
                        f"/nots/index/history/{sector['id']}", params={"size": 1}
                    )
                    value = 0.0
                    if isinstance(sector_raw, dict):
                        content = sector_raw.get("content") or []
                        if content:
                            value = self._safe_float(
                                content[0].get("currentValue") or content[0].get("close")
                            )
                    elif isinstance(sector_raw, list) and sector_raw:
                        value = self._safe_float(
                            sector_raw[0].get("currentValue") or sector_raw[0].get("close")
                        )

                    sub_indices[sector["code"]] = {
                        "name":          sector["name"],
                        "index":         value,
                        "current_index": value,
                    }
                    logger.debug(f"  {sector['code']}: {value}")

                except Exception as sec_err:
                    logger.warning(
                        f"Could not fetch sector {sector['code']}: {sec_err}"
                    )

            return {
                "nepse_index": nepse_index,
                "timestamp":   datetime.now().isoformat(),
                "sub_indices": sub_indices,
            }

        except (ConnectionError, TimeoutError) as exc:
            logger.error(f"Error fetching market indices: {exc}")
            return {
                "nepse_index": 0,
                "timestamp":   datetime.now().isoformat(),
                "sub_indices": {},
                "error":       str(exc),
            }
        except Exception as exc:
            logger.error(f"Unexpected error fetching market indices: {exc}")
            return {
                "nepse_index": 0,
                "timestamp":   datetime.now().isoformat(),
                "sub_indices": {},
                "error":       str(exc),
            }

    def fetch_stock_list(self) -> List[Dict[str, Any]]:
        """
        Fetch today's live stock prices for all listed securities.

        GET /nots/securityDailyTradeStat/58

        Returns:
            List of stock dicts with symbol, name, sector, ltp, ohlcv, etc.
        """
        try:
            logger.info("Fetching stock list from nepalstock.com...")
            raw = self._get_securities()

            stocks: List[Dict[str, Any]] = []
            for row in raw:
                if not isinstance(row, dict):
                    continue
                symbol = str(row.get("symbol") or "").strip()
                if not symbol:
                    continue

                ltp = self._safe_float(
                    row.get("lastTradedPrice") or row.get("closePrice")
                )
                prev_close = self._safe_float(
                    row.get("previousClose") or row.get("lastTradedPrice") or ltp
                )
                change = ltp - prev_close
                change_pct = round((change / prev_close * 100), 2) if prev_close else 0.0

                stocks.append({
                    "symbol":        symbol,
                    "name":          str(
                        row.get("securityName") or
                        row.get("companyName") or symbol
                    ).strip(),
                    "sector":        str(row.get("sectorName") or "").strip(),
                    "ltp":           ltp,
                    "open_price":    self._safe_float(row.get("openPrice")),
                    "high_price":    self._safe_float(row.get("highPrice")),
                    "low_price":     self._safe_float(row.get("lowPrice")),
                    "close_price":   self._safe_float(row.get("closePrice") or ltp),
                    "previous_close": prev_close,
                    "volume":        self._safe_int(
                        row.get("totalTradeQuantity") or
                        row.get("totalTradedQuantity")
                    ),
                    "turnover":      self._safe_float(
                        row.get("totalTurnover") or
                        row.get("totalTradedValue")
                    ),
                    "change":        change,
                    "change_percent": change_pct,
                    "total_trades":  self._safe_int(
                        row.get("totalTrades") or
                        row.get("noOfTransactions")
                    ),
                    "raw_data": row,
                })

            logger.info(f"Fetched {len(stocks)} stocks from nepalstock.com")
            return stocks

        except (ConnectionError, TimeoutError) as exc:
            logger.error(f"Error fetching stock list: {exc}")
            return []
        except Exception as exc:
            logger.error(f"Unexpected error fetching stock list: {exc}")
            return []

    def fetch_stock_ohlcv(
        self,
        symbol: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch OHLCV chart data for a stock.

        GET /nots/market/graphdata/{scripID}

        The scripID is resolved from the cached securities list.

        Args:
            symbol    : Stock symbol (e.g. "NABIL").
            start_date: Filter records on or after this date.
            end_date  : Filter records on or before this date.

        Returns:
            List of OHLCV dicts: {date, open, high, low, close, volume}.
        """
        try:
            logger.info(f"Fetching OHLCV for {symbol}...")

            # Resolve security ID from cached list
            securities = self._get_securities()
            scrip_id: Optional[int] = None
            for sec in securities:
                if str(sec.get("symbol") or "").upper() == symbol.upper():
                    scrip_id = sec.get("securityId") or sec.get("id")
                    break

            if not scrip_id:
                logger.warning(f"Security ID not found for symbol '{symbol}'")
                return []

            data = self._get(f"/nots/market/graphdata/{scrip_id}")

            if not isinstance(data, list):
                return []

            ohlcv: List[Dict[str, Any]] = []
            for row in data:
                if not isinstance(row, dict):
                    continue
                date_str = str(
                    row.get("businessDate") or row.get("date") or ""
                )
                ohlcv.append({
                    "date":   date_str,
                    "open":   self._safe_float(row.get("openPrice")),
                    "high":   self._safe_float(row.get("highPrice")),
                    "low":    self._safe_float(row.get("lowPrice")),
                    "close":  self._safe_float(row.get("closePrice")),
                    "volume": self._safe_int(
                        row.get("totalTradeQuantity") or row.get("volume")
                    ),
                    "raw_data": row,
                })

            # Apply optional date filters
            if start_date:
                cutoff = start_date.strftime("%Y-%m-%d")
                ohlcv = [r for r in ohlcv if r["date"] >= cutoff]
            if end_date:
                cutoff = end_date.strftime("%Y-%m-%d")
                ohlcv = [r for r in ohlcv if r["date"] <= cutoff]

            logger.info(f"Fetched {len(ohlcv)} OHLCV records for {symbol}")
            return ohlcv

        except (ConnectionError, TimeoutError) as exc:
            logger.error(f"Error fetching OHLCV for {symbol}: {exc}")
            return []
        except Exception as exc:
            logger.error(f"Unexpected error fetching OHLCV for {symbol}: {exc}")
            return []

    def fetch_market_depth(self, symbol: str) -> Dict[str, Any]:
        """
        Market depth (order book) is not available via the nepalstock.com
        public API. Returns an empty structure.

        Args:
            symbol: Stock symbol.

        Returns:
            Dict with empty buy_orders and sell_orders.
        """
        logger.warning(
            f"Market depth not available via nepalstock.com scraper for {symbol}"
        )
        return {
            "symbol":      symbol,
            "timestamp":   datetime.now().isoformat(),
            "buy_orders":  [],
            "sell_orders": [],
            "note": (
                "Market depth is not available via the nepalstock.com "
                "public API scraper."
            ),
        }

    def fetch_floorsheet(
        self,
        symbol: Optional[str] = None,
        date: Optional[datetime] = None,
        max_pages: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Fetch floorsheet (individual trade records).

        POST /nots/nepse-data/floorsheet?page=X&size=500&sort=contractId,desc

        Paginated. max_pages caps the total number of POST requests to avoid
        hammering the server. Default max_pages=5 → up to 2 500 records.

        Args:
            symbol   : Filter by stock symbol (optional).
            date     : Not used by this API (always returns today's data).
            max_pages: Maximum pages to fetch (each page = 500 records).

        Returns:
            List of trade dicts: {symbol, contract_id, buyer_broker,
                                  seller_broker, quantity, price, amount,
                                  trade_time}.
        """
        try:
            logger.info(
                f"Fetching floorsheet "
                f"(symbol={symbol}, max_pages={max_pages})..."
            )

            payload_id = self._fetch_payload()
            post_body = {"id": payload_id}
            trades: List[Dict[str, Any]] = []

            for page in range(max_pages):
                params: Dict[str, Any] = {
                    "page": page,
                    "size": 500,
                    "sort": "contractId,desc",
                }
                if symbol:
                    params["symbol"] = symbol.upper()

                try:
                    data = self._post(
                        "/nots/nepse-data/floorsheet",
                        json_data=post_body,
                        params=params,
                    )
                except Exception as page_err:
                    logger.warning(f"Floorsheet page {page} failed: {page_err}")
                    break

                if not data:
                    break

                # Response: {"floorsheets": {"content": [...], "last": bool}}
                floorsheets = data.get("floorsheets") if isinstance(data, dict) else data
                if isinstance(floorsheets, dict):
                    content = floorsheets.get("content", [])
                    is_last = floorsheets.get("last", True)
                elif isinstance(floorsheets, list):
                    content = floorsheets
                    is_last = True
                else:
                    break

                for row in content:
                    if not isinstance(row, dict):
                        continue
                    row_symbol = str(
                        row.get("stockSymbol") or row.get("symbol") or ""
                    ).strip()
                    trades.append({
                        "symbol":        row_symbol,
                        "contract_id":   str(row.get("contractId") or row.get("id") or ""),
                        "buyer_broker":  self._safe_int(
                            row.get("buyerBrokerNo") or row.get("buyerBroker")
                        ),
                        "seller_broker": self._safe_int(
                            row.get("sellerBrokerNo") or row.get("sellerBroker")
                        ),
                        "quantity":      self._safe_int(
                            row.get("contractQuantity") or row.get("quantity")
                        ),
                        "price":         self._safe_float(
                            row.get("contractRate") or row.get("rate")
                        ),
                        "amount":        self._safe_float(
                            row.get("contractAmount") or row.get("amount")
                        ),
                        "trade_time":    str(row.get("tradeTime") or row.get("time") or ""),
                        "raw_data":      row,
                    })

                logger.debug(
                    f"  Floorsheet page {page}: "
                    f"{len(content)} records (last={is_last})"
                )

                if is_last:
                    break

            logger.info(f"Fetched {len(trades)} floorsheet records")
            return trades

        except (ConnectionError, TimeoutError) as exc:
            logger.error(f"Error fetching floorsheet: {exc}")
            return []
        except Exception as exc:
            logger.error(f"Unexpected error fetching floorsheet: {exc}")
            return []

    def fetch_stock_fundamentals(self, symbol: str) -> Dict[str, Any]:
        """
        Fetch fundamental data for a stock.

        Uses the cached securities list (GET /nots/securityDailyTradeStat/58)
        to avoid an extra request.

        Args:
            symbol: Stock symbol (e.g. "NABIL").

        Returns:
            Dict with eps, pe_ratio, book_value, pb_ratio, roe, etc.
        """
        try:
            logger.info(f"Fetching fundamentals for {symbol}...")
            securities = self._get_securities()

            row: Optional[Dict] = None
            for sec in securities:
                if str(sec.get("symbol") or "").upper() == symbol.upper():
                    row = sec
                    break

            if not row:
                return {
                    "symbol": symbol,
                    "error":  f"Symbol '{symbol}' not found in securities list",
                }

            return {
                "symbol":         symbol.upper(),
                "eps":            self._safe_float(row.get("eps") or row.get("EPS")),
                "pe_ratio":       self._safe_float(row.get("peRatio") or row.get("pe")),
                "book_value":     self._safe_float(row.get("bookValue")),
                "pb_ratio":       self._safe_float(row.get("pbRatio")),
                "roe":            self._safe_float(row.get("roe")),
                "debt_to_equity": self._safe_float(row.get("debtToEquity")),
                "dividend_yield": self._safe_float(row.get("dividendYield")),
                "market_cap":     self._safe_float(row.get("marketCap")),
                "raw_data":       row,
            }

        except (ConnectionError, TimeoutError) as exc:
            logger.error(f"Error fetching fundamentals for {symbol}: {exc}")
            return {"symbol": symbol, "error": str(exc)}
        except Exception as exc:
            logger.error(f"Unexpected error fetching fundamentals for {symbol}: {exc}")
            return {"symbol": symbol, "error": str(exc)}

    def health_check(self) -> bool:
        """
        Check if the nepalstock.com API is accessible.

        GET /nots/nepse-data/market-open — expects a JSON dict with 'isOpen'.

        Returns:
            True if the API responds with valid JSON, False otherwise.
        """
        try:
            logger.info("Running nepalstock.com health check...")
            data = self._get("/nots/nepse-data/market-open")
            is_healthy = isinstance(data, dict) and "isOpen" in data
            logger.info(
                f"Health check: {'OK' if is_healthy else 'FAILED'} — {data}"
            )
            return is_healthy
        except (ConnectionError, TimeoutError) as exc:
            # 401 is expected when running outside Nepal (geo-restriction).
            # Log at DEBUG so it doesn't alarm; callers decide what to do.
            logger.debug(f"Health check unavailable (geo-restricted?): {exc}")
            return False
        except Exception as exc:
            logger.debug(f"Health check error: {exc}")
            return False

    def close(self) -> None:
        """Close the underlying HTTP session and clear caches."""
        self.session.close()
        self._payload = None
        self._securities = None
        logger.debug("NepalStockScraper session closed")


# --------------------------------------------------------------------------- #
# Factory function                                                             #
# --------------------------------------------------------------------------- #

def create_api_client(provider: str = "nepse") -> NepalStockScraper:
    """
    Factory function — creates a NepalStockScraper instance.

    Kept for backward compatibility with all existing service modules
    (MarketDataService, StockDataService, etc.) that call
    ``create_api_client("nepse")``.

    Args:
        provider: Only "nepse" is supported.

    Returns:
        NepalStockScraper: Configured scraper instance.

    Raises:
        ValueError: If an unsupported provider name is given.
    """
    if provider == "nepse":
        return NepalStockScraper()
    raise ValueError(f"Unsupported API provider: '{provider}'")
