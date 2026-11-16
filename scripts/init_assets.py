import os
from urllib.parse import urlparse

from botocore.exceptions import ClientError

from shared.aws import aws_client


def main():
    endpoint = os.getenv("AWS_ENDPOINT_URL", "")
    if urlparse(endpoint).hostname not in {
        "localstack", "localhost", "127.0.0.1"
    }:
        raise RuntimeError("This initializer is local-only")

    bucket = os.getenv("ASSETS_BUCKET", "flash-ticket-assets")
    region = os.getenv("AWS_REGION", "us-east-1")
    sender = os.getenv("SES_SENDER", "tickets@example.com")

    s3 = aws_client("s3")

    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in {
            "404", "NoSuchBucket", "NotFound"
        }:
            raise

        options = {"Bucket": bucket}

        if region != "us-east-1":
            options["CreateBucketConfiguration"] = {
                "LocationConstraint": region
            }

        s3.create_bucket(**options)

    s3.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )

    s3.put_bucket_encryption(
        Bucket=bucket,
        ServerSideEncryptionConfiguration={
            "Rules": [{
                "ApplyServerSideEncryptionByDefault": {
                    "SSEAlgorithm": "AES256"
                }
            }]
        },
    )

    aws_client("ses").verify_email_identity(
        EmailAddress=sender
    )

    print(f"Private assets bucket ready: {bucket}")
    print(f"Local SES identity ready: {sender}")


if __name__ == "__main__":
    main()