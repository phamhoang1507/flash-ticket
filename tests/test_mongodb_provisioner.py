import ast
import importlib.util
import json
from pathlib import Path
import re
import stat
import sys
import tempfile
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

recovery_spec = importlib.util.spec_from_file_location(
    "mongodb_repair", PROJECT_ROOT / "scripts" / "repair_mongodb_bootstrap.py"
)
recovery_module = importlib.util.module_from_spec(recovery_spec)
recovery_spec.loader.exec_module(recovery_module)

bake_spec = importlib.util.spec_from_file_location(
    "mongodb_bake", PROJECT_ROOT / "scripts" / "bake_mongo_ami.py"
)
bake_module = importlib.util.module_from_spec(bake_spec)
bake_spec.loader.exec_module(bake_module)


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

    def test_redaction_covers_json_escaped_password(self):
        runner = OfflineRunner()
        password = 'test-only:"\\secret-é'
        escaped = json.dumps(password, ensure_ascii=True)[1:-1]
        runner.remember(password)

        self.assertNotIn(escaped, runner.redact("value=" + escaped))

    def test_non_rfc1918_addresses_are_rejected(self):
        for address in ("127.0.0.1", "169.254.169.254", "8.8.8.8", "::1", None):
            with self.subTest(address=address), self.assertRaises(module.ProvisionError):
                module.private_address(address)

    def test_private_dns_must_match_the_instance_address(self):
        payload = {
            "database": "flash_ticket",
            "app_username": "flash_catalog",
            "app_password": "test-only-password",
        }
        for hostname in (
            "ip-10-42-20-11.ec2.internal", "mongodb.example.com", ["invalid"],
        ):
            with self.subTest(hostname=hostname), self.assertRaises(module.ProvisionError):
                module.build_uri(
                    {"private_ip": "10.42.20.10", "private_dns": hostname}, payload
                )

    def test_invalid_instance_id_type_is_rejected(self):
        runner = OfflineRunner()
        with patch.object(runner, "command", return_value=json.dumps({
            "instance_id": 123, "private_ip": "10.42.20.10",
        })):
            with self.assertRaises(module.ProvisionError):
                module.load_metadata(runner)

    def test_sensitive_aws_request_uses_a_private_temporary_file(self):
        runner = module.Runner()
        request = {"Name": "test-parameter", "Value": "test-only-secret"}
        paths = []

        def inspect_request(arguments, **_options):
            position = arguments.index("--cli-input-json") + 1
            path = Path(unquote(urlsplit(arguments[position]).path))
            paths.append(path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), request)
            self.assertNotIn(request["Value"], " ".join(arguments))
            return '{"Version": 1}'

        with patch.object(runner, "command", side_effect=inspect_request):
            self.assertEqual(runner.aws("ssm", "put-parameter", request=request), {"Version": 1})
        self.assertEqual(len(paths), 1)
        self.assertFalse(paths[0].exists())

    def test_repair_does_not_reset_a_different_failed_module(self):
        definition = next(
            node for node in ast.parse(recovery_module.REMOTE_SCRIPT).body
            if isinstance(node, ast.FunctionDef) and node.name == "scripts_user_failure"
        )
        namespace = {"re": re}
        exec(compile(ast.Module(body=[definition], type_ignores=[]), "<offline-guard>", "exec"), namespace)
        guard = namespace["scripts_user_failure"]

        self.assertTrue(guard("('scripts_user', RuntimeError('Runparts: 1 failures'))"))
        self.assertFalse(guard("('other_module', RuntimeError('mentions scripts_user'))"))
        self.assertFalse(guard("unstructured error mentions scripts_user"))
        self.assertFalse(guard(None))

    def test_stopped_builder_requires_a_finalizer_record(self):
        instance_id = "i-0123456789abcdef0"
        account_id = "123456789012"
        details = {
            "ImageId": "ami-0123456789abcdef0",
            "State": {"Name": "stopped"},
            "Tags": [
                {"Key": "Project", "Value": "flash-ticket"},
                {"Key": "Environment", "Value": "dev"},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proof_path = root / "output" / f"mongo-ami-{instance_id}.validated.json"
            proof_path.parent.mkdir()
            proof_path.write_text(json.dumps({
                "InstanceId": instance_id,
                "AccountId": account_id,
                "BaseImageId": details["ImageId"],
                "ValidationCommandId": "12345678-1234-1234-1234-123456789abc",
            }), encoding="utf-8")
            with (
                patch.object(bake_module, "command", return_value={"instance_id": instance_id}),
                patch.object(bake_module, "instance", return_value=details),
                patch.object(bake_module, "existing_image", return_value=None),
                patch.object(bake_module, "invocation", return_value={
                    "Status": "Success", "ResponseCode": 0,
                }),
                patch.object(bake_module, "aws", return_value={"Account": account_id}) as aws,
            ):
                with self.assertRaisesRegex(bake_module.BakeError, "finalization record"):
                    bake_module.bake(root)
                aws.assert_called_once_with(root, "sts", "get-caller-identity")


if __name__ == "__main__":
    unittest.main()
