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

python3 - \
  "$TASK_BUILDER_ID" \
  "${TASK_PROJECT_ROOT}/output" <<'PY'
import base64
import binascii
import json
from pathlib import Path
import subprocess
import sys
import time

instance_id = sys.argv[1]
directory = Path(sys.argv[2])
directory.mkdir(parents=True, exist_ok=True)

log_path = directory / "mongo-builder-console.log"
response_path = directory / "mongo-builder-console.json"

log_path.write_text("", encoding="utf-8")

success_marker = "FLASH_TICKET_MONGO_AMI_BUILD_OK"
failure_marker = "FLASH_TICKET_MONGO_AMI_BUILD_FAILED"

last_output = ""

for attempt in range(1, 7):
    result = subprocess.run(
        [
            "aws",
            "ec2",
            "get-console-output",
            "--instance-id",
            instance_id,
            "--latest",
            "--profile",
            "flash-ticket-terraform",
            "--region",
            "us-east-1",
            "--output",
            "json",
            "--no-cli-pager",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or "GetConsoleOutput failed.")

    document = json.loads(result.stdout)

    response_path.write_text(
        json.dumps(document, indent=2) + "\n",
        encoding="utf-8",
    )

    output = document.get("Output") or ""

    if output:
        try:
            compact = "".join(output.split())
            decoded = base64.b64decode(
                compact,
                validate=True,
            ).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            decoded = ""

        if decoded:
            output = decoded

        last_output = output
        log_path.write_text(output, encoding="utf-8")

    print(
        f"Console attempt {attempt}/6: "
        f"{len(output)} characters received.",
        flush=True,
    )

    if failure_marker in output:
        print(
            "\n".join(output.splitlines()[-100:]),
            file=sys.stderr,
        )
        raise SystemExit(
            f"Builder reported a failed installation. Read {log_path}"
        )

    if success_marker in output:
        print("MongoDB build success marker verified.", flush=True)
        break

    if attempt < 6:
        time.sleep(10)
else:
    if last_output:
        print(
            "\n".join(last_output.splitlines()[-100:]),
            file=sys.stderr,
        )

    raise SystemExit(
        "Build marker is still missing. No AMI was created. "
        f"Console log: {log_path}. "
        f"Raw response: {response_path}."
    )
PY

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

aws_dev ec2 describe-images \
  --image-ids "$TASK_MONGO_AMI_ID" \
  --query 'Images[0].{ImageId:ImageId,Name:Name,State:State,SnapshotIds:BlockDeviceMappings[].Ebs.SnapshotId}' \
  --output json \
  > "${TASK_PROJECT_ROOT}/output/mongo-ami.json"

printf 'AMI ready: %s\n' "$TASK_MONGO_AMI_ID"
printf 'Metadata saved to output/mongo-ami.json\n'