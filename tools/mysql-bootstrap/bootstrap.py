#!/usr/bin/env python3

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import sys

import pymysql
from pymysql.constants import CLIENT


DATABASE = "flash_ticket"
APP_HOST = "%"
TABLES = ("inventory", "bookings", "outbox")
PRIVILEGES = {"SELECT", "INSERT", "UPDATE", "DELETE"}

MIGRATION_DIRECTORY = Path("/app/migrations")
DEFAULT_CA = "/app/certs/global-bundle.pem"

STAGE = "configuration"


class BootstrapError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    name: str
    checksum: str
    sql: str


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    admin_user: str
    admin_password: str
    app_user: str
    app_password: str
    ca_file: str

    @classmethod
    def from_environment(cls):
        required = {}

        for name in (
            "MYSQL_HOST",
            "MYSQL_ADMIN_PASSWORD",
            "MYSQL_APP_PASSWORD",
        ):
            value = os.environ.get(name)

            if not value:
                raise BootstrapError(f"Missing environment variable: {name}")

            required[name] = value

        admin_user = os.environ.get("MYSQL_ADMIN_USER", "flash_admin")
        app_user = os.environ.get("MYSQL_APP_USER", "flash_app")

        for username in (admin_user, app_user):
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", username):
                raise BootstrapError("Invalid MySQL username.")

        if admin_user == app_user:
            raise BootstrapError("Admin and application usernames must differ.")

        for name in ("MYSQL_ADMIN_PASSWORD", "MYSQL_APP_PASSWORD"):
            if not 16 <= len(required[name]) <= 128:
                raise BootstrapError(f"Invalid password length: {name}")

        port = int(os.environ.get("MYSQL_PORT", "3306"))

        if not 1 <= port <= 65535:
            raise BootstrapError("Invalid MySQL port.")

        return cls(
            host=required["MYSQL_HOST"],
            port=port,
            admin_user=admin_user,
            admin_password=required["MYSQL_ADMIN_PASSWORD"],
            app_user=app_user,
            app_password=required["MYSQL_APP_PASSWORD"],
            ca_file=os.environ.get("MYSQL_SSL_CA", DEFAULT_CA),
        )


def load_migrations(directory):
    migrations = []
    numbers = set()

    for path in sorted(directory.glob("*.sql")):
        match = re.fullmatch(r"([0-9]{3})_[A-Za-z0-9_]+\.sql", path.name)

        if not match:
            raise BootstrapError(f"Invalid migration filename: {path.name}")

        number = match.group(1)

        if number in numbers:
            raise BootstrapError(f"Duplicate migration number: {number}")

        numbers.add(number)

        sql = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")

        if not sql.strip():
            raise BootstrapError(f"Empty migration: {path.name}")

        if re.search(r"^\s*DELIMITER\b", sql, flags=re.I | re.M):
            raise BootstrapError(
                f"DELIMITER client directives are unsupported: {path.name}"
            )

        migrations.append(Migration(
            name=path.name,
            checksum=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            sql=sql,
        ))

    if not migrations:
        raise BootstrapError("No migrations found.")

    return migrations


def pending_migrations(migrations, applied):
    source = {migration.name: migration for migration in migrations}

    if set(applied) - set(source):
        raise BootstrapError("An applied migration is missing from the image.")

    for name, checksum in applied.items():
        if source[name].checksum != checksum:
            raise BootstrapError(f"Applied migration checksum changed: {name}")

    pending = [
        migration
        for migration in migrations
        if migration.name not in applied
    ]

    if applied and any(
        migration.name < max(applied)
        for migration in pending
    ):
        raise BootstrapError("Out-of-order migrations are not allowed.")

    return pending


def validate_app_grants(grants):
    allowed_targets = {
        f"`{DATABASE}`.`{table}`"
        for table in TABLES
    }

    for grant in grants:
        if "WITH GRANT OPTION" in grant.upper():
            raise BootstrapError("Application user has GRANT OPTION.")

        match = re.match(r"^GRANT (.+?) ON (.+?) TO ", grant)

        if not match:
            raise BootstrapError(
                "Application user has unexpected role or proxy grants."
            )

        privileges = {
            value.strip()
            for value in match.group(1).split(",")
        }
        target = match.group(2)

        if target == "*.*" and privileges == {"USAGE"}:
            continue

        if (
            target not in allowed_targets
            or not privileges.issubset(PRIVILEGES)
        ):
            raise BootstrapError(
                "Application user has privileges outside the expected scope."
            )


def connect(settings, *, admin):
    context = ssl.create_default_context(cafile=settings.ca_file)
    context.minimum_version = ssl.TLSVersion.TLSv1_2

    connection = pymysql.connect(
        host=settings.host,
        port=settings.port,
        user=settings.admin_user if admin else settings.app_user,
        password=settings.admin_password if admin else settings.app_password,
        charset="utf8mb4",
        ssl=context,
        autocommit=True,
        connect_timeout=5,
        read_timeout=60,
        write_timeout=60,
        client_flag=CLIENT.MULTI_STATEMENTS if admin else 0,
        init_command="SET time_zone = '+00:00'",
    )

    try:
        with connection.cursor() as cursor:
            cursor.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
            row = cursor.fetchone()

            if not row or not row[1]:
                raise BootstrapError("MySQL connection is not using TLS.")

            if not admin:
                cursor.execute("SELECT CURRENT_USER()")
                identity = cursor.fetchone()[0]

                if identity != f"{settings.app_user}@{APP_HOST}":
                    raise BootstrapError(
                        "Application connected through an unexpected account."
                    )

        return connection

    except Exception:
        connection.close()
        raise


