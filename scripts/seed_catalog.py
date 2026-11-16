import os
from pathlib import Path
from urllib.parse import urlparse

from pymongo import MongoClient

from shared.aws import aws_client
from shared.inventory import create_redis_client

EVENT_ID = "11111111-1111-4111-8111-111111111111"


def main():
    endpoint = os.getenv("AWS_ENDPOINT_URL", "")
    if urlparse(endpoint).hostname not in {
        "localstack", "localhost", "127.0.0.1"
    }:
        raise RuntimeError("This seed command is local-only")

    database = os.getenv(
        "MONGODB_DATABASE", "flash_ticket"
    )

    with MongoClient(
        os.environ["MONGODB_URL"],
        serverSelectionTimeoutMS=5000,
    ) as mongo:
        collection = mongo[database].events

        collection.create_index([
            ("status", 1),
            ("_id", 1),
        ])

        collection.update_one(
            {"_id": EVENT_ID},
            {
                "$setOnInsert": {
                    "title": "Flash Night Live",
                    "description": (
                        "Đêm nhạc demo của Flash-Ticket. "
                        "Đặt vé và nhận PDF có QR."
                    ),
                    "banner_key": "banners/demo.svg",
                    "status": "PUBLISHED",
                }
            },
            upsert=True,
        )

    banner = (
        Path(__file__).resolve().parents[1]
        / "assets"
        / "demo-banner.svg"
    )

    aws_client("s3").put_object(
        Bucket=os.getenv(
            "ASSETS_BUCKET", "flash-ticket-assets"
        ),
        Key="banners/demo.svg",
        Body=banner.read_bytes(),
        ContentType="image/svg+xml",
        ServerSideEncryption="AES256",
    )

    redis = create_redis_client()

    try:
        redis.delete("catalog:events:v1")
    finally:
        redis.close()

    print(f"Catalog demo ready: {EVENT_ID}")


if __name__ == "__main__":
    main()