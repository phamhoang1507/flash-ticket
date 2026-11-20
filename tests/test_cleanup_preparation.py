"""Offline regression checks for Terraform cleanup plan authorization."""

from copy import deepcopy
from argparse import Namespace
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import aws_dev_power as power
from scripts import deploy_dev as deploy
from scripts.deploy_dev import BOOTSTRAP, FOUNDATION, DeployError, cleanup_targets, validate_cleanup_plan


S3_ADDRESS = "module.assets.aws_s3_bucket.this"
ECR_ADDRESS = 'module.ecr.aws_ecr_repository.this["booking"]'
TARGETS = {
    S3_ADDRESS: "aws_s3_bucket",
    ECR_ADDRESS: "aws_ecr_repository",
}


def resource_change(address, resource_type, before, after, actions=None):
    return {
        "address": address,
        "mode": "managed",
        "type": resource_type,
        "name": "this",
        "provider_name": "registry.terraform.io/hashicorp/aws",
        "change": {
            "actions": ["update"] if actions is None else actions,
            "before": before,
            "after": after,
            "after_unknown": {},
        },
    }


def s3_change():
    before = {
        "id": "flash-ticket-dev-assets-123456789012-us-east-1",
        "bucket": "flash-ticket-dev-assets-123456789012-us-east-1",
        "arn": "arn:aws:s3:::flash-ticket-dev-assets-123456789012-us-east-1",
        "region": "us-east-1",
        "tags": {"Project": "flash-ticket", "Environment": "dev"},
        "force_destroy": False,
    }
    after = deepcopy(before)
    after["force_destroy"] = True
    return resource_change(S3_ADDRESS, "aws_s3_bucket", before, after)


def ecr_change():
    before = {
        "id": "flash-ticket-dev/booking",
        "name": "flash-ticket-dev/booking",
        "arn": (
            "arn:aws:ecr:us-east-1:123456789012:"
            "repository/flash-ticket-dev/booking"
        ),
        "tags": {"Project": "flash-ticket", "Environment": "dev"},
        "force_delete": False,
    }
    after = deepcopy(before)
    after["force_delete"] = True
    return resource_change(ECR_ADDRESS, "aws_ecr_repository", before, after)


def plan(*changes):
    return {
        "format_version": "1.2",
        "terraform_version": "1.11.0",
        "resource_changes": list(changes),
    }


