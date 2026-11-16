import json
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

    sqs = aws_client("sqs")
    name = os.getenv("QUEUE_NAME", "flash-ticket-bookings")

    def ensure_queue(queue_name: str, attributes: dict) -> str:
        try:
            url = sqs.get_queue_url(
                QueueName=queue_name
            )["QueueUrl"]
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in {
                "AWS.SimpleQueueService.NonExistentQueue",
                "QueueDoesNotExist",
                "NonExistentQueue",
            }:
                raise
            url = sqs.create_queue(
                QueueName=queue_name
            )["QueueUrl"]

        sqs.set_queue_attributes(
            QueueUrl=url,
            Attributes=attributes,
        )
        return url

    def queue_arn(url: str) -> str:
        return sqs.get_queue_attributes(
            QueueUrl=url,
            AttributeNames=["QueueArn"],
        )["Attributes"]["QueueArn"]

    dlq_url = ensure_queue(
        f"{name}-dlq",
        {
            "MessageRetentionPeriod": "1209600",
            "SqsManagedSseEnabled": "true",
        },
    )

    queue_url = ensure_queue(
        name,
        {
            "MessageRetentionPeriod": "345600",
            "VisibilityTimeout": "120",
            "ReceiveMessageWaitTimeSeconds": "20",
            "SqsManagedSseEnabled": "true",
            "RedrivePolicy": json.dumps({
                "deadLetterTargetArn": queue_arn(dlq_url),
                "maxReceiveCount": "5",
            }),
        },
    )

    sqs.set_queue_attributes(
        QueueUrl=dlq_url,
        Attributes={
            "RedriveAllowPolicy": json.dumps({
                "redrivePermission": "byQueue",
                "sourceQueueArns": [queue_arn(queue_url)],
            })
        },
    )

    print(f"Queue ready: {queue_url}")
    print(f"DLQ ready: {dlq_url}")


if __name__ == "__main__":
    main()