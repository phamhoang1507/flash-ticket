#!/usr/bin/env python3

import json
from pathlib import Path
import secrets
import subprocess
import sys
import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_SCRIPT = PROJECT_ROOT / "scripts" / "mongodb" / "bootstrap.js"

CONTAINER = "flash-ticket-mongo-bootstrap-test"
IMAGE = "mongo:8.0"

SECRET_VALUES = []


def redact(text):
    for value in SECRET_VALUES:
        text = text.replace(value, "[REDACTED]")
    return text


def command(arguments, *, input_text=None, allow_failure=False):
    result = subprocess.run(
        arguments,
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0 and not allow_failure:
        details = redact(result.stderr or result.stdout)
        raise RuntimeError(
            f"Command failed: {' '.join(arguments[:3])}\n{details}"
        )

    return result


def run_bootstrap(payload, *, expect_success):
    result = command(
        [
            "docker", "exec", "-i", CONTAINER,
            "mongosh",
            "--quiet",
            "--norc",
            "--host", "127.0.0.1",
            "--port", "27017",
            "--file", "/tmp/bootstrap.js",
        ],
        input_text=json.dumps(payload),
        allow_failure=True,
    )

    combined_output = result.stdout + "\n" + result.stderr

    if any(value in combined_output for value in SECRET_VALUES):
        raise RuntimeError("A secret was found in bootstrap output.")

    if not expect_success:
        if result.returncode == 0:
            raise RuntimeError("Bootstrap unexpectedly accepted wrong credentials.")
        return None

    if result.returncode != 0:
        raise RuntimeError(
            "Bootstrap failed:\n" + redact(combined_output)
        )

    for line in reversed(result.stdout.splitlines()):
        try:
            report = json.loads(line)
        except json.JSONDecodeError:
            continue

        if isinstance(report, dict) and report.get("status") == "ready":
            return report

    raise RuntimeError("Bootstrap did not return a ready report.")


def main():
    if not BOOTSTRAP_SCRIPT.is_file():
        raise RuntimeError(f"Missing script: {BOOTSTRAP_SCRIPT}")

    command(["docker", "info"])

    payload = {
        "database": "flash_ticket",
        "root_username": "flash_admin",
        "root_password": secrets.token_hex(16),
        "app_username": "flash_catalog",
        "app_password": secrets.token_hex(16) + ":@/%&",
    }

    SECRET_VALUES.extend([
        payload["root_password"],
        payload["app_password"],
    ])

    created = False

    try:
        command([
            "docker", "run",
            "--detach",
            "--rm",
            "--name", CONTAINER,
            "--label", "com.flash-ticket.purpose=mongodb-bootstrap-test",
            "--network", "none",
            "--memory", "1g",
            "--tmpfs", "/data/db:rw,size=512m",
            "--tmpfs", "/data/configdb:rw,size=32m",
            "--env", "MONGOSH_DISABLE_TELEMETRY=1",
            IMAGE,
            "--auth",
            "--bind_ip", "127.0.0.1",
            "--wiredTigerCacheSizeGB", "0.25",
        ])
        created = True

        for _ in range(30):
            result = command(
                [
                    "docker", "exec", CONTAINER,
                    "mongosh",
                    "--quiet",
                    "--norc",
                    "mongodb://127.0.0.1:27017/?serverSelectionTimeoutMS=2000",
                    "--eval",
                    "quit(db.adminCommand({ping: 1}).ok === 1 ? 0 : 1)",
                ],
                allow_failure=True,
            )

            if result.returncode == 0:
                break

            time.sleep(1)
        else:
            raise RuntimeError("MongoDB test container did not become ready.")

        command([
            "docker", "cp",
            str(BOOTSTRAP_SCRIPT),
            f"{CONTAINER}:/tmp/bootstrap.js",
        ])

        first = run_bootstrap(payload, expect_success=True)

        if not first["root_created"] or not first["app_created"]:
            raise RuntimeError("First bootstrap did not create both users.")

        print("PASS: first bootstrap creates admin and application users.")

        repeated = run_bootstrap(payload, expect_success=True)

        if repeated["root_created"] or repeated["app_created"]:
            raise RuntimeError("Repeated bootstrap unexpectedly recreated users.")

        print("PASS: repeated bootstrap preserves existing users.")

        wrong_root = dict(payload)
        wrong_root["root_password"] = secrets.token_hex(16)
        SECRET_VALUES.append(wrong_root["root_password"])

        run_bootstrap(wrong_root, expect_success=False)
        print("PASS: wrong admin password is rejected.")

        wrong_app = dict(payload)
        wrong_app["app_password"] = secrets.token_hex(16)
        SECRET_VALUES.append(wrong_app["app_password"])

        run_bootstrap(wrong_app, expect_success=False)
        print("PASS: wrong application password is rejected.")

        run_bootstrap(payload, expect_success=True)
        print("PASS: original credentials still work after rejected attempts.")

        permission_check = """
const input = JSON.parse(require("fs").readFileSync(0, "utf8"));
const auth = db.getSiblingDB(input.database).auth(
  input.app_username,
  input.app_password
);

if (auth !== 1 && auth?.ok !== 1) {
  quit(2);
}

try {
  const result = db.getSiblingDB("admin").runCommand({usersInfo: 1});
  quit(result.ok === 0 && result.code === 13 ? 0 : 1);
} catch (error) {
  quit(error.code === 13 ? 0 : 1);
}
"""

        command(
            [
                "docker", "exec", "-i", CONTAINER,
                "mongosh",
                "--quiet",
                "--norc",
                "--host", "127.0.0.1",
                "--port", "27017",
                "--eval", permission_check,
            ],
            input_text=json.dumps(payload),
        )

        print("PASS: application user cannot inspect admin users.")
        print("All MongoDB bootstrap checks passed.")

    finally:
        if created:
            command(
                ["docker", "rm", "--force", CONTAINER],
                allow_failure=True,
            )


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError) as error:
        print(f"ERROR: {redact(str(error))}", file=sys.stderr)
        sys.exit(1)