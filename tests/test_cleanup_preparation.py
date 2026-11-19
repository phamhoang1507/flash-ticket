"""Offline regression checks for Terraform cleanup plan authorization."""

from copy import deepcopy
import unittest

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


if __name__ == "__main__":
    unittest.main()
