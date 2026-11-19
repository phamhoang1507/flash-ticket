import argparse
import json
import os
import re
import sys

from botocore.exceptions import BotoCoreError, ClientError
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError

from shared.aws import aws_client
from shared.inventory import create_redis_client

EVENT_ID = "11111111-1111-4111-8111-111111111111"
CACHE_KEY = "catalog:events:v1"


class SeedError(RuntimeError):
    pass


def account_id(value: str) -> str:
    if not re.fullmatch(r"[0-9]{12}", value):
        raise argparse.ArgumentTypeError("Account ID must contain 12 digits")
    return value


def verify_aws_environment(expected_account: str):
    for name, value in os.environ.items():
        if not value:
            continue

        if (
            name == "AWS_ENDPOINT_URL"
            or name.startswith("AWS_ENDPOINT_URL_")
            or name == "S3_PUBLIC_ENDPOINT"
        ):
            raise SeedError("Remove AWS endpoint overrides before AWS seeding")

    local_credentials = {
        "test", "testing", "dummy", "localstack", "fake", "foobar_key",
        "foobar_secret",
    }

    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        if os.getenv(name, "").strip().casefold() in local_credentials:
            raise SeedError("LocalStack credentials cannot be used on AWS")

    region = os.getenv("AWS_REGION", "us-east-1")
    sts = aws_client("sts")

    try:
        allowed_endpoints = {
            "https://sts.amazonaws.com",
            f"https://sts.{region}.amazonaws.com",
        }

        if sts.meta.endpoint_url.rstrip("/") not in allowed_endpoints:
            raise SeedError("STS must use its official AWS HTTPS endpoint")

        if sts.get_caller_identity()["Account"] != expected_account:
            raise SeedError("AWS caller account does not match --account-id")
    finally:
        sts.close()


def seed(expected_account: str):
    verify_aws_environment(expected_account)

    mongo_url = os.getenv("MONGODB_URL", "")
    database = os.getenv("MONGODB_DATABASE", "flash_ticket")

    if not mongo_url or not os.getenv("REDIS_URL"):
        raise SeedError("MONGODB_URL and REDIS_URL are required")

    if database != "flash_ticket":
        raise SeedError("The AWS demo seed requires MONGODB_DATABASE=flash_ticket")

    with MongoClient(
        mongo_url,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        socketTimeoutMS=5000,
        waitQueueTimeoutMS=5000,
        maxPoolSize=2,
    ) as mongo:
        collection = mongo[database].events
        collection.create_index([("status", 1), ("_id", 1)])
        result = collection.update_one(
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

        document = collection.find_one(
            {"_id": EVENT_ID},
            {"status": 1, "banner_key": 1},
        )

        if (
            not document
            or document.get("status") != "PUBLISHED"
            or document.get("banner_key") != "banners/demo.svg"
        ):
            raise SeedError(
                "Existing demo event differs; review it before publishing"
            )

    redis = create_redis_client()

    try:
        redis.delete(CACHE_KEY)
    finally:
        redis.close()

    return {
        "status": "ready",
        "account_id": expected_account,
        "event_id": EVENT_ID,
        "event_created": result.upserted_id is not None,
        "catalog_cache_invalidated": True,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Seed the Flash-Ticket AWS demo event without resetting inventory"
    )
    parser.add_argument("--account-id", type=account_id, required=True)
    arguments = parser.parse_args()

    try:
        print(json.dumps(seed(arguments.account_id)))
    except SeedError as error:
        print(f"AWS_CATALOG_SEED_FAILED: {error}", file=sys.stderr)
        sys.exit(1)
    except (
        BotoCoreError,
        ClientError,
        PyMongoError,
        RedisError,
        OSError,
        ValueError,
    ) as error:
        print(
            f"AWS_CATALOG_SEED_FAILED error_type={type(error).__name__}",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
