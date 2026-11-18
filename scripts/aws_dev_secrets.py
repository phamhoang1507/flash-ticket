#!/usr/bin/env python3

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_FILE = PROJECT_ROOT / ".secrets" / "aws-dev-secrets.json"
PARAMETER_PREFIX = "/flash-ticket/dev"

# Number of random bytes before hexadecimal encoding.
SECRET_SIZES = {
    "data/MYSQL_MASTER_PASSWORD": 16,
    "data/MYSQL_APP_PASSWORD": 16,
    "data/MONGODB_ROOT_PASSWORD": 16,
    "data/MONGODB_APP_PASSWORD": 16,
    "data/REDIS_AUTH_TOKEN": 32,
    "worker/TICKET_SIGNING_SECRET": 32,
}

REDACTED_VALUES = set()


def aws_json(profile, region, *arguments):
    command = [
        "aws",
        *arguments,
        "--profile",
        profile,
        "--region",
        region,
        "--output",
        "json",
        "--no-cli-pager",
    ]

    environment = os.environ.copy()
    environment["AWS_PAGER"] = ""
    environment["AWS_CLI_AUTO_PROMPT"] = "off"

    result = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )

    if result.returncode != 0:
        message = result.stderr.strip() or "AWS CLI returned an error."

        for value in REDACTED_VALUES:
            message = message.replace(value, "[REDACTED]")

        raise RuntimeError(message)

    return json.loads(result.stdout)


def load_local_values(account_id, region):
    if not LOCAL_FILE.exists():
        return None

    if LOCAL_FILE.is_symlink():
        raise RuntimeError("Refusing to read a symlink as the secrets file.")

    LOCAL_FILE.chmod(0o600)
    document = json.loads(LOCAL_FILE.read_text(encoding="utf-8"))

    if document.get("account_id") != account_id:
        raise RuntimeError("Local secrets belong to a different AWS account.")

    if document.get("region") != region:
        raise RuntimeError("Local secrets belong to a different AWS Region.")

    values = document.get("parameters")

    if not isinstance(values, dict):
        raise RuntimeError("Invalid local secrets file.")

    if set(values) != set(SECRET_SIZES):
        raise RuntimeError("Local secrets file contains unexpected parameter names.")

    if any(not isinstance(value, str) or not value for value in values.values()):
        raise RuntimeError("Local secrets file contains an invalid value.")

    return values


def save_local_values(account_id, region, values):
    LOCAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_FILE.parent.chmod(0o700)

    document = {
        "account_id": account_id,
        "region": region,
        "parameters": values,
    }

    descriptor = os.open(
        LOCAL_FILE,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )

    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2)
        handle.write("\n")


def main():
    parser = argparse.ArgumentParser(
        description="Create or verify Flash-Ticket development secrets."
    )
    parser.add_argument("--profile", default="flash-ticket-terraform")
    parser.add_argument("--region", default="us-east-1")
    arguments = parser.parse_args()

    identity = aws_json(
        arguments.profile,
        arguments.region,
        "sts",
        "get-caller-identity",
    )
    account_id = identity["Account"]

    if account_id == "000000000000":
        raise RuntimeError("This script must target AWS, not LocalStack.")

    names = [
        f"{PARAMETER_PREFIX}/{suffix}"
        for suffix in SECRET_SIZES
    ]

    response = aws_json(
        arguments.profile,
        arguments.region,
        "ssm",
        "get-parameters",
        "--names",
        *names,
        "--with-decryption",
    )

    remote_values = {}

    for parameter in response["Parameters"]:
        if parameter["Type"] != "SecureString":
            raise RuntimeError(
                f"Existing parameter is not SecureString: {parameter['Name']}"
            )

        remote_values[parameter["Name"]] = parameter["Value"]
        REDACTED_VALUES.add(parameter["Value"])

    local_values = load_local_values(account_id, arguments.region)

    if local_values is None:
        local_values = {}

        for suffix, size in SECRET_SIZES.items():
            name = f"{PARAMETER_PREFIX}/{suffix}"

            if name in remote_values:
                local_values[suffix] = remote_values[name]
            else:
                local_values[suffix] = secrets.token_hex(size)

    REDACTED_VALUES.update(local_values.values())

    # Check every existing value before creating any missing parameter.
    for suffix, value in local_values.items():
        name = f"{PARAMETER_PREFIX}/{suffix}"

        if name in remote_values and remote_values[name] != value:
            raise RuntimeError(
                f"Local and AWS values differ for {name}. "
                "No existing secret was overwritten."
            )

    if not LOCAL_FILE.exists():
        save_local_values(
            account_id,
            arguments.region,
            local_values,
        )

    with tempfile.TemporaryDirectory(prefix="flash-ticket-ssm-") as directory:
        request_file = Path(directory) / "request.json"

        descriptor = os.open(
            request_file,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        os.close(descriptor)

        for suffix, value in local_values.items():
            name = f"{PARAMETER_PREFIX}/{suffix}"

            if name in remote_values:
                print(f"UNCHANGED {name}")
                continue

            request = {
                "Name": name,
                "Description": "Flash-Ticket development secret.",
                "Value": value,
                "Type": "SecureString",
                "Tier": "Standard",
                "Overwrite": False,
                "Tags": [
                    {"Key": "Project", "Value": "flash-ticket"},
                    {"Key": "Environment", "Value": "dev"},
                    {"Key": "ManagedBy", "Value": "aws-dev-secrets-script"},
                ],
            }

            request_file.write_text(
                json.dumps(request),
                encoding="utf-8",
            )

            result = aws_json(
                arguments.profile,
                arguments.region,
                "ssm",
                "put-parameter",
                "--cli-input-json",
                request_file.as_uri(),
            )

            print(f"CREATED {name} version={result['Version']}")

    print("Secrets are ready. No secret values were printed.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)