#!/usr/bin/env python3
"""Repair the missing /run/sshd bootstrap failure on the existing MongoDB node."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sys


REMOTE_SCRIPT = r'''
import datetime
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import sys


class RepairError(RuntimeError):
    pass


def announce(message):
    print("MONGODB_REPAIR " + message, flush=True)


def run(arguments, timeout=30):
    try:
        return subprocess.run(
            arguments,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RepairError("Command timed out: " + arguments[0]) from exc


def atomic_write(path, content, mode=0o600, owner=None):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = stream.name
            os.fchmod(stream.fileno(), mode)
            if owner is not None:
                os.fchown(stream.fileno(), owner[0], owner[1])
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RepairError("Cannot read cloud-init JSON: " + str(path)) from exc
    if not isinstance(value, dict):
        raise RepairError("Unexpected cloud-init JSON structure: " + str(path))
    return value


def status_report():
    result = run(["cloud-init", "status", "--format", "json"])
    try:
        document = json.loads(result.stdout)
    except ValueError as exc:
        raise RepairError("cloud-init status did not return valid JSON.") from exc
    if not isinstance(document, dict):
        raise RepairError("cloud-init status returned an unexpected structure.")
    nested = document.get("v1", {})
    if not isinstance(nested, dict):
        nested = {}
    state = document.get("status", nested.get("status"))
    stage = document.get("stage", nested.get("stage"))
    if state == "running" or stage is not None:
        raise RepairError("cloud-init is still running. Wait before repairing this node.")
    return result.returncode, state


def stage_errors(stage, name):
    if not isinstance(stage, dict):
        raise RepairError("Missing or invalid cloud-init stage: " + name)
    errors = stage.get("errors", [])
    if not isinstance(errors, list):
        raise RepairError("Unexpected errors structure in stage: " + name)
    return errors


def mongo_active():
    return run(["systemctl", "is-active", "--quiet", "mongod"]).returncode == 0


def require_ready_marker():
    path = Path("/var/log/flash-ticket-mongodb-node.log")
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise RepairError("The MongoDB node readiness log is missing.") from exc
    if "FLASH_TICKET_MONGODB_NODE_READY" not in lines:
        raise RepairError("The MongoDB bootstrap readiness marker is missing.")


def install_runtime_directory():
    announce("stage=sshd-runtime-directory")
    directory = Path("/run/sshd")
    if directory.is_symlink():
        raise RepairError("Refusing to use a symlink for /run/sshd.")
    directory.mkdir(mode=0o755, exist_ok=True)
    os.chown(directory, 0, 0)
    os.chmod(directory, 0o755)
    configuration = Path("/etc/tmpfiles.d/flash-ticket-sshd.conf")
    configuration.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    atomic_write(
        configuration,
        "d /run/sshd 0755 root root -\n",
        mode=0o644,
        owner=(0, 0),
    )
    validation = run(["/usr/sbin/sshd", "-t"])
    if validation.returncode:
        raise RepairError(
            "sshd configuration validation still failed. Inspect sudo sshd -t."
        )


def repair():
    if os.geteuid() != 0:
        raise RepairError("The remote repair must run as root through sudo -n.")
    os.umask(0o077)
    lock_descriptor = os.open(
        "/run/flash-ticket-mongo-repair.lock",
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(lock_descriptor, "r+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RepairError("Another MongoDB repair is already running.") from exc

        announce("stage=inspect-cloud-init")
        status_path = Path("/var/lib/cloud/data/status.json")
        result_path = Path("/var/lib/cloud/data/result.json")
        status = read_json(status_path)
        v1 = status.get("v1", status)
        if not isinstance(v1, dict) or v1.get("stage") is not None:
            raise RepairError("Cloud-init has an active stage or invalid state.")
        status_code, state = status_report()
        if status_code not in (0, 1, 2):
            raise RepairError("Unexpected cloud-init status exit code.")
        if state not in ("done", "error"):
            raise RepairError("Cloud-init is not in a completed state.")
        final_pid = run([
            "systemctl", "show", "--property=MainPID", "--value", "cloud-final.service"
        ])
        if final_pid.returncode == 0 and final_pid.stdout.strip() not in ("", "0"):
            raise RepairError("cloud-final.service still has a running process; wait before retrying.")

        install_runtime_directory()
        if status_code == 0 and state == "done" and mongo_active():
            require_ready_marker()
            announce("stage=complete result=already-ready persistent-sshd-directory=installed")
            return

        for name in ("init-local", "init", "modules-config"):
            if stage_errors(v1.get(name), name):
                raise RepairError(
                    "Cloud-init has errors outside scripts_user; refusing a targeted reset."
                )
        failures = stage_errors(v1.get("modules-final"), "modules-final")
        if not failures or any(
            not isinstance(error, str)
            or ("scripts_user" not in error and "scripts-user" not in error)
            for error in failures
        ):
            raise RepairError(
                "The final-stage errors are not exclusively scripts_user; no state was reset."
            )

        announce("stage=backup-cloud-init-state")
        backup_root = Path("/root/flash-ticket-mongo-repair")
        backup_root.mkdir(mode=0o700, exist_ok=True)
        os.chmod(backup_root, 0o700)
        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y%m%dT%H%M%S%fZ"
        )
        backup = backup_root / (timestamp + "-" + str(os.getpid()))
        backup.mkdir(mode=0o700)
        for original in (status_path, result_path):
            if original.exists():
                destination = backup / original.name
                destination.write_bytes(original.read_bytes())
                os.chmod(destination, 0o600)
        announce("backup=" + str(backup))

        configuration = backup / "rerun-final.json"
        module_help = run(["cloud-init", "modules", "--help"])
        if module_help.returncode or "--file" not in module_help.stdout:
            raise RepairError(
                "Installed cloud-init does not expose modules --file; no status was reset."
            )
        atomic_write(
            configuration,
            json.dumps({"cloud_final_modules": [["scripts_user", "always"]]}) + "\n",
        )
        try:
            announce("stage=reset-only-final-stage")
            metadata = status_path.stat()
            del v1["modules-final"]
            atomic_write(
                status_path,
                json.dumps(status) + "\n",
                mode=stat.S_IMODE(metadata.st_mode),
                owner=(metadata.st_uid, metadata.st_gid),
            )

            announce("stage=rerun-original-node-bootstrap")
            rerun = run(
                [
                    "cloud-init", "modules", "--mode", "final",
                    "--file", str(configuration),
                ],
                timeout=240,
            )
            if rerun.returncode:
                raise RepairError(
                    "The original scripts_user rerun failed (exit "
                    + str(rerun.returncode)
                    + "). Inspect /var/log/cloud-init-output.log and "
                    "/var/log/flash-ticket-mongodb-node.log; backup=" + str(backup)
                )

            announce("stage=verify-cloud-init-and-mongodb")
            code, current_state = status_report()
            if code != 0 or current_state != "done":
                raise RepairError(
                    "Cloud-init did not finish cleanly after repair. Run sudo cloud-init "
                    "status --long; backup=" + str(backup)
                )
            if not mongo_active():
                raise RepairError(
                    "mongod is inactive after bootstrap. Inspect sudo journalctl -u "
                    "mongod --no-pager; backup=" + str(backup)
                )
            require_ready_marker()
            announce("stage=complete result=ready backup=" + str(backup))
        except (RepairError, OSError, ValueError, KeyError):
            # Retain a retryable failure if the CLI failed before initializing its stage.
            try:
                current = read_json(status_path)
                current_v1 = current.get("v1", current)
                if isinstance(current_v1, dict) and "modules-final" not in current_v1:
                    atomic_write(
                        status_path,
                        (backup / "status.json").read_text(encoding="utf-8"),
                        mode=stat.S_IMODE(metadata.st_mode),
                        owner=(metadata.st_uid, metadata.st_gid),
                    )
                    announce("stage=restore-prior-status backup=" + str(backup))
            except (RepairError, OSError, ValueError, KeyError):
                announce("stage=inspect-status-backup backup=" + str(backup))
            raise
        finally:
            configuration.unlink(missing_ok=True)


try:
    repair()
except (RepairError, OSError, ValueError, KeyError) as error:
    print("MONGODB_REPAIR_FAILED: " + str(error), file=sys.stderr, flush=True)
    sys.exit(1)
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Flash-Ticket repository root",
    )
    arguments = parser.parse_args()
    root = arguments.repo_root.resolve()
    ssh_script = root / "scripts" / "ssh-mongodb.sh"
    if not ssh_script.is_file():
        print(f"Required SSH helper is missing: {ssh_script}", file=sys.stderr)
        return 1

    environment = dict(os.environ)
    environment.pop("TF_LOG", None)
    environment.pop("TF_LOG_PATH", None)
    environment["AWS_PAGER"] = ""
    environment["AWS_CLI_AUTO_PROMPT"] = "off"
    remote_command = "sudo -n python3 -c " + shlex.quote(REMOTE_SCRIPT)
    try:
        result = subprocess.run(
            ["bash", str(ssh_script), remote_command],
            cwd=root,
            env=environment,
            timeout=420,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        print(
            "MONGODB_REPAIR_FAILED: SSH invocation failed or timed out. "
            "Inspect the remote cloud-init state before retrying.",
            file=sys.stderr,
        )
        return 1
    if result.returncode:
        print(
            f"MONGODB_REPAIR_FAILED: SSH helper exited with {result.returncode}.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
