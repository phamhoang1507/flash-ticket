#!/usr/bin/env bash

set +x
set -euo pipefail

umask 077

TASK_SCRIPT_DIR="$(
  cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
  pwd
)"

TASK_PROJECT_ROOT="$(
  cd -- "$TASK_SCRIPT_DIR/.."
  pwd
)"

export AWS_PAGER=""
export AWS_CLI_AUTO_PROMPT="off"

unset TF_LOG TF_LOG_PATH

read_ssm_secret() {
  aws ssm get-parameter \
    --name "$1" \
    --with-decryption \
    --profile flash-ticket-terraform \
    --region us-east-1 \
    --query Parameter.Value \
    --output text
}

TF_VAR_rds_master_password="$(
  read_ssm_secret \
    /flash-ticket/dev/data/MYSQL_MASTER_PASSWORD
)"

TF_VAR_redis_auth_token="$(
  read_ssm_secret \
    /flash-ticket/dev/data/REDIS_AUTH_TOKEN
)"

if [[ -z "$TF_VAR_rds_master_password" || "$TF_VAR_rds_master_password" == "None" ]]; then
  printf '%s\n' "Failed to load the RDS master password from SSM." >&2
  exit 1
fi

if [[ -z "$TF_VAR_redis_auth_token" || "$TF_VAR_redis_auth_token" == "None" ]]; then
  printf '%s\n' "Failed to load the Redis AUTH token from SSM." >&2
  exit 1
fi

export TF_VAR_rds_master_password
export TF_VAR_redis_auth_token

exec terraform \
  "-chdir=${TASK_PROJECT_ROOT}/infra/environments/dev" \
  "$@"