#!/usr/bin/env bash
set -euo pipefail
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$task_dir/server"
if [[ ! -x .venv/bin/python ]]; then
  echo 'Set up mission-control/server/.venv first; see docs/MISSION_CONTROL.md.' >&2
  exit 1
fi
if [[ ! -f ../web/dist/index.html ]]; then
  echo 'Build the frontend first: cd mission-control/web && npm ci && npm run build' >&2
  exit 1
fi
if [[ -f .env ]]; then set -- --env-file .env "$@"; fi
exec .venv/bin/python -m uvicorn bullseye.app:app --host 127.0.0.1 --port "${BULLSEYE_PORT:-8000}" "$@"
