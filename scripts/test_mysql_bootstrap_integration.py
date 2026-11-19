#!/usr/bin/env python3

import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote


LABEL = "com.flash-ticket.test=mysql-bootstrap"
SECRET_VALUES = set()

PROBE_CODE = r'''
import json
import socket
import ssl
import sys

import pymysql
import bootstrap

mode = sys.argv[1]
settings = bootstrap.Settings.from_environment()

if mode == "hostname_rejected":
    assert socket.gethostbyname("mysql") == socket.gethostbyname("mysql-wrong")

    try:
        connection = bootstrap.connect(settings, admin=True)
    except Exception as error:
        causes = (
            error,
            getattr(error, "original_exception", None),
            error.__cause__,
            error.__context__,
        )

        matched = any(
            isinstance(cause, ssl.SSLCertVerificationError)
            and cause.verify_code == 62
            for cause in causes
        )

        if not matched:
            raise RuntimeError(
                "Connection failed for a reason other than hostname mismatch."
            ) from error

        print(json.dumps({"hostname_rejected": True}))
        sys.exit(0)
    else:
        connection.close()
        raise RuntimeError("TLS unexpectedly accepted the wrong hostname.")

connection = bootstrap.connect(
    settings,
    admin=mode != "permissions",
)

try:
    with connection.cursor() as cursor:
        if mode == "ready":
            cursor.execute("SELECT VERSION()")
            print(json.dumps({"version": cursor.fetchone()[0]}))

        elif mode == "clear_data":
            for table in ("outbox", "bookings", "inventory"):
                cursor.execute(f"DELETE FROM `flash_ticket`.`{table}`")

            print(json.dumps({"cleared": True}))

        elif mode == "counts":
            counts = {}

            for table in bootstrap.TABLES:
                cursor.execute(
                    f"SELECT COUNT(*) FROM `flash_ticket`.`{table}`"
                )
                counts[table] = cursor.fetchone()[0]

            print(json.dumps(counts))

        elif mode == "permissions":
            for table in bootstrap.TABLES:
                target = f"`flash_ticket`.`{table}`"

                cursor.execute(f"SELECT * FROM {target} LIMIT 0")

                cursor.execute(
                    f"INSERT INTO {target} "
                    f"SELECT * FROM {target} WHERE 1 = 0"
                )

                cursor.execute(f"SHOW COLUMNS FROM {target}")
                column = cursor.fetchone()[0].replace("`", "``")

                cursor.execute(
                    f"UPDATE {target} "
                    f"SET `{column}` = `{column}` WHERE 1 = 0"
                )

                cursor.execute(f"DELETE FROM {target} WHERE 1 = 0")

            forbidden = {
                "create_table": (
                    "CREATE TABLE flash_ticket._forbidden_test "
                    "(id INT PRIMARY KEY)"
                ),
                "migration_ledger": (
                    "SELECT version FROM flash_ticket._schema_migrations LIMIT 1"
                ),
                "system_users": (
                    "SELECT User FROM mysql.user LIMIT 1"
                ),
            }

            for name, statement in forbidden.items():
                try:
                    cursor.execute(statement)
                except pymysql.MySQLError as error:
                    if error.args[0] not in {1044, 1142, 1143, 1227}:
                        raise
                else:
                    raise RuntimeError(
                        f"Application user unexpectedly has permission: {name}"
                    )

            print(json.dumps({"permissions": "verified"}))

        else:
            raise RuntimeError("Unknown probe mode.")

finally:
    connection.close()
'''


def redact(text):
    for value in sorted(SECRET_VALUES, key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
    return text


def run(arguments, *, environment=None, check=True):
    child_environment = os.environ.copy()

    if environment:
        child_environment.update(environment)

    result = subprocess.run(
        arguments,
        env=child_environment,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    output = result.stdout + "\n" + result.stderr

    if any(value in output for value in SECRET_VALUES):
        raise RuntimeError("A test credential appeared in command output.")

    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(arguments[:3])}\n"
            + redact(output)
        )

    return result


def parse_report(result):
    for line in reversed(result.stdout.splitlines()):
        try:
            report = json.loads(line)
        except json.JSONDecodeError:
            continue

        if isinstance(report, dict):
            return report

    raise RuntimeError("Command did not return a JSON report.")


