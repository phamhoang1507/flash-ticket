#!/usr/bin/env bash
set -euo pipefail

TASK_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' 'Thiếu python3 trong WSL. Cài Python 3 trước khi bật AWS dev.' >&2
  exit 1
fi

exec python3 "${TASK_SCRIPT_DIR}/aws_dev_power.py" up "$@"
