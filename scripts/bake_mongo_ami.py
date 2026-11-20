#!/usr/bin/env python3
"""Validate the Terraform builder through SSM, stop it cleanly, and create its AMI."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROFILE = "flash-ticket-terraform"
REGION = "us-east-1"
INSTANCE_ID = re.compile(r"i-(?:[0-9a-f]{8}|[0-9a-f]{17})\Z")
COMMAND_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")

VALIDATE_COMMAND = r"""set -eu
status_file=/var/lib/flash-ticket/mongo-build.status
deadline=$(( $(date +%s) + 900 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  status=$(cat "$status_file" 2>/dev/null || true)
  case "$status" in
    READY)
      printf 'Build status: READY\n'
      test -x /usr/local/sbin/flash-ticket-finalize-ami
      test "$(systemctl is-enabled mongod.service 2>/dev/null || true)" = disabled
      grep -q 'authorization: enabled' /etc/mongod.conf
      grep -q 'bindIp: 127.0.0.1' /etc/mongod.conf
      test -f /etc/tmpfiles.d/flash-ticket-sshd.conf
      /usr/sbin/sshd -t
      dpkg-query --show --showformat='${Package} ${Version}\n' mongodb-org-server ec2-instance-connect openssh-server
      mongod --version
      exit 0
      ;;
    FAILED)
      printf 'Build status: FAILED\n' >&2
      tail -n 100 /var/log/flash-ticket-mongo-build.log >&2
      exit 1
      ;;
  esac
  sleep 5
