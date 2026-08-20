# NEPSE Trading Bot — Quick Start

This guide shows how to run the **entire stack** (FastAPI backend +
React/Vite frontend) on your local machine.

The project is split into two repositories that live side-by-side:

```
nepse-bot/
├── nepse-bot-be/     ← this repo (Python 3.11+, FastAPI, PostgreSQL)
└── nepse-bot-fe/     ← sibling repo (React 18, Vite 5, TypeScript)
```

---

## 1. Prerequisites

Install these once on your machine:

| Tool              | Min version | macOS (brew)                          | Windows                             |
| ----------------- | ----------- | ------------------------------------- | ----------------------------------- |
| Python            | 3.11+       | `brew install python@3.11`            | [python.org](https://python.org)    |
| Node.js           | 18+         | `brew install node`                   | [nodejs.org](https://nodejs.org)    |
| pnpm *(preferred)* | 9+          | `brew install pnpm`                   | `npm i -g pnpm`                     |
| PostgreSQL        | 14+         | `brew install postgresql@14`          | [postgresql.org](https://postgresql.org) |
| Git               | any         | preinstalled                          | [git-scm.com](https://git-scm.com)  |

> `npm` or `yarn` also work for the frontend — the runner auto-detects
> whichever is available.

---

## 2. Clone both repos

```bash
mkdir -p ~/nepse-bot && cd ~/nepse-bot
git clone https://github.com/dixitmanidhakal/nepse-bot-be.git
git clone https://github.com/dixitmanidhakal/nepse-bot-fe.git
```

---

## 3. Start PostgreSQL and create the database

```bash
# macOS
brew services start postgresql@14
createdb nepse_bot

# Linux
sudo service postgresql start
sudo -u postgres createdb nepse_bot

# Windows (PowerShell)
# Start "postgresql-x64-14" service from services.msc, then:
createdb -U postgres nepse_bot
```

Verify:

```bash
psql nepse_bot -c "SELECT 1;"
```

---

## 4. Start the backend (one command)

```bash
cd nepse-bot-be
./run.sh                     # macOS / Linux
# or on Windows:
run.bat
```

What `run.sh` / `run.bat` do automatically:

1. Create `venv/` if missing.
2. `pip install -r requirements.txt` on first run.
3. Copy `.env.example` → `.env` if `.env` is missing.
4. Launch **uvicorn** on `http://localhost:8000`.

Environment variables you can override:

| Var       | Default     | Purpose                                |
| --------- | ----------- | -------------------------------------- |
| `HOST`    | `0.0.0.0`   | Bind address                           |
| `PORT`    | `8000`      | Port                                   |
| `RELOAD`  | `0`         | Set `1` to enable hot-reload (dev)     |

Example:

```bash
RELOAD=1 PORT=9000 ./run.sh
```

Once running, verify:

```bash
curl http://localhost:8000/health
open http://localhost:8000/docs       # Swagger UI
```

---

## 5. Start the frontend (one command)

In a **new terminal**:

```bash
cd nepse-bot-fe
./run.sh                     # macOS / Linux
# or on Windows:
run.bat
```

What `run.sh` / `run.bat` do automatically:

1. Auto-detect package manager (pnpm → npm → yarn).
2. Install dependencies on first run.
3. Launch Vite dev server on `http://localhost:5173`.

Environment variables:

| Var     | Default | Purpose                       |
| ------- | ------- | ----------------------------- |
| `PORT`  | `5173`  | Dev server port               |
| `MODE`  | `dev`   | `dev` \| `build` \| `preview` |

Open the app:

```
http://localhost:5173
```

The UI talks to the backend at `http://localhost:8000` — configured in
`src/api/client.ts`. CORS is pre-allowed for `localhost:5173`.

---

## 6. Manual backend setup (if you prefer explicit steps)

```bash
cd nepse-bot-be

# create + activate virtualenv
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# configure
cp .env.example .env              # edit DATABASE_URL if needed

# (optional) run database migrations
alembic upgrade head

# start the API
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 7. Manual frontend setup

```bash
cd nepse-bot-fe

pnpm install                      # or npm install / yarn
pnpm dev                          # or npm run dev / yarn dev
```

Production build:

```bash
pnpm build                        # output → dist/
pnpm preview                      # serve the built bundle
```

---

## 8. Verify everything works

With both servers running:

```bash
# Backend reachable
curl -s http://localhost:8000/health | jq
curl -s http://localhost:8000/api/v1/recommendations/top?limit=3 | jq

# Frontend reachable
open http://localhost:5173             # landing page
open http://localhost:5173/recommendations
```

Expected: landing page loads, the **Recommendations** page shows a list
of scored NEPSE symbols (CMF2, CBLD88, etc.).

---

## 9. Running tests

```bash
cd nepse-bot-be
source venv/bin/activate
pytest                            # all suites
pytest tests/unit                 # unit only
pytest tests/api                  # API layer
pytest tests/integration          # end-to-end
pytest -k recommendation -v       # keyword filter
```

---

## 10. Common commands

```bash
# backend — tail logs from uvicorn
RELOAD=1 ./run.sh

# database
psql nepse_bot
psql nepse_bot -c "\dt"
pg_dump nepse_bot > backup.sql

# check dependency versions
pip list
pnpm --filter . list --depth 0

# rebuild everything from scratch
rm -rf nepse-bot-be/venv nepse-bot-fe/node_modules
./nepse-bot-be/run.sh
./nepse-bot-fe/run.sh
```

---

## 11. Troubleshooting

### Port already in use

```bash
lsof -i :8000        # find the PID
kill -9 <PID>
# or start on a different port:
PORT=9000 ./run.sh
```

### `psql: FATAL: database "nepse_bot" does not exist`

```bash
createdb nepse_bot
```

### `ModuleNotFoundError` in backend

Your venv is not activated or dependencies aren't installed:

```bash
source venv/bin/activate
pip install -r requirements.txt
```

### Frontend shows "Network Error"

The backend isn't running or is on a non-default port. Confirm:

```bash
curl http://localhost:8000/health
```

If you changed the backend port, update `src/api/client.ts` in the
frontend.

### `brew services` says postgresql is running but `psql` hangs

```bash
brew services restart postgresql@14
```

---

## 12. What's inside the backend

```
nepse-bot-be/
├── app/
│   ├── main.py                 # FastAPI entry point + CORS + lifespan
│   ├── config.py               # Pydantic settings (reads .env)
│   ├── database.py             # SQLAlchemy engine + session
│   ├── api/v1/                 # REST routes (94+ endpoints)
│   ├── components/             # recommendation_engine, screener, etc.
│   ├── services/               # data providers, scrapers, market bus
│   ├── indicators/             # RSI, MACD, EMA, volatility …
│   └── models/                 # SQLAlchemy ORM models
├── tests/
│   ├── unit/                   # pure-function tests
│   ├── api/                    # FastAPI TestClient
│   └── integration/            # end-to-end flows
├── requirements.txt
├── pytest.ini
├── run.sh / run.bat            # one-command launchers
└── .env.example
```

For the full architecture see `ARCHITECTURE.md` and `README.md`.

---

Questions? Open an issue on
[github.com/dixitmanidhakal/nepse-bot-be](https://github.com/dixitmanidhakal/nepse-bot-be).