class CleanupPlanAuthorizationTests(unittest.TestCase):
    def test_accepts_only_the_expected_bucket_and_repository_force_flags(self):
        validate_cleanup_plan(plan(s3_change(), ecr_change()), TARGETS)

    def test_accepts_idempotent_flags_and_unrelated_managed_no_ops(self):
        bucket = s3_change()
        bucket["change"]["before"]["force_destroy"] = True
        bucket["change"]["actions"] = ["no-op"]
        unchanged = {"id": "EDISTRIBUTION", "comment": "Existing frontend"}
        distribution = resource_change(
            "module.web_auth.aws_cloudfront_distribution.this",
            "aws_cloudfront_distribution",
            deepcopy(unchanged),
            deepcopy(unchanged),
            ["no-op"],
        )
        validate_cleanup_plan(plan(bucket, distribution), {S3_ADDRESS: "aws_s3_bucket"})

    def test_accepts_read_only_data_and_empty_unknown_masks(self):
        bucket = s3_change()
        bucket["change"]["after_unknown"] = {"tags": {}, "tags_all": {}}
        identity = resource_change(
            "data.aws_caller_identity.current",
            "aws_caller_identity",
            None,
            {"account_id": "123456789012"},
            ["read"],
        )
        identity["mode"] = "data"
        identity["change"]["after_unknown"] = {"id": True}
        validate_cleanup_plan(plan(bucket, identity), {S3_ADDRESS: "aws_s3_bucket"})

    def test_rejects_unconfirmed_targets_and_false_selected_no_ops(self):
        with self.assertRaises(DeployError):
            validate_cleanup_plan(plan(s3_change()), TARGETS)
        bucket = s3_change()
        bucket["change"]["after"]["force_destroy"] = False
        bucket["change"]["actions"] = ["no-op"]
        with self.assertRaises(DeployError):
            validate_cleanup_plan(plan(bucket), {S3_ADDRESS: "aws_s3_bucket"})

    def test_targets_only_existing_known_bucket_and_repository_addresses(self):
        addresses = [S3_ADDRESS, ECR_ADDRESS,
                     "module.web_auth.aws_cloudfront_distribution.this",
                     "module.other.aws_s3_bucket.this"]
        self.assertEqual(cleanup_targets(FOUNDATION, addresses), TARGETS)
        self.assertEqual(cleanup_targets(FOUNDATION, []), {})
        self.assertEqual(cleanup_targets(BOOTSTRAP, ["aws_s3_bucket.terraform_state"]),
                         {"aws_s3_bucket.terraform_state": "aws_s3_bucket"})

    def test_rejects_create_delete_and_both_replacement_orders(self):
        for actions in (
            ["create"],
            ["delete"],
            ["delete", "create"],
            ["create", "delete"],
        ):
            with self.subTest(actions=actions):
                bucket = s3_change()
                bucket["change"]["actions"] = actions
                with self.assertRaises(DeployError):
                    validate_cleanup_plan(plan(bucket), {S3_ADDRESS: "aws_s3_bucket"})

    def test_rejects_cloudfront_updates_even_alongside_an_allowed_bucket(self):
        distribution = resource_change(
            "module.web_auth.aws_cloudfront_distribution.this",
            "aws_cloudfront_distribution",
            {"id": "EDISTRIBUTION", "comment": "Old configuration"},
            {"id": "EDISTRIBUTION", "comment": "Unreviewed configuration"},
        )
        with self.assertRaises(DeployError):
            validate_cleanup_plan(plan(s3_change(), distribution), {S3_ADDRESS: "aws_s3_bucket"})

    def test_rejects_a_bucket_outside_the_exact_target_addresses(self):
        bucket = s3_change()
        bucket["address"] = "module.other_application.aws_s3_bucket.this"
        with self.assertRaises(DeployError):
            validate_cleanup_plan(plan(bucket), {S3_ADDRESS: "aws_s3_bucket"})

    def test_rejects_identity_configuration_or_tag_changes_with_the_flag(self):
        for field, value in (
            ("id", "another-bucket"),
            ("bucket", "another-bucket"),
            ("arn", "arn:aws:s3:::another-bucket"),
            ("region", "us-west-2"),
            ("tags", {"Project": "another-project"}),
        ):
            with self.subTest(field=field):
                bucket = s3_change()
                bucket["change"]["after"][field] = value
                with self.assertRaises(DeployError):
                    validate_cleanup_plan(plan(bucket), {S3_ADDRESS: "aws_s3_bucket"})

        repository = ecr_change()
        repository["change"]["after"]["name"] = "another-project/booking"
        with self.assertRaises(DeployError):
            validate_cleanup_plan(plan(repository), {ECR_ADDRESS: "aws_ecr_repository"})

    def test_rejects_false_or_non_boolean_force_settings(self):
        for value in (False, None, "true", 1):
            with self.subTest(value=value):
                bucket = s3_change()
                bucket["change"]["after"]["force_destroy"] = value
                with self.assertRaises(DeployError):
                    validate_cleanup_plan(plan(bucket), {S3_ADDRESS: "aws_s3_bucket"})

    def test_rejects_unknown_values_that_could_hide_other_changes(self):
        for mask in (
            {"bucket": True},
            {"tags": {"Project": True}},
            {"force_destroy": True},
        ):
            with self.subTest(mask=mask):
                bucket = s3_change()
                bucket["change"]["after_unknown"] = mask
                with self.assertRaises(DeployError):
                    validate_cleanup_plan(plan(bucket), {S3_ADDRESS: "aws_s3_bucket"})

    def test_rejects_malformed_plans_type_mismatches_and_managed_reads(self):
        invalid_documents = [
            {},
            {"resource_changes": "invalid"},
            plan({"address": S3_ADDRESS}),
        ]
        for field in ("address", "type", "mode", "change"):
            bucket = s3_change()
            del bucket[field]
            invalid_documents.append(plan(bucket))
        for field in ("actions", "before", "after", "after_unknown"):
            bucket = s3_change()
            del bucket["change"][field]
            invalid_documents.append(plan(bucket))
        bucket = s3_change()
        bucket["type"] = "aws_ecr_repository"
        invalid_documents.append(plan(bucket))
        bucket = s3_change()
        bucket["change"]["actions"] = ["read"]
        invalid_documents.append(plan(bucket))
        for index, document in enumerate(invalid_documents):
            with self.subTest(case=index):
                with self.assertRaises(DeployError):
                    validate_cleanup_plan(document, {S3_ADDRESS: "aws_s3_bucket"})


