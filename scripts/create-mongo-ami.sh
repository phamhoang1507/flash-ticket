#!/usr/bin/env bash

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

aws_dev() {
  aws "$@" \
    --profile flash-ticket-terraform \
    --region us-east-1 \
    --no-cli-pager
}

TASK_BUILDER_ID="$(
  terraform \
    "-chdir=${TASK_PROJECT_ROOT}/infra/environments/dev" \
    output -json mongo_ami_builder \
    | python3 -c '
import json
import sys

builder = json.load(sys.stdin)

if not builder or not builder.get("instance_id"):
    raise SystemExit("MongoDB AMI builder is not enabled.")

print(builder["instance_id"])
'
)"

printf 'Waiting for builder %s to stop...\n' "$TASK_BUILDER_ID"

aws_dev ec2 wait instance-stopped \
  --instance-ids "$TASK_BUILDER_ID"

aws_dev ec2 get-console-output \
  --instance-id "$TASK_BUILDER_ID" \
  --latest \
  --output json \
  | python3 -c '
import base64
import binascii
import json
import sys

document = json.load(sys.stdin)
output = document.get("Output") or ""

try:
    decoded = base64.b64decode(output, validate=True).decode("utf-8")
except (binascii.Error, UnicodeDecodeError, ValueError):
    decoded = ""

if decoded:
    output = decoded

marker = "FLASH_TICKET_MONGO_AMI_BUILD_OK"

if marker not in output:
    print("\n".join(output.splitlines()[-80:]), file=sys.stderr)
    raise SystemExit(
        "Build success marker is missing. No AMI was created. "
        "Inspect the build output before retrying."
    )

print("MongoDB AMI build success marker verified.")
'

TASK_IMAGE_NAME="flash-ticket-dev-mongo80-${TASK_BUILDER_ID}"

TASK_MONGO_AMI_ID="$(
  aws_dev ec2 describe-images \
    --owners self \
    --filters "Name=name,Values=${TASK_IMAGE_NAME}" \
    --query 'Images[0].ImageId' \
    --output text
)"

if [[ -z "$TASK_MONGO_AMI_ID" || "$TASK_MONGO_AMI_ID" == "None" ]]; then
  TASK_MONGO_AMI_ID="$(
    aws_dev ec2 create-image \
      --instance-id "$TASK_BUILDER_ID" \
      --name "$TASK_IMAGE_NAME" \
      --description "MongoDB 8.0 on Ubuntu 24.04 for Flash-Ticket dev." \
      --tag-specifications \
        'ResourceType=image,Tags=[{Key=Project,Value=flash-ticket},{Key=Environment,Value=dev},{Key=Role,Value=mongodb-ami}]' \
        'ResourceType=snapshot,Tags=[{Key=Project,Value=flash-ticket},{Key=Environment,Value=dev},{Key=Role,Value=mongodb-ami}]' \
      --query ImageId \
      --output text
  )"
fi

printf 'Waiting for AMI %s...\n' "$TASK_MONGO_AMI_ID"

aws_dev ec2 wait image-available \
  --image-ids "$TASK_MONGO_AMI_ID"

mkdir -p "${TASK_PROJECT_ROOT}/output"

aws_dev ec2 describe-images \
  --image-ids "$TASK_MONGO_AMI_ID" \
  --query 'Images[0].{ImageId:ImageId,Name:Name,State:State,SnapshotIds:BlockDeviceMappings[].Ebs.SnapshotId}' \
  --output json \
  > "${TASK_PROJECT_ROOT}/output/mongo-ami.json"

printf 'AMI ready: %s\n' "$TASK_MONGO_AMI_ID"
printf 'Metadata saved to output/mongo-ami.json\n'