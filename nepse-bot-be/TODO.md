# NEPSE Bot - Web Scraper Implementation TODO

## Task: Replace nepseapi.surajrimal.dev with nepalstock.com web scraper

### Steps

- [x] Analyze current `nepse_api_client.py` (uses unreliable 3rd-party API)
- [x] Analyze `stonk.py` (nepse library) for authentication mechanism
- [x] Test nepalstock.com API endpoints
- [x] Plan implementation

- [x] Step 1: Update `nepse-bot-be/app/services/nepse_api_client.py`

  - [x] Replace `NepseAPIClient` with `NepalStockScraper`
  - [x] Use `https://www.nepalstock.com.np/api` as base URL
  - [x] Implement `fetchPayload()` + `ID_MAPPING` auth mechanism
  - [x] Add rate limiting (1s delay between requests)
  - [x] Cache payload ID and securities list
  - [x] Implement all `BaseAPIClient` methods

- [x] Step 2: Update `nepse-bot-be/app/config.py`

  - [x] Changed `nepse_api_base_url` to `https://www.nepalstock.com.np/api`

- [x] Step 3: Update `nepse-bot-be/requirements.txt`

  - [x] Added `beautifulsoup4==4.12.3`, `lxml==5.3.0`, `urllib3==2.3.0`

- [x] Step 4: Install new dependencies (all installed successfully)

- [x] Step 5: Import + structure test passed (all 8 methods verified OK)
