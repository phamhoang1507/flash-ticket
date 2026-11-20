import json
import logging
import os
import random
import signal
import threading
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import text

from shared.aws import aws_client, get_queue_url
from shared.database import create_db_engine
from shared.errors import failure_details
from shared.inventory import create_redis_client, project_inventory

logger = logging.getLogger("outbox-relay")
stop = threading.Event()

LEASE_SECONDS = int(os.getenv("LEASE_SECONDS", "120"))
POLL_SECONDS = float(os.getenv("OUTBOX_POLL_SECONDS", "1"))

if not 30 <= LEASE_SECONDS <= 43200:
    raise ValueError("LEASE_SECONDS must be between 30 and 43200")

if not 0.1 <= POLL_SECONDS <= 30:
    raise ValueError("OUTBOX_POLL_SECONDS must be between 0.1 and 30")

engine = create_db_engine()


@dataclass(frozen=True)
class Event:
    id: str
    owner: str
    event_type: str
    payload: dict
    attempts: int


def claim_event() -> Event | None:
    with engine.begin() as connection:
        row = connection.execute(text("""
            SELECT id, event_type, payload, attempts
            FROM outbox
            WHERE published_at IS NULL
              AND available_at <= UTC_TIMESTAMP(6)
              AND (
                  lease_until IS NULL
                  OR lease_until <= UTC_TIMESTAMP(6)
              )
            ORDER BY created_at, id
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        """)).mappings().first()

        if row is None:
            return None

        owner = str(uuid4())

        connection.execute(
            text("""
                UPDATE outbox
                SET lease_owner = :owner,
                    lease_until = TIMESTAMPADD(
                        SECOND, :lease_seconds, UTC_TIMESTAMP(6)
                    ),
                    attempts = attempts + 1
                WHERE id = :id
            """),
            {
                "id": row["id"],
                "owner": owner,
                "lease_seconds": LEASE_SECONDS,
            },
        )

        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)

        return Event(
            id=row["id"],
            owner=owner,
            event_type=row["event_type"],
            payload=payload,
            attempts=row["attempts"] + 1,
        )


def renew_lease(event: Event):
    with engine.begin() as connection:
        result = connection.execute(
            text("""
                UPDATE outbox
                SET lease_until = TIMESTAMPADD(
                    SECOND, :lease_seconds, UTC_TIMESTAMP(6)
                )
                WHERE id = :id
                  AND lease_owner = :owner
                  AND published_at IS NULL
                  AND lease_until > UTC_TIMESTAMP(6)
            """),
            {
                "id": event.id,
                "owner": event.owner,
                "lease_seconds": LEASE_SECONDS,
            },
        )

        if result.rowcount != 1:
            raise RuntimeError("Outbox lease lost")


def mark_published(event: Event):
    with engine.begin() as connection:
        result = connection.execute(
            text("""
                UPDATE outbox
                SET published_at = UTC_TIMESTAMP(6),
                    lease_owner = NULL,
                    lease_until = NULL
                WHERE id = :id
                  AND lease_owner = :owner
                  AND published_at IS NULL
                  AND lease_until > UTC_TIMESTAMP(6)
            """),
            {"id": event.id, "owner": event.owner},
        )

        if result.rowcount != 1:
            raise RuntimeError("Cannot acknowledge outbox publication")


def schedule_retry(event: Event):
    delay = (
        min(60, 2 ** min(event.attempts, 6))
        + random.randint(0, 3)
    )

    with engine.begin() as connection:
        connection.execute(
            text("""
                UPDATE outbox
                SET available_at = TIMESTAMPADD(
                        SECOND, :delay, UTC_TIMESTAMP(6)
                    ),
                    lease_owner = NULL,
                    lease_until = NULL
                WHERE id = :id
                  AND lease_owner = :owner
                  AND published_at IS NULL
            """),
            {
                "id": event.id,
                "owner": event.owner,
                "delay": delay,
            },
        )


def publish(event: Event, redis, sqs, queue_url: str):
    if event.event_type != "BookingCreated":
        raise ValueError("Unsupported event type")

    if not isinstance(event.payload, dict):
        raise ValueError("Event payload must be an object")

    renew_lease(event)

    project_inventory(
        redis,
        event.payload["event_id"],
        event.payload["remaining"],
        event.payload["version"],
    )

    renew_lease(event)

    sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(
            {
                **event.payload,
                "event_type": event.event_type,
                "outbox_id": event.id,
            },
            separators=(",", ":"),
        ),
    )

    mark_published(event)


def main():
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())

    redis = create_redis_client()
    sqs = aws_client("sqs")
    failures = 0

    logger.info("Outbox Relay started")

    try:
        while not stop.is_set():
            event = None

            try:
                queue_url = get_queue_url()
                event = claim_event()

                if event is None:
                    failures = 0
                    stop.wait(POLL_SECONDS)
                    continue

                publish(event, redis, sqs, queue_url)
                failures = 0

                logger.info("Published outbox_id=%s", event.id)

            except Exception as exc:
                failures += 1

                logger.error(
                    "Relay failure outbox_id=%s %s",
                    event.id if event else "none",
                    failure_details(exc),
                )

                if event is not None:
                    try:
                        schedule_retry(event)
                    except Exception as retry_exc:
                        logger.error(
                            "Retry scheduling failed %s",
                            failure_details(retry_exc),
                        )

                delay = min(30, 2 ** min(failures, 5))
                stop.wait(delay + random.random())
    finally:
        redis.close()
        sqs.close()
        engine.dispose()
        logger.info("Outbox Relay stopped")


if __name__ == "__main__":
    main()
