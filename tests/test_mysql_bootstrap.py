from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, "/app")

import bootstrap


class MigrationTests(unittest.TestCase):
    def test_applied_migration_is_not_pending(self):
        migration = bootstrap.Migration(
            "001_schema.sql",
            "a" * 64,
            "SELECT 1;",
        )

        pending = bootstrap.pending_migrations(
            [migration],
            {"001_schema.sql": "a" * 64},
        )

        self.assertEqual(pending, [])

    def test_changed_applied_migration_is_rejected(self):
        migration = bootstrap.Migration(
            "001_schema.sql",
            "b" * 64,
            "SELECT 2;",
        )

        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.pending_migrations(
                [migration],
                {"001_schema.sql": "a" * 64},
            )

    def test_missing_applied_migration_is_rejected(self):
        migration = bootstrap.Migration(
            "002_add_index.sql",
            "b" * 64,
            "SELECT 2;",
        )

        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.pending_migrations(
                [migration],
                {"001_schema.sql": "a" * 64},
            )

    def test_delimiter_directive_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "001_schema.sql").write_text(
                "DELIMITER $$\nSELECT 1$$\n",
                encoding="utf-8",
            )

            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap.load_migrations(path)


class GrantTests(unittest.TestCase):
    def test_expected_table_privileges_are_accepted(self):
        bootstrap.validate_app_grants([
            "GRANT USAGE ON *.* TO `flash_app`@`%`",
            (
                "GRANT SELECT, INSERT, UPDATE, DELETE "
                "ON `flash_ticket`.`bookings` TO `flash_app`@`%`"
            ),
        ])

    def test_database_wide_privileges_are_rejected(self):
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.validate_app_grants([
                "GRANT ALL PRIVILEGES ON `flash_ticket`.* TO `flash_app`@`%`",
            ])

    def test_grant_option_is_rejected(self):
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.validate_app_grants([
                (
                    "GRANT SELECT ON `flash_ticket`.`bookings` "
                    "TO `flash_app`@`%` WITH GRANT OPTION"
                ),
            ])

    def test_role_grants_are_rejected(self):
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.validate_app_grants([
                "GRANT `rds_superuser_role`@`%` TO `flash_app`@`%`",
            ])


if __name__ == "__main__":
    unittest.main()