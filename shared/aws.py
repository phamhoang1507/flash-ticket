import os
from functools import lru_cache

import boto3
from botocore.config import Config


@lru_cache
def aws_client(service: str, *, public: bool = False):
    endpoint = os.getenv("AWS_ENDPOINT_URL") or None

    if service == "s3" and public:
        endpoint = os.getenv("S3_PUBLIC_ENDPOINT") or endpoint

    return boto3.client(
        service,
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        endpoint_url=endpoint,
        config=Config(
            connect_timeout=3,
            read_timeout=30,
            retries={
                "mode": "standard",
                "total_max_attempts": 3,
            },
            signature_version="s3v4" if service == "s3" else "v4",
            s3={"addressing_style": "path"},
        ),
    )


@lru_cache
def get_queue_url() -> str:
    return aws_client("sqs").get_queue_url(
        QueueName=os.getenv("QUEUE_NAME", "flash-ticket-bookings")
    )["QueueUrl"]