#!/usr/bin/env python3

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import quote, urlencode


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEV_ROOT = PROJECT_ROOT / "infra" / "environments" / "dev"

SSH_SCRIPT = PROJECT_ROOT / "scripts" / "ssh-mongodb.sh"
BOOTSTRAP_SCRIPT = PROJECT_ROOT / "scripts" / "mongodb" / "bootstrap.js"

AWS_PROFILE = "flash-ticket-terraform"
AWS_REGION = "us-east-1"

ROOT_PASSWORD_PARAMETER = (
    "/flash-ticket/dev/data/MONGODB_ROOT_PASSWORD"
)
APP_PASSWORD_PARAMETER = (
    "/flash-ticket/dev/data/MONGODB_APP_PASSWORD"
)
CATALOG_URI_PARAMETER = (
    "/flash-ticket/dev/catalog/MONGODB_URI"
)

REMOTE_SCRIPT = "/usr/local/lib/flash-ticket/bootstrap-mongodb.js"

READINESS_COMMAND = r"""set -u
cloud_init_result=0
sudo -n cloud-init status --wait --long || cloud_init_result=$?
mongodb_result=0
sudo -n systemctl is-active mongod || mongodb_result=$?
if [ "$cloud_init_result" -ne 0 ] || [ "$mongodb_result" -ne 0 ]; then
  printf 'READINESS_FAILED cloud_init_exit=%s mongod_exit=%s\n' "$cloud_init_result" "$mongodb_result"
  sudo -n systemctl status mongod --no-pager --full || true
  sudo -n journalctl -u mongod -n 60 --no-pager || true
  sudo -n tail -n 80 /var/log/flash-ticket-mongodb-node.log || true
  exit 1
fi
"""


class ProvisionError(RuntimeError):
    pass


