import os
from functools import lru_cache

import boto3
from botocore.config import Config


@lru_cache
def aws_client(service: str):
    return boto3.client(
        service,
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        endpoint_url=os.getenv("AWS_ENDPOINT_URL") or None,
        config=Config(
            connect_timeout=3,
            read_timeout=30,
            retries={
                "mode": "standard",
                "total_max_attempts": 3,
            },
        ),
    )


@lru_cache
def get_queue_url() -> str:
    return aws_client("sqs").get_queue_url(
        QueueName=os.getenv("QUEUE_NAME", "flash-ticket-bookings")
    )["QueueUrl"]