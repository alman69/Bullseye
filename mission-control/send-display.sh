#!/usr/bin/env bash
set -euo pipefail
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$task_dir/server"
if [[ ! -x .venv/bin/python ]]; then
  echo 'Set up mission-control/server/.venv first; see docs/BLE_DISPLAY.md.' >&2
  exit 1
fi
exec .venv/bin/python -m bullseye.ble_display "$@"
