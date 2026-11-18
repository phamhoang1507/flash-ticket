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

TASK_CONNECTION_FIELDS="$(
  terraform \
    "-chdir=${TASK_PROJECT_ROOT}/infra/environments/dev" \
    output -json mongodb \
    | python3 -c '
import json
import sys

document = json.load(sys.stdin)

if not isinstance(document, dict):
    raise SystemExit("MongoDB output is missing. Deploy MongoDB first.")

keys = (
    "instance_id",
    "availability_zone",
    "instance_connect_endpoint_id",
)

values = [document.get(key) for key in keys]

if any(not isinstance(value, str) or not value for value in values):
    raise SystemExit("MongoDB connection information is incomplete.")

print(" ".join(values))
'
)"

read -r \
  TASK_INSTANCE_ID \
  TASK_AVAILABILITY_ZONE \
  TASK_ENDPOINT_ID \
  <<< "$TASK_CONNECTION_FIELDS"

TASK_KEY_DIRECTORY="${TASK_PROJECT_ROOT}/.secrets"
TASK_KEY_PATH="${TASK_KEY_DIRECTORY}/mongodb-dev-ssh"

mkdir -p "$TASK_KEY_DIRECTORY"
chmod 700 "$TASK_KEY_DIRECTORY"

if [[ ! -f "$TASK_KEY_PATH" ]]; then
  ssh-keygen \
    -q \
    -t ed25519 \
    -N "" \
    -C "flash-ticket-mongodb-dev" \
    -f "$TASK_KEY_PATH"
fi

chmod 600 "$TASK_KEY_PATH"

ssh-keygen -y \
  -f "$TASK_KEY_PATH" \
  > "${TASK_KEY_PATH}.pub"

TASK_KEY_SENT="$(
  aws ec2-instance-connect send-ssh-public-key \
    --instance-id "$TASK_INSTANCE_ID" \
    --instance-os-user ubuntu \
    --availability-zone "$TASK_AVAILABILITY_ZONE" \
    --ssh-public-key "file://${TASK_KEY_PATH}.pub" \
    --profile flash-ticket-terraform \
    --region us-east-1 \
    --query Success \
    --output text
)"

if [[ "$TASK_KEY_SENT" != "True" && "$TASK_KEY_SENT" != "true" ]]; then
  printf '%s\n' "EC2 Instance Connect did not accept the SSH public key." >&2
  exit 1
fi

TASK_PROXY_COMMAND="aws ec2-instance-connect open-tunnel --instance-id %h --instance-connect-endpoint-id ${TASK_ENDPOINT_ID} --remote-port 22 --max-tunnel-duration 900 --profile flash-ticket-terraform --region us-east-1"

exec ssh \
  -i "$TASK_KEY_PATH" \
  -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=accept-new \
  -o "UserKnownHostsFile=${TASK_KEY_DIRECTORY}/mongodb-known-hosts" \
  -o "HostKeyAlias=${TASK_INSTANCE_ID}" \
  -o "ProxyCommand=${TASK_PROXY_COMMAND}" \
  "ubuntu@${TASK_INSTANCE_ID}" \
  "$@"