def generate_certificates(directory):
    certificates = directory / "certs"
    certificates.mkdir(mode=0o755)

    run([
        "openssl", "req",
        "-x509",
        "-newkey", "rsa:2048",
        "-sha256",
        "-nodes",
        "-days", "2",
        "-subj", "/CN=Flash-Ticket Local Test CA",
        "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign",
        "-keyout", str(directory / "ca-key.pem"),
        "-out", str(certificates / "ca.pem"),
    ])

    run([
        "openssl", "req",
        "-new",
        "-newkey", "rsa:2048",
        "-sha256",
        "-nodes",
        "-subj", "/CN=mysql",
        "-keyout", str(certificates / "server-key.pem"),
        "-out", str(directory / "server.csr"),
    ])

    extensions = directory / "server.ext"
    extensions.write_text(
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
        "subjectAltName=DNS:mysql\n"
        "subjectKeyIdentifier=hash\n"
        "authorityKeyIdentifier=keyid,issuer\n",
        encoding="utf-8",
    )

    run([
        "openssl", "x509",
        "-req",
        "-in", str(directory / "server.csr"),
        "-CA", str(certificates / "ca.pem"),
        "-CAkey", str(directory / "ca-key.pem"),
        "-CAcreateserial",
        "-days", "2",
        "-sha256",
        "-extfile", str(extensions),
        "-out", str(certificates / "server-cert.pem"),
    ])

    (certificates / "ca.pem").chmod(0o444)
    (certificates / "server-cert.pem").chmod(0o444)

    return certificates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--mysql-image", default="mysql:8.4")
    args = parser.parse_args()

    for executable in ("docker", "openssl"):
        if shutil.which(executable) is None:
            raise RuntimeError(f"Missing executable: {executable}")

    run(["docker", "info"])
    run(["docker", "image", "inspect", args.image])

    run([
        "docker", "run", "--rm",
        "--network", "none",
        args.image,
        "--check-files",
    ])

    prefix = f"flash-ticket-mysql-test-{secrets.token_hex(4)}"
    network = f"{prefix}-net"
    server = f"{prefix}-db"
    client = f"{prefix}-client"

    root_password = secrets.token_hex(16)
    app_password = secrets.token_hex(16) + ":@/%&'\\"
    wrong_root_password = secrets.token_hex(16)
    wrong_app_password = secrets.token_hex(16)

    for value in (
        root_password,
        app_password,
        wrong_root_password,
        wrong_app_password,
    ):
        SECRET_VALUES.add(value)
        SECRET_VALUES.add(quote(value, safe=""))

    print(f"Testing image: {args.image}", flush=True)
    print(f"Local test environment: {prefix}", flush=True)

    with tempfile.TemporaryDirectory(
        prefix="flash-ticket-mysql-tls-"
    ) as temporary:
        directory = Path(temporary)
        certificates = generate_certificates(directory)

        base_environment = {
            "MYSQL_HOST": "mysql",
            "MYSQL_PORT": "3306",
            "MYSQL_ADMIN_USER": "root",
            "MYSQL_ADMIN_PASSWORD": root_password,
            "MYSQL_APP_USER": "flash_app",
            "MYSQL_APP_PASSWORD": app_password,
            "MYSQL_SSL_CA": "/app/certs/local-test-ca.pem",
        }

        def run_client(
            arguments=(),
            *,
            python=False,
            overrides=None,
            check=True,
        ):
            environment = dict(base_environment)
            environment.update(overrides or {})

            command = [
                "docker", "run",
                "--rm",
                "--name", client,
                "--label", LABEL,
                "--network", network,
                "--mount",
                (
                    f"type=bind,source={certificates / 'ca.pem'},"
                    "target=/app/certs/local-test-ca.pem,readonly"
                ),
            ]

            for name in environment:
                command.extend(["--env", name])

            if python:
                command.extend(["--entrypoint", "python"])

            command.append(args.image)
            command.extend(arguments)

            return run(
                command,
                environment=environment,
                check=check,
            )

        def probe(mode, *, overrides=None, check=True):
            return run_client(
                ["-c", PROBE_CODE, mode],
                python=True,
                overrides=overrides,
                check=check,
            )

        def bootstrap(*, overrides=None, expect_success=True):
            result = run_client(
                overrides=overrides,
                check=False,
            )

            if not expect_success:
                if result.returncode == 0:
                    raise RuntimeError(
                        "Bootstrap unexpectedly accepted incorrect credentials."
                    )
                return None

            if result.returncode != 0:
                raise RuntimeError(
                    "Bootstrap failed:\n"
                    + redact(result.stdout + result.stderr)
                )

            report = parse_report(result)

            if report.get("status") != "ready":
                raise RuntimeError("Bootstrap did not report ready.")

            return report

        network_created = False

        try:
            run([
                "docker", "network", "create",
                "--internal",
                "--label", LABEL,
                network,
            ])
            network_created = True

            server_command = (
                "install -d -o mysql -g mysql -m 0700 /run/mysql-test-tls; "
                "install -o mysql -g mysql -m 0600 "
                "/certs/server-key.pem /run/mysql-test-tls/server-key.pem; "
                "exec docker-entrypoint.sh mysqld "
                "--require_secure_transport=ON "
                "--ssl-ca=/certs/ca.pem "
                "--ssl-cert=/certs/server-cert.pem "
                "--ssl-key=/run/mysql-test-tls/server-key.pem "
                "--innodb-buffer-pool-size=64M "
                "--innodb-redo-log-capacity=64M "
                "--performance-schema=OFF"
            )

            run([
                "docker", "run",
                "--detach",
                "--name", server,
                "--label", LABEL,
                "--network", network,
                "--network-alias", "mysql",
                "--network-alias", "mysql-wrong",
                "--memory", "1g",
                "--tmpfs", "/var/lib/mysql:rw,size=768m",
                "--mount",
                f"type=bind,source={certificates},target=/certs,readonly",
                "--env", "MYSQL_ROOT_PASSWORD",
                "--env", "MYSQL_ROOT_HOST",
                "--entrypoint", "bash",
                args.mysql_image,
                "-ec", server_command,
            ], environment={
                "MYSQL_ROOT_PASSWORD": root_password,
                "MYSQL_ROOT_HOST": "%",
            })

            last_result = None

            for attempt in range(1, 61):
                last_result = probe("ready", check=False)

                if last_result.returncode == 0:
                    version = parse_report(last_result)["version"]

                    if not version.startswith("8.4."):
                        raise RuntimeError(
                            f"Expected MySQL 8.4, received {version}"
                        )
                    break

                if attempt % 10 == 0:
                    print(
                        f"Waiting for MySQL: attempt {attempt}/60",
                        flush=True,
                    )

                time.sleep(1)
            else:
                raise RuntimeError(
                    "MySQL did not become ready:\n"
                    + redact(last_result.stdout + last_result.stderr)
                )

            print(f"PASS: MySQL {version} accepts verified TLS connections.")

            first = bootstrap()

            if (
                first["app_created"] is not True
                or first["migrations_applied"] < 1
                or first["tls"] is not True
            ):
                raise RuntimeError("Unexpected first bootstrap report.")

            print("PASS: first bootstrap creates schema and app user.")

            probe("clear_data")

            repeated = bootstrap()

            if (
                repeated["app_created"] is not False
                or repeated["migrations_applied"] != 0
            ):
                raise RuntimeError("Repeated bootstrap was not idempotent.")

            counts = parse_report(probe("counts"))

            if any(count != 0 for count in counts.values()):
                raise RuntimeError("Repeated bootstrap recreated seed rows.")

            print("PASS: repeated bootstrap skips migrations and seed.")

            bootstrap(
                overrides={
                    "MYSQL_APP_PASSWORD": wrong_app_password,
                },
                expect_success=False,
            )
            print("PASS: wrong app password is rejected.")

            bootstrap(
                overrides={
                    "MYSQL_ADMIN_PASSWORD": wrong_root_password,
                },
                expect_success=False,
            )
            print("PASS: wrong admin password is rejected.")

            hostname_report = parse_report(probe(
                "hostname_rejected",
                overrides={"MYSQL_HOST": "mysql-wrong"},
            ))

            if hostname_report.get("hostname_rejected") is not True:
                raise RuntimeError("Hostname verification check failed.")

            print("PASS: TLS rejects a certificate hostname mismatch.")

            bootstrap()
            print("PASS: original credentials still work.")

            permissions = parse_report(probe("permissions"))

            if permissions.get("permissions") != "verified":
                raise RuntimeError("Application permission checks failed.")

            print("PASS: app DML works; DDL, ledger and system access are denied.")
            print("All MySQL bootstrap integration checks passed.")

        except Exception:
            if network_created:
                logs = run(
                    ["docker", "logs", "--tail", "60", server],
                    check=False,
                )
                print(
                    redact(logs.stdout + logs.stderr),
                    file=sys.stderr,
                )
            raise

        finally:
            if network_created:
                for name in (client, server):
                    run(
                        [
                            "docker", "rm",
                            "--force", "--volumes",
                            name,
                        ],
                        check=False,
                    )

                result = run(
                    ["docker", "network", "rm", network],
                    check=False,
                )

                if result.returncode != 0:
                    print(
                        f"Cleanup warning: inspect network {network}",
                        file=sys.stderr,
                    )


if __name__ == "__main__":
    try:
        main()
    except (
        RuntimeError,
        OSError,
        ValueError,
        subprocess.TimeoutExpired,
    ) as error:
        print(f"ERROR: {redact(str(error))}", file=sys.stderr)
        sys.exit(1)