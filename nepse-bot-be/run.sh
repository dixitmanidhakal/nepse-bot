#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# NEPSE Trading Bot — Backend runner (macOS / Linux)
#
# Starts uvicorn on http://localhost:8000 using the repo-local virtualenv.
# If the venv is missing or dependencies aren't installed, they are created
# and installed automatically.
#
# Usage:
#     ./run.sh                     # run on 0.0.0.0:8000 (default)
#     PORT=9000 ./run.sh           # custom port
#     HOST=127.0.0.1 ./run.sh      # localhost-only bind
#     RELOAD=1 ./run.sh            # enable uvicorn --reload (dev)
# ----------------------------------------------------------------------------
set -euo pipefail

cd "$(dirname "$0")"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
RELOAD_FLAG=""
if [ "${RELOAD:-0}" = "1" ]; then
    RELOAD_FLAG="--reload"
fi

# Bootstrap venv if needed
if [ ! -d "venv" ]; then
    echo "→ Creating Python virtualenv (venv/)..."
    python3 -m venv venv
fi

# Activate
# shellcheck disable=SC1091
source venv/bin/activate

# Install / sync deps if fastapi isn't present
if ! python -c "import fastapi" 2>/dev/null; then
    echo "→ Installing dependencies (requirements.txt)..."
    pip install --upgrade pip >/dev/null
    pip install -r requirements.txt
fi

# Ensure .env exists
if [ ! -f ".env" ] && [ -f ".env.example" ]; then
    echo "→ No .env found — copying .env.example → .env"
    cp .env.example .env
fi

echo "──────────────────────────────────────────────────────────"
echo "  NEPSE Trading Bot — Backend"
echo "  URL    : http://${HOST}:${PORT}"
echo "  Docs   : http://${HOST}:${PORT}/docs"
echo "  Health : http://${HOST}:${PORT}/health"
echo "──────────────────────────────────────────────────────────"

exec python -m uvicorn app.main:app --host "$HOST" --port "$PORT" $RELOAD_FLAG
