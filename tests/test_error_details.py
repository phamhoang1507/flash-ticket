from copy import deepcopy
import unittest

from shared.errors import failure_details


class AWSFailure(RuntimeError):
    def __init__(self, response, operation="SendEmail"):
        super().__init__("raw AWS error containing buyer@example.com and secret data")
        self.response = response
        self.operation_name = operation


class FailureDetailsTests(unittest.TestCase):
    def setUp(self):
        self.response = {
            "Error": {
                "Code": "MessageRejected",
                "Message": "Email buyer@example.com is not verified. Password=secret",
            },
            "ResponseMetadata": {
                "RequestId": "a64d6b07-0d45-4c69-a7d9-b86b4838f036",
                "HTTPStatusCode": 400,
                "HTTPHeaders": {"authorization": "Bearer secret-token"},
            },
            "RequestParameters": {
                "Body": "https://example.com/ticket?X-Amz-Signature=secret-signature",
            },
        }

    def test_ses_rejection_is_diagnosable_without_recipient_or_payload(self):
        summary = failure_details(AWSFailure(self.response))
        for field in (
            "error_type=AWSFailure",
            "aws_operation=SendEmail",
            "aws_error_code=MessageRejected",
            "aws_request_id=a64d6b07-0d45-4c69-a7d9-b86b4838f036",
            "aws_http_status=400",
        ):
            self.assertIn(field, summary)
        for sensitive in (
            "buyer@example.com", "secret", "Bearer", "https://example.com",
            "not verified", "RequestParameters", "HTTPHeaders",
        ):
            self.assertNotIn(sensitive, summary)

    def test_access_denied_and_rate_limits_remain_distinguishable(self):
        for code in ("AccessDenied", "Throttling", "RequestLimitExceeded"):
            with self.subTest(code=code):
                response = deepcopy(self.response)
                response["Error"]["Code"] = code
                self.assertIn(
                    f"aws_error_code={code}",
                    failure_details(AWSFailure(response)),
                )

    def test_non_aws_exception_never_logs_its_message(self):
        summary = failure_details(ValueError("DATABASE_URL contains secret-password"))
        self.assertEqual(summary, "error_type=ValueError")

    def test_metadata_cannot_inject_log_lines(self):
        response = deepcopy(self.response)
        response["Error"]["Code"] = "AccessDenied\nforged-log-entry"
        response["ResponseMetadata"]["RequestId"] = "buyer@example.com"
        summary = failure_details(AWSFailure(response, "SendEmail\rforged-log-entry"))
        self.assertNotIn("\n", summary)
        self.assertNotIn("\r", summary)
        self.assertNotIn("forged-log-entry", summary)
        self.assertNotIn("buyer@example.com", summary)

    def test_malformed_optional_metadata_does_not_hide_the_original_failure(self):
        for response in (
            None,
            [],
            {"Error": None, "ResponseMetadata": []},
            {"Error": {"Code": []}, "ResponseMetadata": {"RequestId": {}}},
        ):
            with self.subTest(response=response):
                self.assertIn("error_type=AWSFailure", failure_details(AWSFailure(response)))

    def test_invalid_status_values_are_not_logged_as_http_status(self):
        for value in (True, "400", 99, 600):
            with self.subTest(value=value):
                response = deepcopy(self.response)
                response["ResponseMetadata"]["HTTPStatusCode"] = value
                self.assertNotIn("aws_http_status=", failure_details(AWSFailure(response)))


if __name__ == "__main__":
    unittest.main()
