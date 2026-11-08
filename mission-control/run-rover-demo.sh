#!/usr/bin/env bash
set -euo pipefail
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export BULLSEYE_MODE=hardware
export BULLSEYE_BENCH_ENABLED=1
export BULLSEYE_PORT="${BULLSEYE_PORT:-8001}"
printf 'Open http://localhost:%s/bench — the rover connects and reconnects automatically (BULLSEYE_BENCH_AUTO=0 to connect by hand).\n' "$BULLSEYE_PORT"
exec "$task_dir/run.sh" "$@"
