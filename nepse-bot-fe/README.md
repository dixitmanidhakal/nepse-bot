# NEPSE Trading Bot — Frontend

React + Vite + TypeScript dashboard that consumes the
[`nepse-bot-be`](https://github.com/dixitmanidhakal/nepse-bot-be)
FastAPI backend and presents a professional trading research UI.

## Features

- **Introduction / Landing page** — explains what the platform does and
  showcases every capability.
- **Dashboard** — system health, live signals, and DB statistics.
- **Stock Recommendations** — deterministic, explainable buy/watch/avoid
  ranking across the full NEPSE universe with per-factor breakdowns.
- **Stock Analysis** — per-symbol indicators, patterns, trading signals.
- **Sector Analysis** — sector strength, rotation, bullish leaders.
- **Stock Screener** — filter by momentum, value, beta, growth, etc.
- **Market Depth** — order-book pressure, walls, liquidity scoring.
- **Floorsheet** — broker tracking & institutional-accumulation analytics.
- **Calendar** — trading days, holidays, festival windows.
- **Quant Lab** — regime detection, position sizing, Kelly fraction.
- **Data Manager** — trigger NEPSE data ingestion jobs.

## Tech stack

| Layer              | Choice                                         |
| ------------------ | ---------------------------------------------- |
| Framework          | React 18                                       |
| Build tool         | Vite 5                                         |
| Language           | TypeScript (strict)                            |
| Styling            | TailwindCSS                                    |
| Data fetching      | TanStack Query (v5) + axios                    |
| Routing            | React Router v6                                |
| Icons              | lucide-react                                   |

## Getting started

### Fast path — one-command runner

```bash
./run.sh                 # macOS / Linux (auto-installs deps)
run.bat                  # Windows
```

The `run.sh` / `run.bat` scripts auto-detect pnpm → npm → yarn, install
dependencies on first run, and start the Vite dev server at
[http://localhost:5173](http://localhost:5173).

| Env var | Default | Purpose                                  |
| ------- | ------- | ---------------------------------------- |
| `PORT`  | `5173`  | Dev server port                          |
| `MODE`  | `dev`   | `dev` \| `build` \| `preview`            |

### Manual

```bash
# Install dependencies
pnpm install       # or npm install / yarn

# Start dev server (talks to http://localhost:8000 by default)
pnpm dev

# Production build
pnpm build
```

The dev server runs on [http://localhost:5173](http://localhost:5173) and
proxies API calls to the backend at
[http://localhost:8000](http://localhost:8000) (configured in
`src/api/client.ts`).

## Project structure

```
src/
├── App.tsx                 # Route map
├── main.tsx                # Entry point
├── api/                    # Typed API clients (one per module)
│   ├── client.ts           # axios instance + error interceptor
│   ├── recommendations.ts  # Recommendation engine client
│   ├── stocks.ts           # Stock screener & beta
│   └── …                   # sectors / indicators / patterns / …
├── components/
│   ├── layout/             # Layout + Sidebar
│   ├── shared/             # StatCard, LoadingSpinner, ErrorMessage
│   └── ui/                 # Re-usable primitives (card, etc.)
├── pages/
│   ├── Intro.tsx           # Landing page
│   ├── Dashboard.tsx       # System dashboard
│   ├── Recommendations.tsx # Top ranked picks
│   └── …                   # analysis / screener / floorsheet / …
└── types/                  # Cross-module TypeScript contracts
```

## Backend contract

Runs against [`nepse-bot-be`](https://github.com/dixitmanidhakal/nepse-bot-be)
which exposes **94+ REST endpoints** under `/api/v1/*`. Interactive docs are
available at `http://localhost:8000/docs` when the backend is running.

## License

MIT © Dixit Mani Dhakal
