#!/usr/bin/env bash
set -euo pipefail

TASK_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' 'Thiếu python3 trong WSL. Cài Python 3 trước khi quản lý AWS dev.' >&2
  exit 1
fi

if [[ "$#" -eq 0 ]]; then
  cat >&2 <<'USAGE'
Chọn phạm vi tắt AWS dev:
  bash scripts/aws-dev-down.sh stop
      Dừng 4 ECS service; database, EC2 và storage vẫn có thể tính phí.
  bash scripts/aws-dev-down.sh runtime
      Xóa app, VPC và database dev; mất dữ liệu, giữ foundation và AMI.
  bash scripts/aws-dev-down.sh all
      Xóa runtime, foundation, AMI/snapshots, SSM và state bucket của dự án.

Thêm --yes để chủ động bật Terraform auto-approve cho lần chạy này.
Các file source, .secrets, IAM login user và AWS Budget được giữ lại.
USAGE
  exit 2
fi

exec python3 "${TASK_SCRIPT_DIR}/aws_dev_power.py" down "$@"