class Runner:
    def __init__(self):
        self.secret_values = set()

    def remember(self, value):
        if value:
            self.secret_values.add(value)
            self.secret_values.add(quote(value, safe=""))

    def redact(self, text):
        for value in sorted(
            self.secret_values,
            key=len,
            reverse=True,
        ):
            text = text.replace(value, "[REDACTED]")
        return text

    def command(self, arguments, *, input_text=None):
        environment = os.environ.copy()
        environment["AWS_PAGER"] = ""
        environment["AWS_CLI_AUTO_PROMPT"] = "off"

        environment.pop("TF_LOG", None)
        environment.pop("TF_LOG_PATH", None)

        try:
            result = subprocess.run(
                arguments,
                input=input_text,
                capture_output=True,
                text=True,
                env=environment,
                timeout=300,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise ProvisionError(
                f"Command timed out: {arguments[0]}"
            ) from None

        if result.returncode != 0:
            details = [
                f"Command failed: {Path(arguments[0]).name} (exit_code={result.returncode})"
            ]
            if result.stdout and result.stdout.strip():
                details.append("stdout:\n" + result.stdout.strip())
            if result.stderr and result.stderr.strip():
                details.append("stderr:\n" + result.stderr.strip())
            raise ProvisionError(self.redact("\n".join(details)))

        return result.stdout

    def aws(self, *arguments, request=None):
        command = [
            "aws",
            *arguments,
            "--profile", AWS_PROFILE,
            "--region", AWS_REGION,
            "--output", "json",
            "--no-cli-pager",
        ]

        if request is None:
            output = self.command(command)
        else:
            with tempfile.TemporaryDirectory(prefix="flash-ticket-mongodb-request-") as directory:
                request_file = Path(directory) / "request.json"
                descriptor = os.open(
                    request_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(request, stream, ensure_ascii=True, allow_nan=False)
                output = self.command([
                    *command, "--cli-input-json", request_file.as_uri(),
                ])

        return json.loads(output) if output.strip() else {}


def load_metadata(runner):
    output = runner.command([
        "terraform",
        f"-chdir={DEV_ROOT}",
        "output",
        "-json",
        "mongodb",
    ])

    metadata = json.loads(output)

    if not isinstance(metadata, dict):
        raise ProvisionError(
            "MongoDB output is missing. Restore the infrastructure first."
        )

    if not re.fullmatch(
        r"i-[0-9a-f]{17}",
        metadata.get("instance_id", ""),
    ):
        raise ProvisionError("Invalid MongoDB instance ID.")

    address = ipaddress.ip_address(
        metadata.get("private_ip", "")
    )

    if address.version != 4 or not address.is_private:
        raise ProvisionError(
            "MongoDB must have a private IPv4 address."
        )

    return metadata


def load_passwords(runner):
    response = runner.aws(
        "ssm",
        "get-parameters",
        "--names",
        ROOT_PASSWORD_PARAMETER,
        APP_PASSWORD_PARAMETER,
        "--with-decryption",
    )

    parameters = {
        parameter["Name"]: parameter
        for parameter in response.get("Parameters", [])
    }

    values = {}

    for field, name in (
        ("root_password", ROOT_PASSWORD_PARAMETER),
        ("app_password", APP_PASSWORD_PARAMETER),
    ):
        parameter = parameters.get(name)

        if parameter is None:
            raise ProvisionError(
                f"Required SSM parameter is missing: {name}"
            )

        if parameter["Type"] != "SecureString":
            raise ProvisionError(
                f"SSM parameter must be SecureString: {name}"
            )

        value = parameter["Value"]
        runner.remember(value)

        if not 16 <= len(value) <= 128 or "\x00" in value:
            raise ProvisionError(
                f"Invalid password format in SSM parameter: {name}"
            )

        values[field] = value

    return values


def bootstrap_remote(runner, payload):
    print("MongoDB stage: instance readiness", flush=True)
    runner.command(["bash", str(SSH_SCRIPT), READINESS_COMMAND])

    print("MongoDB stage: upload bootstrap script", flush=True)
    runner.command(
        [
            "bash",
            str(SSH_SCRIPT),
            (
                "sudo -n install -d -m 0755 /usr/local/lib/flash-ticket"
                f" && sudo -n tee {REMOTE_SCRIPT} >/dev/null"
                f" && sudo -n chmod 0644 {REMOTE_SCRIPT}"
            ),
        ],
        input_text=BOOTSTRAP_SCRIPT.read_text(
            encoding="utf-8"
        ),
    )

    print("MongoDB stage: provision database users", flush=True)
    output = runner.command(
        [
            "bash",
            str(SSH_SCRIPT),
            (
                "sudo -n env MONGOSH_DISABLE_TELEMETRY=1"
                " mongosh --quiet --norc"
                " --host 127.0.0.1 --port 27017"
                f" --file {REMOTE_SCRIPT}"
            ),
        ],
        input_text=json.dumps(payload),
    )

    for line in reversed(output.splitlines()):
        try:
            report = json.loads(line)
        except json.JSONDecodeError:
            continue

        if (
            isinstance(report, dict)
            and report.get("status") == "ready"
            and report.get("database") == payload["database"]
            and report.get("app_user") == payload["app_username"]
        ):
            return report

    raise ProvisionError(
        "Remote bootstrap did not return a valid ready report."
    )


def build_uri(metadata, payload):
    host = metadata.get("private_dns") or metadata["private_ip"]

    if not re.fullmatch(r"[A-Za-z0-9.-]+", host):
        raise ProvisionError("Invalid MongoDB hostname.")

    username = quote(payload["app_username"], safe="")
    password = quote(payload["app_password"], safe="")
    database = quote(payload["database"], safe="")

    options = urlencode({
        "authSource": payload["database"],
        "directConnection": "true",
        "retryWrites": "false",
        "serverSelectionTimeoutMS": "3000",
    })

    return (
        f"mongodb://{username}:{password}"
        f"@{host}:27017/{database}?{options}"
    )


def save_uri(runner, uri):
    runner.remember(uri)

    response = runner.aws(
        "ssm",
        "get-parameters",
        "--names",
        CATALOG_URI_PARAMETER,
        "--with-decryption",
    )

    parameters = response.get("Parameters", [])
    existing = parameters[0] if parameters else None

    if existing:
        runner.remember(existing["Value"])

        if existing["Type"] != "SecureString":
            raise ProvisionError(
                "Existing Catalog URI parameter must be SecureString."
            )

        if existing["Value"] == uri:
            return "unchanged"

    request = {
        "Name": CATALOG_URI_PARAMETER,
        "Description": "MongoDB URI for Flash-Ticket development Catalog.",
        "Value": uri,
        "Type": "SecureString",
        "Tier": "Standard",
        "Overwrite": existing is not None,
    }

    if existing is None:
        request["Tags"] = [
            {"Key": "Project", "Value": "flash-ticket"},
            {"Key": "Environment", "Value": "dev"},
            {"Key": "ManagedBy", "Value": "mongodb-provisioner"},
        ]

    runner.aws(
        "ssm",
        "put-parameter",
        request=request,
    )

    return "updated" if existing else "created"


def provision(runner, database, root_user, app_user):
    metadata = load_metadata(runner)
    passwords = load_passwords(runner)

    payload = {
        "database": database,
        "root_username": root_user,
        "app_username": app_user,
        **passwords,
    }

    report = bootstrap_remote(runner, payload)

    uri = build_uri(metadata, payload)
    status = save_uri(runner, uri)

    return report, status


def check_local(runner):
    for executable in (
        "aws",
        "terraform",
        "bash",
        "ssh",
        "ssh-keygen",
    ):
        if shutil.which(executable) is None:
            raise ProvisionError(
                f"Required executable is missing: {executable}"
            )

    for path in (SSH_SCRIPT, BOOTSTRAP_SCRIPT):
        if not path.is_file():
            raise ProvisionError(f"Required file is missing: {path}")

    runner.command(["bash", "-n", str(SSH_SCRIPT)])


def main():
    parser = argparse.ArgumentParser(
        description="Provision MongoDB users and publish the Catalog URI."
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument("--execute", action="store_true")

    parser.add_argument("--database", default="flash_ticket")
    parser.add_argument("--root-user", default="flash_admin")
    parser.add_argument("--app-user", default="flash_catalog")

    args = parser.parse_args()
    runner = Runner()

    try:
        if (
            not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", args.database)
            or args.database in {"admin", "config", "local"}
        ):
            raise ProvisionError("Invalid application database name.")

        for username in (args.root_user, args.app_user):
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", username):
                raise ProvisionError("Invalid MongoDB username.")

        if args.root_user == args.app_user:
            raise ProvisionError("Admin and application usernames must differ.")

        check_local(runner)

        if not args.execute:
            print("Offline checks passed. No AWS APIs were called.")
            return

        report, status = provision(
            runner,
            args.database,
            args.root_user,
            args.app_user,
        )

        print(
            "MongoDB users verified: "
            f"root_created={report['root_created']}, "
            f"app_created={report['app_created']}"
        )
        print(f"Catalog URI parameter {status}: {CATALOG_URI_PARAMETER}")

    except (
        ProvisionError,
        OSError,
        ValueError,
        KeyError,
    ) as error:
        print(
            f"ERROR: {runner.redact(str(error))}",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()