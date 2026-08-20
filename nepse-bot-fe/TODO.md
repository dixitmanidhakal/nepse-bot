# Universal Data Layer — TODO

## Phase 1 — Abstraction + Free provider

- [x] `src/types/canonical.ts` — CanonicalStock/Bar/Depth/Trade/Floorsheet/Sector/Freshness
- [x] `src/data/providers/types.ts` — DataProvider interface
- [x] `src/data/providers/freeProvider.ts` — maps /api/v1/free/\* → canonical
- [x] `src/data/index.ts` — provider selector (env: VITE_DATA_PROVIDER)
- [x] `src/analytics/indicators.ts` — RSI, MACD, SMA/EMA, BB on CanonicalBar[]
- [x] `src/analytics/patterns.ts` — basic patterns on CanonicalBar[]
- [x] `src/analytics/screeners.ts` — momentum, oversold, volume on CanonicalStock[]
- [x] `src/analytics/recommendations.ts` — scoring + top-n
- [x] build passes (tsc + vite build — 2504 modules, 825 KB)

## Phase 2 — Rewire pages

- [x] Dashboard.tsx → uses `stocksApi` which cascades to `/api/v1/free/market/live`
- [x] StockAnalysis.tsx → `indicatorsApi` uses `getOhlcv` from canonical data layer
- [x] Recommendations.tsx → cascades free → legacy → client-side; FreshnessBanner ✓
- [x] MarketDepth.tsx → `getDepth()` via freeProvider (partial depth from yonepse)
- [x] Floorsheet.tsx → `floorsheetApi` hits `/free/floorsheet/latest`; FreshnessBanner ✓
- [x] SectorAnalysis.tsx → `sectorsApi` uses `/free/indices/sectors/{sector}/stocks`
- [x] StockScreener.tsx → `stocksApi.screen()` cascades to live market snapshot
- [x] Calendar.tsx — graceful "upstream unavailable" fallback
- [x] DataManager.tsx — uses `/api/v1/free/health` freshness data
- [x] `src/api/health.ts` → prefers `/api/v1/free/health`
- [x] `src/api/stocks.ts` → all screeners computed from `/api/v1/free/market/live`
- [x] `src/api/recommendations.ts` → cascade free → legacy → client-side
- [x] `src/api/sectors.ts` → `/free/indices/sectors/{sector}/stocks` drill-down
- [x] `src/api/floorsheet.ts` → `/free/floorsheet/latest` with date in response
- [x] `src/components/shared/FreshnessBanner.tsx` → shows data age per source

## Phase 3 — Deploy & verify

- [x] `npm run build` passes
- [ ] commit + push origin/main
- [ ] Vercel redeploy (auto via git push)
- [ ] Verify every page loads live data in incognito

## Phase 4 (future) — Additional providers

- [ ] `providers/legacyProvider.ts` (for eventual Postgres-backed prod)
- [ ] `providers/mockProvider.ts` (for tests)
