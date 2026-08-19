#!/usr/bin/env bash
# Starts both halves of Drape and shuts them down together on Ctrl-C.
set -euo pipefail
cd "$(dirname "$0")"

[ -d backend/.venv ] || { echo "Run: cd backend && python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt"; exit 1; }
[ -d frontend/node_modules ] || { echo "Run: cd frontend && npm install"; exit 1; }
# Credentials are read at generation time, so without this a fresh clone starts
# fine and only fails once someone has set up a whole session.
[ -f backend/.env ] || {
  echo "Missing backend/.env — copy backend/.env.example and fill it in."
  echo "Analysis and browsing work without it; generation will fail."
  echo
}

trap 'kill 0' EXIT INT TERM

(cd backend && ./.venv/bin/uvicorn app.main:app --port 8077 --reload) &
(cd frontend && npm run dev) &

echo
echo "  Drape → http://localhost:5173"
echo
wait