class OperationalSafetyTests(unittest.TestCase):
    def test_expired_login_has_actionable_commands_and_permission_errors_do_not(self):
        result = subprocess.CompletedProcess(
            ["aws"], 255, stdout="", stderr="Error when retrieving credentials from login: Your login session has expired.")
        with patch.object(deploy.subprocess, "run", return_value=result):
            with self.assertRaises(DeployError) as caught:
                deploy.identity()
        self.assertIn("aws login --profile flash-ticket-dev", str(caught.exception))
        self.assertIn("aws sts get-caller-identity --profile flash-ticket-terraform", str(caught.exception))
        self.assertNotIn("aws login", deploy.auth_error_hint("AccessDenied: permission is missing"))

    def test_ecr_only_treats_the_explicit_image_not_found_response_as_missing(self):
        repository = "123456789012.dkr.ecr.us-east-1.amazonaws.com/flash-ticket-dev/booking"
        with patch.object(deploy, "aws", return_value={"images": [], "failures": [{"failureCode": "ImageNotFound"}]}):
            self.assertFalse(deploy.ecr_image_exists(repository, "dev-1.0.0-abcdef1"))
        with patch.object(deploy, "aws", side_effect=DeployError("AccessDenied")):
            with self.assertRaisesRegex(DeployError, "AccessDenied"):
                deploy.ecr_image_exists(repository, "dev-1.0.0-abcdef1")

    def test_stop_uses_only_tracked_ecs_api_calls_and_never_applies_terraform(self):
        names = {name: f"flash-ticket-dev-{name}" for name in deploy.SERVICE_NAMES}
        runtime = {"ecs": {"cluster_name": "flash-ticket-dev", "service_names": names}}
        described = {"services": [{"serviceName": name, "status": "ACTIVE"} for name in names.values()]}
        with patch.object(deploy, "outputs", return_value=runtime), \
                patch.object(deploy, "aws", side_effect=[described, {}, {}, {}, {}, {}]) as aws, \
                patch.object(deploy, "flags"), patch.object(deploy, "terraform") as terraform, \
                redirect_stdout(io.StringIO()):
            deploy.stop(Namespace())
        terraform.assert_not_called()
        updates = [call.args for call in aws.call_args_list if call.args[:2] == ("ecs", "update-service")]
        self.assertEqual(len(updates), 4)
        self.assertEqual({arguments[arguments.index("--service") + 1] for arguments in updates}, set(names.values()))
        self.assertTrue(all(arguments[-2:] == ("--desired-count", "0") for arguments in updates))

    def test_stop_waits_for_an_already_draining_service_without_updating_it(self):
        names = {name: f"flash-ticket-dev-{name}" for name in deploy.SERVICE_NAMES}
        services = [{"serviceName": name, "status": "INACTIVE"} for name in names.values()]
        services[0]["status"] = "DRAINING"
        with patch.object(deploy, "outputs", return_value={"ecs": {"cluster_name": "dev", "service_names": names}}), \
                patch.object(deploy, "aws", side_effect=[{"services": services}, {}]) as aws, \
                patch.object(deploy, "flags"), redirect_stdout(io.StringIO()):
            deploy.stop(Namespace())
        self.assertEqual(aws.call_args_list[1].args[:3], ("ecs", "wait", "services-inactive"))

    def test_cancelled_runtime_destroy_does_not_save_disabled_startup_flags(self):
        arguments = Namespace(yes=False, all=False, bootstrap_bucket=False)
        with patch.object(deploy, "identity"), \
                patch.object(deploy, "state_addresses", return_value=["module.data_layer[0].aws_db_instance.mysql"]), \
                patch.object(deploy, "terraform", side_effect=DeployError("Destroy cancelled")) as terraform, \
                patch.object(deploy, "flags") as flags:
            with self.assertRaisesRegex(DeployError, "cancelled"):
                deploy.destroy(arguments)
        flags.assert_not_called()
        self.assertIn("-var=enable_data_layer=false", terraform.call_args.args)
        self.assertIn("-var=enable_compute=false", terraform.call_args.args)

    def test_runtime_flags_are_saved_only_after_a_successful_destroy(self):
        events = []
        arguments = Namespace(yes=False, all=False, bootstrap_bucket=False)
        with patch.object(deploy, "identity"), \
                patch.object(deploy, "state_addresses", return_value=["module.data_layer[0].aws_db_instance.mysql"]), \
                patch.object(deploy, "terraform", side_effect=lambda *args, **kwargs: events.append("destroy")), \
                patch.object(deploy, "flags", side_effect=lambda **kwargs: events.append("flags")), \
                redirect_stdout(io.StringIO()):
            deploy.destroy(arguments)
        self.assertEqual(events, ["destroy", "flags"])