done
printf 'Timed out waiting for MongoDB build status READY.\n' >&2
tail -n 100 /var/log/flash-ticket-mongo-build.log >&2
exit 1
"""

FINALIZE_COMMAND = r"""set -eu
test "$(cat /var/lib/flash-ticket/mongo-build.status)" = READY
systemd-run --unit=flash-ticket-finalize-ami --on-active=10s /usr/local/sbin/flash-ticket-finalize-ami
printf 'Finalizer scheduled. SSM will disconnect before EC2 reaches stopped state.\n'
"""


class BakeError(RuntimeError):
    pass


class CommandError(BakeError):
    pass


def command(arguments: list[str], *, root: Path) -> Any:
    environment = dict(os.environ)
    environment.pop("TF_LOG", None)
    environment.pop("TF_LOG_PATH", None)
    environment.update(
        AWS_PROFILE=PROFILE,
        AWS_REGION=REGION,
        AWS_DEFAULT_REGION=REGION,
        AWS_PAGER="",
        AWS_CLI_AUTO_PROMPT="off",
        AWS_RETRY_MODE="standard",
        AWS_MAX_ATTEMPTS="8",
        TF_IN_AUTOMATION="1",
    )
    try:
        result = subprocess.run(
            arguments,
            cwd=root,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=180,
        )
    except FileNotFoundError as exc:
        raise CommandError(f"Required command is not installed: {arguments[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CommandError(f"{arguments[0]} timed out; inspect builder state before retrying.") from exc
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise CommandError(f"{arguments[0]} failed: {detail[-6000:]}")
    try:
        return json.loads(result.stdout or "null")
    except json.JSONDecodeError as exc:
        raise CommandError(f"{arguments[0]} returned invalid JSON.") from exc


def aws(root: Path, *arguments: str) -> Any:
    return command(
        [
            "aws",
            "--profile",
            PROFILE,
            "--region",
            REGION,
            "--no-cli-pager",
            *arguments,
            "--output",
            "json",
        ],
        root=root,
    )


def atomic_json(path: Path, document: dict[str, Any]) -> None:
    atomic_text(path, json.dumps(document, indent=2) + "\n")


def atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = stream.name
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def instance(root: Path, instance_id: str) -> dict[str, Any]:
    document = aws(root, "ec2", "describe-instances", "--instance-ids", instance_id)
    instances = [
        item
        for reservation in document["Reservations"]
        for item in reservation["Instances"]
    ]
    if len(instances) != 1:
        raise BakeError("The Terraform builder instance could not be identified uniquely.")
    return instances[0]


def existing_image(root: Path, name: str) -> dict[str, Any] | None:
    images = aws(
        root,
        "ec2",
        "describe-images",
        "--owners",
        "self",
        "--filters",
        f"Name=name,Values={name}",
    )["Images"]
    if len(images) > 1:
        raise BakeError(f"Multiple AMIs have the expected name: {name}")
    if not images:
        return None
    image = images[0]
    tags = {tag["Key"]: tag["Value"] for tag in image.get("Tags", [])}
    if any(
        tags.get(key) != value
        for key, value in {
            "Project": "flash-ticket",
            "Environment": "dev",
            "Role": "mongodb-ami",
        }.items()
    ):
        raise BakeError("An AMI with the expected name has different ownership tags.")
    if image["State"] not in {"pending", "available"}:
        raise BakeError(f"Existing AMI state is {image['State']}; inspect it before retrying.")
    return image


def wait_ssm(root: Path, instance_id: str, timeout: int = 600) -> None:
    print(f"Waiting for SSM on {instance_id} (up to {timeout} seconds)...", flush=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        information = aws(
            root,
            "ssm",
            "describe-instance-information",
            "--filters",
            json.dumps([{"Key": "InstanceIds", "Values": [instance_id]}]),
        )["InstanceInformationList"]
        if any(item.get("PingStatus") == "Online" for item in information):
            return
        time.sleep(10)
    raise BakeError(
        "SSM did not become Online. Inspect the builder IAM instance profile, HTTPS "
        "egress, public-subnet Internet Gateway route, and EC2 console output. "
        "The builder has been left running."
    )


def send_command(root: Path, instance_id: str, script: str, comment: str) -> str:
    document = aws(
        root,
        "ssm",
        "send-command",
        "--instance-ids",
        instance_id,
        "--document-name",
        "AWS-RunShellScript",
        "--comment",
        comment,
        "--timeout-seconds",
        "120",
        "--parameters",
        json.dumps({"commands": [script], "executionTimeout": ["960"]}),
    )
    return document["Command"]["CommandId"]


def invocation(root: Path, instance_id: str, command_id: str) -> dict[str, Any] | None:
    try:
        return aws(
            root,
            "ssm",
            "get-command-invocation",
            "--command-id",
            command_id,
            "--instance-id",
            instance_id,
        )
    except CommandError as exc:
        if "InvocationDoesNotExist" in str(exc):
            return None
        raise


def wait_validation(root: Path, instance_id: str, command_id: str) -> None:
    print(f"Validating MongoDB build through SSM command {command_id}...", flush=True)
    deadline = time.monotonic() + 1080
    while time.monotonic() < deadline:
        result = invocation(root, instance_id, command_id)
        if result is None or result["Status"] in {"Pending", "InProgress", "Delayed"}:
            time.sleep(5)
            continue
        output = result.get("StandardOutputContent", "").strip()
        error = result.get("StandardErrorContent", "").strip()
        if output:
            print(output, flush=True)
        if error:
            print(error, file=sys.stderr, flush=True)
        if result["Status"] != "Success" or result.get("ResponseCode") != 0:
            raise BakeError(
                f"SSM validation ended with {result['Status']}. No AMI was created; "
                "the builder remains available for inspection."
            )
        return
    raise BakeError("SSM validation timed out. No AMI was created.")


def wait_stopped(root: Path, instance_id: str, finalize_command: str | None) -> None:
    print("Waiting for finalization and EC2 state stopped...", flush=True)
    deadline = time.monotonic() + 600
    checked_command = False
    while time.monotonic() < deadline:
        state = instance(root, instance_id)["State"]["Name"]
        if state == "stopped":
            return
        if state not in {"running", "stopping"}:
            raise BakeError(f"Unexpected builder state while stopping: {state}")
        if finalize_command and not checked_command:
            result = invocation(root, instance_id, finalize_command)
            if result and result["Status"] in {"Failed", "Cancelled", "TimedOut"}:
                raise BakeError(
                    "Scheduling the AMI finalizer failed: "
                    + result.get("StandardErrorContent", result["Status"])
                )
            if result and result["Status"] == "Success":
                checked_command = True
        time.sleep(5)
    raise BakeError(
        "Builder did not reach stopped state. Inspect "
        "/var/log/flash-ticket-mongo-finalize.log before retrying. No AMI was created."
    )


def wait_image(root: Path, image_id: str) -> dict[str, Any]:
    print(f"Waiting for AMI {image_id} to become available...", flush=True)
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        images = aws(root, "ec2", "describe-images", "--image-ids", image_id)["Images"]
        if not images:
            raise BakeError(f"AMI {image_id} could not be found.")
        image = images[0]
        if image["State"] == "available":
            return image
        if image["State"] != "pending":
            raise BakeError(f"AMI {image_id} entered state {image['State']}.")
        time.sleep(15)
    raise BakeError(f"AMI {image_id} is still pending. Rerun this script to resume waiting.")


def bake(root: Path) -> None:
    builder = command(
        ["terraform", "-chdir=infra/environments/dev", "output", "-json", "mongo_ami_builder"],
        root=root,
    )
    if not isinstance(builder, dict) or not INSTANCE_ID.fullmatch(
        str(builder.get("instance_id", ""))
    ):
        raise BakeError(
            "Terraform output mongo_ami_builder is empty. Apply the network/builder stage "
            "with enable_mongo_ami_builder=true and enable_data_layer=false first."
        )
    instance_id = builder["instance_id"]
    account_id = aws(root, "sts", "get-caller-identity")["Account"]
    details = instance(root, instance_id)
    tags = {item["Key"]: item["Value"] for item in details.get("Tags", [])}
    if tags.get("Project") != "flash-ticket" or tags.get("Environment") != "dev":
        raise BakeError("Builder ownership tags must be Project=flash-ticket and Environment=dev.")
    name = f"flash-ticket-dev-mongo80-{instance_id}"
    image = existing_image(root, name)
    proof_path = root / "output" / f"mongo-ami-{instance_id}.validated.json"
    expected_proof = {
        "InstanceId": instance_id,
        "AccountId": account_id,
        "BaseImageId": details["ImageId"],
    }

    if image is None:
        state = details["State"]["Name"]
        if state == "pending":
            deadline = time.monotonic() + 300
            while state == "pending" and time.monotonic() < deadline:
                time.sleep(5)
                state = instance(root, instance_id)["State"]["Name"]
        if state == "running":
            wait_ssm(root, instance_id)
            validation = send_command(
                root, instance_id, VALIDATE_COMMAND, "Validate Flash-Ticket MongoDB AMI build"
            )
            wait_validation(root, instance_id, validation)
            atomic_json(
                proof_path,
                {
                    **expected_proof,
                    "ValidatedAt": datetime.now(timezone.utc).isoformat(),
                    "ValidationCommandId": validation,
                },
            )
            finalizer = send_command(
                root, instance_id, FINALIZE_COMMAND, "Schedule Flash-Ticket MongoDB AMI finalizer"
            )
            atomic_json(
                proof_path,
                {
                    **expected_proof,
                    "ValidatedAt": datetime.now(timezone.utc).isoformat(),
                    "ValidationCommandId": validation,
                    "FinalizerCommandId": finalizer,
                },
            )
            wait_stopped(root, instance_id, finalizer)
        elif state in {"stopping", "stopped"}:
            try:
                proof = json.loads(proof_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise BakeError(
                    "Builder is stopped/stopping without a saved SSM validation record. "
                    "Start this builder instance and rerun validation; no AMI was created."
                ) from exc
            if not isinstance(proof, dict) or any(
                proof.get(key) != value for key, value in expected_proof.items()
            ):
                raise BakeError("Saved SSM validation record does not match this builder/account.")
            for field in ("ValidationCommandId", "FinalizerCommandId"):
                command_id = proof.get(field)
                if not isinstance(command_id, str) or not COMMAND_ID.fullmatch(command_id):
                    raise BakeError(
                        "The stopped builder has no complete SSM finalization record. "
                        "Start this builder and rerun validation; no AMI was created."
                    )
            result = invocation(root, instance_id, proof["ValidationCommandId"])
            if not result or result["Status"] != "Success" or result.get("ResponseCode") != 0:
                raise BakeError(
                    "Saved SSM validation did not succeed; no AMI was created. "
                    "Start the builder and inspect it before retrying."
                )
            # The finalizer deliberately stops SSM. Its last command status can lag
            # behind shutdown; the recorded schedule plus EC2 stopped state is used.
            wait_stopped(root, instance_id, None)
        else:
            raise BakeError(f"Builder must be running or previously validated; state is {state}.")

        image_tags = [
            {"Key": "Name", "Value": name},
            {"Key": "Project", "Value": "flash-ticket"},
            {"Key": "Environment", "Value": "dev"},
            {"Key": "Role", "Value": "mongodb-ami"},
        ]
        created = aws(
            root,
            "ec2",
            "create-image",
            "--instance-id",
            instance_id,
            "--name",
            name,
            "--description",
            "Flash-Ticket MongoDB 8.0 on Ubuntu 24.04; no credentials; SSM agent disabled",
            "--no-reboot",
            "--tag-specifications",
            json.dumps(
                [
                    {"ResourceType": "image", "Tags": image_tags},
                    {"ResourceType": "snapshot", "Tags": image_tags},
                ]
            ),
        )
        image_id = created["ImageId"]
    else:
        image_id = image["ImageId"]

    image = wait_image(root, image_id)
    manifest = {
        "ImageId": image["ImageId"],
        "Name": image["Name"],
        "State": image["State"],
        "SnapshotIds": [
            mapping["Ebs"]["SnapshotId"]
            for mapping in image["BlockDeviceMappings"]
            if mapping.get("Ebs", {}).get("SnapshotId")
        ],
    }
    atomic_json(root / "output" / "mongo-ami.json", manifest)
    atomic_text(
        root / "infra" / "environments" / "dev" / "mongodb-ami.auto.tfvars",
        "mongodb_ami_id = " + json.dumps(image_id) + "\n",
    )
    print(f"AMI ready: {image_id}", flush=True)
    print("Wrote output/mongo-ami.json and infra/environments/dev/mongodb-ami.auto.tfvars.")
    print("Disable enable_mongo_ami_builder and apply to remove the stopped builder and its volume.")
    print("The private AMI and snapshots remain billable until explicitly deregistered/deleted.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Flash-Ticket repository root (default: parent of this scripts directory)",
    )
    arguments = parser.parse_args()
    try:
        bake(arguments.repo_root.resolve())
    except (BakeError, OSError, KeyError, ValueError) as exc:
        print(f"MONGO_AMI_BAKE_FAILED: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(
            "Interrupted. Inspect builder state before retrying; resources were not deleted.",
            file=sys.stderr,
        )
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
