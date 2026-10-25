#!/usr/bin/env bash
# Start the StormSim SF server on 127.0.0.1:8765 with the Gemini key injected (never printed).
# Usage: scripts/serve.sh            (real sim if sim/run.py exists, else mock)
#        STORMSIM_MOCK=1 scripts/serve.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export NUMBA_NUM_THREADS="${NUMBA_NUM_THREADS:-16}"
exec scripts/gem.sh .venv/bin/python -m uvicorn server.app:app --host 127.0.0.1 --port "${PORT:-8765}" --log-level warning