def bootstrap(settings, migrations):
    global STAGE

    STAGE = "connect_admin"
    connection = connect(settings, admin=True)
    lock_acquired = False

    try:
        with connection.cursor() as cursor:
            STAGE = "acquire_lock"

            cursor.execute(
                "SELECT GET_LOCK(%s, 30)",
                ("flash-ticket:mysql-bootstrap",),
            )

            if cursor.fetchone()[0] != 1:
                raise BootstrapError("Could not acquire the bootstrap lock.")

            lock_acquired = True

            STAGE = "check_existing_app_user"

            cursor.execute(
                "SELECT COUNT(*) FROM mysql.user WHERE User = %s AND Host = %s",
                (settings.app_user, APP_HOST),
            )
            app_exists = cursor.fetchone()[0] > 0

            if app_exists:
                app_connection = connect(settings, admin=False)
                app_connection.close()

                cursor.execute(
                    "SHOW GRANTS FOR %s@%s",
                    (settings.app_user, APP_HOST),
                )

                validate_app_grants([
                    row[0] for row in cursor.fetchall()
                ])

            STAGE = "prepare_migration_ledger"

            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{DATABASE}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin"
            )

            connection.select_db(DATABASE)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS `_schema_migrations` (
                    version VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin
                        NOT NULL PRIMARY KEY,
                    checksum CHAR(64) CHARACTER SET ascii COLLATE ascii_bin
                        NOT NULL,
                    applied_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                ) ENGINE=InnoDB
            """)

            cursor.execute(
                "SELECT version, checksum FROM `_schema_migrations`"
            )
            applied = dict(cursor.fetchall())
            pending = pending_migrations(migrations, applied)

            for migration in pending:
                STAGE = f"migration:{migration.name}"

                cursor.execute(migration.sql)

                while cursor.nextset():
                    pass

                cursor.execute(
                    "INSERT INTO `_schema_migrations` (version, checksum) "
                    "VALUES (%s, %s)",
                    (migration.name, migration.checksum),
                )

            STAGE = "verify_application_tables"

            for table in TABLES:
                cursor.execute(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = %s AND table_name = %s",
                    (DATABASE, table),
                )

                if cursor.fetchone() is None:
                    raise BootstrapError(f"Required table is missing: {table}")

            STAGE = "configure_application_user"

            if not app_exists:
                cursor.execute(
                    "CREATE USER %s@%s IDENTIFIED BY %s REQUIRE SSL",
                    (
                        settings.app_user,
                        APP_HOST,
                        settings.app_password,
                    ),
                )
            else:
                cursor.execute(
                    "ALTER USER %s@%s REQUIRE SSL",
                    (settings.app_user, APP_HOST),
                )

            for table in TABLES:
                cursor.execute(
                    f"GRANT SELECT, INSERT, UPDATE, DELETE "
                    f"ON `{DATABASE}`.`{table}` TO %s@%s",
                    (settings.app_user, APP_HOST),
                )

            STAGE = "verify_application_access"

            with connect(settings, admin=False) as app_connection:
                with app_connection.cursor() as app_cursor:
                    for table in TABLES:
                        app_cursor.execute(
                            f"SELECT 1 FROM `{DATABASE}`.`{table}` LIMIT 0"
                        )

            return {
                "status": "ready",
                "database": DATABASE,
                "app_user": settings.app_user,
                "app_created": not app_exists,
                "migrations_applied": len(pending),
                "tls": True,
            }

    finally:
        if lock_acquired:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT RELEASE_LOCK(%s)",
                        ("flash-ticket:mysql-bootstrap",),
                    )
            except pymysql.MySQLError:
                pass

        connection.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-files", action="store_true")
    args = parser.parse_args()

    try:
        migrations = load_migrations(MIGRATION_DIRECTORY)

        if args.check_files:
            ssl.create_default_context(cafile=DEFAULT_CA)
            print(f"Files verified: {len(migrations)} migration(s), CA bundle valid.")
            return

        settings = Settings.from_environment()
        report = bootstrap(settings, migrations)
        print(json.dumps(report))

    except BootstrapError as error:
        print(
            f"BOOTSTRAP_FAILED stage={STAGE}: {error}",
            file=sys.stderr,
        )
        sys.exit(1)

    except pymysql.MySQLError as error:
        code = error.args[0] if error.args else "unknown"
        print(
            f"BOOTSTRAP_FAILED stage={STAGE} mysql_code={code}",
            file=sys.stderr,
        )
        sys.exit(1)

    except (OSError, ValueError):
        print(
            f"BOOTSTRAP_FAILED stage={STAGE}: configuration or file error",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()