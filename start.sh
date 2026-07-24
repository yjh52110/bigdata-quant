#!/usr/bin/env bash
# Starts the admin API, the MCP server and the dashboard together.
# Usage: ./start.sh   (Ctrl-C stops everything)
set -euo pipefail

cd "$(dirname "$0")"

: "${QUANT_API_KEY:=}"
: "${API_PORT:=8000}"
: "${MCP_PORT:=8765}"

if [ -z "$QUANT_API_KEY" ]; then
  echo "WARNING: QUANT_API_KEY is unset - the admin API will accept unauthenticated requests."
  echo "         Set it before exposing this beyond localhost:  export QUANT_API_KEY=..."
  echo
fi

pids=()
cleanup() {
  echo
  echo "Shutting down..."
  for pid in "${pids[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Starting admin API on :$API_PORT"
uvicorn backend.api_server:app --host 127.0.0.1 --port "$API_PORT" &
pids+=($!)

echo "Starting MCP server on :$MCP_PORT"
python3 -m backend.mcp_server --transport http --host 0.0.0.0 --port "$MCP_PORT" &
pids+=($!)

if [ -d admin-dashboard/node_modules ]; then
  echo "Starting dashboard on :5173"
  npm run dev --prefix admin-dashboard &
  pids+=($!)
else
  echo "Skipping dashboard (run: npm install --prefix admin-dashboard)"
fi

echo
echo "Dashboard   http://localhost:5173"
echo "Admin API   http://localhost:$API_PORT"
echo "MCP         http://localhost:$MCP_PORT/mcp"
echo
wait
