import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, quote, unquote, urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location(
    "mongodb_provisioner",
    PROJECT_ROOT / "scripts" / "provision_mongodb.py",
)

module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class OfflineRunner(module.Runner):
    def command(self, arguments, *, input_text=None):
        raise AssertionError("External process calls are forbidden in this test.")


class FakeSsmRunner(OfflineRunner):
    def __init__(self, existing_uri=None, parameter_type="SecureString"):
        super().__init__()
        self.existing_uri = existing_uri
        self.parameter_type = parameter_type
        self.writes = []

    def aws(self, *arguments, request=None):
        operation = arguments[:2]

        if operation == ("ssm", "get-parameters"):
            parameters = []

            if self.existing_uri is not None:
                parameters.append({
                    "Name": module.CATALOG_URI_PARAMETER,
                    "Type": self.parameter_type,
                    "Value": self.existing_uri,
                })

            return {"Parameters": parameters}

        if operation == ("ssm", "put-parameter"):
            self.writes.append(request)
            return {"Version": 2}

        raise AssertionError(f"Unexpected AWS operation: {operation}")


class MongoProvisionerTests(unittest.TestCase):
    def test_password_special_characters_are_url_encoded(self):
        password = "test-only:@/%&value"

        uri = module.build_uri(
            {
                "private_ip": "10.42.20.10",
                "private_dns": "ip-10-42-20-10.ec2.internal",
            },
            {
                "database": "flash_ticket",
                "app_username": "flash_catalog",
                "app_password": password,
            },
        )

        parsed = urlsplit(uri)
        query = parse_qs(parsed.query)

        self.assertEqual(unquote(parsed.password), password)
        self.assertEqual(parsed.hostname, "ip-10-42-20-10.ec2.internal")
        self.assertEqual(query["authSource"], ["flash_ticket"])
        self.assertEqual(query["retryWrites"], ["false"])

    def test_failed_bootstrap_does_not_publish_uri(self):
        runner = OfflineRunner()

        with (
            patch.object(module, "load_metadata", return_value={
                "private_ip": "10.42.20.10",
            }),
            patch.object(module, "load_passwords", return_value={
                "root_password": "r" * 32,
                "app_password": "a" * 32,
            }),
            patch.object(
                module,
                "bootstrap_remote",
                side_effect=module.ProvisionError("Bootstrap failed."),
            ),
            patch.object(module, "save_uri") as publish,
        ):
            with self.assertRaises(module.ProvisionError):
                module.provision(
                    runner,
                    "flash_ticket",
                    "flash_admin",
                    "flash_catalog",
                )

            publish.assert_not_called()

    def test_unchanged_uri_does_not_create_a_new_version(self):
        uri = "mongodb://test-user:test-password@10.42.20.10/flash_ticket"
        runner = FakeSsmRunner(existing_uri=uri)

        result = module.save_uri(runner, uri)

        self.assertEqual(result, "unchanged")
        self.assertEqual(runner.writes, [])

    def test_changed_uri_is_written_as_secure_string(self):
        runner = FakeSsmRunner(existing_uri="mongodb://old-host/flash_ticket")

        result = module.save_uri(
            runner,
            "mongodb://new-host/flash_ticket",
        )

        self.assertEqual(result, "updated")
        self.assertEqual(len(runner.writes), 1)
        self.assertEqual(runner.writes[0]["Type"], "SecureString")
        self.assertTrue(runner.writes[0]["Overwrite"])

    def test_redaction_covers_raw_and_url_encoded_passwords(self):
        runner = OfflineRunner()
        password = "test-only:@/%&value"

        runner.remember(password)

        message = runner.redact(
            f"raw={password}; encoded={quote(password, safe='')}"
        )

        self.assertNotIn(password, message)
        self.assertNotIn(quote(password, safe=""), message)


if __name__ == "__main__":
    unittest.main()