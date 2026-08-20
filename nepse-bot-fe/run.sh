#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# NEPSE Trading Bot — Frontend runner (macOS / Linux)
#
# Starts the Vite dev server on http://localhost:5173. Automatically detects
# the first available package manager (pnpm > npm > yarn) and installs
# dependencies on first run.
#
# Usage:
#     ./run.sh                 # dev server (default 5173)
#     PORT=3000 ./run.sh       # custom port
#     MODE=build ./run.sh      # production build → dist/
#     MODE=preview ./run.sh    # run vite preview against the built dist
# ----------------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-5173}"
MODE="${MODE:-dev}"

pick_pm() {
    if command -v pnpm >/dev/null 2>&1; then echo "pnpm"; return; fi
    if command -v npm  >/dev/null 2>&1; then echo "npm";  return; fi
    if command -v yarn >/dev/null 2>&1; then echo "yarn"; return; fi
    echo ""
}

PM="$(pick_pm)"
if [ -z "$PM" ]; then
    echo "✗ No package manager found. Install pnpm, npm, or yarn." >&2
    exit 1
fi

# Install deps on first run
if [ ! -d "node_modules" ]; then
    echo "→ Installing dependencies with $PM..."
    case "$PM" in
        pnpm) pnpm install ;;
        npm)  npm install  ;;
        yarn) yarn install ;;
    esac
fi

echo "──────────────────────────────────────────────────────────"
echo "  NEPSE Trading Bot — Frontend ($PM)"
echo "  Mode   : $MODE"
echo "  URL    : http://localhost:${PORT}"
echo "  Backend: http://localhost:8000  (edit src/api/client.ts to change)"
echo "──────────────────────────────────────────────────────────"

case "$MODE" in
    dev)
        exec $PM run dev -- --host 0.0.0.0 --port "$PORT"
        ;;
    build)
        exec $PM run build
        ;;
    preview)
        exec $PM run preview -- --host 0.0.0.0 --port "$PORT"
        ;;
    *)
        echo "✗ Unknown MODE='$MODE'. Use dev | build | preview." >&2
        exit 1
        ;;
esac