@unittest.skipUnless(shutil.which("git"), "Image provenance checks require the local git executable")
class ImageSourceProvenanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="flash-ticket-image-inputs-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.environment = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                                GIT_TERMINAL_PROMPT="0", GIT_TEMPLATE_DIR="")
        self.environment_patch = patch.dict(os.environ, self.environment)
        self.environment_patch.start()
        self.addCleanup(self.environment_patch.stop)
        self.root_patch = patch.object(deploy, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.log_patch = patch.object(power, "announce")
        self.log_patch.start()
        self.addCleanup(self.log_patch.stop)
        self.files = (
            ".dockerignore", "shared/auth.py", "services/booking/app/main.py",
            "services/booking/requirements.txt", "services/catalog/Dockerfile",
            "scripts/seed_aws_catalog.py", "scripts/reconcile_inventory.py",
            "assets/demo-banner.svg", "database/001_schema.sql",
            "tools/mysql-bootstrap/bootstrap.py", "tools/mysql-bootstrap/requirements.txt",
        )
        self.git("init", "--quiet")
        for name in self.files:
            self.write(name, "original\n")
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "test: record image inputs")
        self.tag = "dev-1.0.0-" + self.git("rev-parse", "--short=12", "HEAD").strip()

    def git(self, *arguments):
        result = subprocess.run(
            ["git", "-c", "user.name=Offline Test", "-c", "user.email=offline@example.invalid",
             "-c", "core.hooksPath=/dev/null", *arguments],
            cwd=self.root, env=self.environment, capture_output=True, text=True, check=True)
        return result.stdout

    def write(self, name, contents):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)

    def test_documentation_and_operational_script_commits_reuse_the_existing_image(self):
        for name in ("docs/README.md", "scripts/aws_dev_power.py", "services/booking/README.md"):
            self.write(name, "Operational documentation only\n")
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "docs: update deployment instructions")
        self.assertFalse(power.image_inputs_changed(self.tag))

    def test_changes_to_any_real_docker_input_require_new_images(self):
        for name in self.files:
            with self.subTest(input=name):
                self.write(name, "updated\n")
                self.assertTrue(power.image_inputs_changed(self.tag))
                self.write(name, "original\n")

    def test_a_new_untracked_application_module_requires_a_committed_build(self):
        self.write("services/worker/app/new_module.py", "new source\n")
        self.assertTrue(power.image_inputs_changed(self.tag))

    def test_unavailable_image_commit_is_not_silently_assumed_to_match(self):
        self.assertTrue(power.image_inputs_changed("dev-1.0.0-ffffffffffff"))


if __name__ == "__main__":
    unittest.main()
