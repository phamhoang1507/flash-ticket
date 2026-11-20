import json
import logging
import os
import random
import signal
import threading
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import text

from app.ticket import create_ticket_pdf
from shared.aws import aws_client, get_queue_url
from shared.database import create_db_engine
from shared.errors import failure_details

logger = logging.getLogger("ticket-worker")
stop = threading.Event()

LEASE_SECONDS = int(os.getenv("LEASE_SECONDS", "120"))
VISIBILITY_SECONDS = int(
    os.getenv("SQS_VISIBILITY_SECONDS", "120")
)
SIGNING_SECRET = os.environ["TICKET_SIGNING_SECRET"]
ASSETS_BUCKET = os.getenv(
    "ASSETS_BUCKET", "flash-ticket-assets"
)
SES_SENDER = os.getenv(
    "SES_SENDER", "tickets@example.com"
)

if not 30 <= LEASE_SECONDS <= 43200:
    raise ValueError("Invalid LEASE_SECONDS")

if not 30 <= VISIBILITY_SECONDS <= 43200:
    raise ValueError("Invalid SQS_VISIBILITY_SECONDS")

if len(SIGNING_SECRET) < 32:
    raise ValueError("TICKET_SIGNING_SECRET is too short")

engine = create_db_engine()


@dataclass(frozen=True)
class ClaimedBooking:
    id: str
    owner: str
    event_id: str
    quantity: int
    email: str
    status: str
    ticket_key: str | None
    email_status: str


class LeaseBusyError(RuntimeError):
    pass


def claim_booking(booking_id: str) -> ClaimedBooking | None:
    owner = str(uuid4())

    with engine.begin() as connection:
        result = connection.execute(
            text("""
                UPDATE bookings
                SET lease_owner = :owner,
                    lease_until = TIMESTAMPADD(
                        SECOND, :seconds, UTC_TIMESTAMP(6)
                    )
                WHERE id = :id
                  AND (
                      status <> 'ISSUED'
                      OR email_status <> 'SENT'
                  )
                  AND (
                      lease_until IS NULL
                      OR lease_until <= UTC_TIMESTAMP(6)
                  )
            """),
            {
                "id": booking_id,
                "owner": owner,
                "seconds": LEASE_SECONDS,
            },
        )

        row = connection.execute(
            text("""
                SELECT id, event_id, quantity, email,
                       status, ticket_key, email_status
                FROM bookings
                WHERE id = :id
            """),
            {"id": booking_id},
        ).mappings().first()

        if row is None:
            raise ValueError("Unknown booking")

        if result.rowcount != 1:
            if (
                row["status"] == "ISSUED"
                and row["email_status"] == "SENT"
                and row["ticket_key"]
            ):
                return None

            raise LeaseBusyError("Booking is already being processed")

        return ClaimedBooking(owner=owner, **dict(row))


ASSIGNMENTS = {
    "renew": """
        lease_until = TIMESTAMPADD(
            SECOND, :lease_seconds, UTC_TIMESTAMP(6)
        )
    """,
    "issued": "status = 'ISSUED', ticket_key = :ticket_key",
    "sent": "email_status = 'SENT'",
}


def owned_update(
    booking: ClaimedBooking,
    operation: str,
    **values,
):
    assignment = ASSIGNMENTS[operation]

    statement = text(f"""
        UPDATE bookings
        SET {assignment}
        WHERE id = :id
          AND lease_owner = :owner
          AND lease_until > UTC_TIMESTAMP(6)
    """)

    with engine.begin() as connection:
        result = connection.execute(
            statement,
            {
                **values,
                "id": booking.id,
                "owner": booking.owner,
                "lease_seconds": LEASE_SECONDS,
            },
        )

        if result.rowcount != 1:
            raise RuntimeError("Booking lease lost")


def release_booking(booking: ClaimedBooking):
    with engine.begin() as connection:
        connection.execute(
            text("""
                UPDATE bookings
                SET lease_owner = NULL,
                    lease_until = NULL
                WHERE id = :id
                  AND lease_owner = :owner
            """),
            {
                "id": booking.id,
                "owner": booking.owner,
            },
        )


class Heartbeat:
    def __init__(self, booking, sqs, queue_url, receipt):
        self.booking = booking
        self.sqs = sqs
        self.queue_url = queue_url
        self.receipt = receipt

        self.done = threading.Event()
        self.lost = threading.Event()
        self.thread = threading.Thread(
            target=self.run,
            daemon=True,
        )

    def renew(self):
        owned_update(self.booking, "renew")

        self.sqs.change_message_visibility(
            QueueUrl=self.queue_url,
            ReceiptHandle=self.receipt,
            VisibilityTimeout=VISIBILITY_SECONDS,
        )

    def run(self):
        interval = min(
            LEASE_SECONDS, VISIBILITY_SECONDS
        ) / 3

        while not self.done.wait(interval):
            try:
                self.renew()
            except Exception as exc:
                self.lost.set()
                logger.error(
                    "Heartbeat failed booking_id=%s %s",
                    self.booking.id,
                    failure_details(exc),
                )
                return

    def check(self):
        if self.lost.is_set():
            raise RuntimeError("Heartbeat failed")

        owned_update(self.booking, "renew")

    def __enter__(self):
        self.renew()
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.done.set()
        self.thread.join()


def process_message(
    message: dict,
    sqs,
    s3,
    public_s3,
    ses,
    queue_url: str,
):
    payload = json.loads(message["Body"])

    if (
        not isinstance(payload, dict)
        or payload.get("event_type") != "BookingCreated"
    ):
        raise ValueError("Unsupported message")

    booking_id = str(UUID(payload["booking_id"]))
    booking = claim_booking(booking_id)

    if booking is None:
        logger.info("Already completed booking_id=%s", booking_id)
        return

    try:
        with Heartbeat(
            booking,
            sqs,
            queue_url,
            message["ReceiptHandle"],
        ) as heartbeat:
            ticket_key = (
                booking.ticket_key
                or f"tickets/{booking.id}.pdf"
            )

            if booking.status != "ISSUED" or not booking.ticket_key:
                pdf = create_ticket_pdf(
                    booking.id,
                    booking.event_id,
                    booking.quantity,
                    SIGNING_SECRET,
                )

                heartbeat.check()

                s3.put_object(
                    Bucket=ASSETS_BUCKET,
                    Key=ticket_key,
                    Body=pdf,
                    ContentType="application/pdf",
                    ContentDisposition=(
                        f'attachment; filename="ticket-{booking.id}.pdf"'
                    ),
                    ServerSideEncryption="AES256",
                    Metadata={"booking-id": booking.id},
                )

                heartbeat.check()

                owned_update(
                    booking,
                    "issued",
                    ticket_key=ticket_key,
                )

            if booking.email_status != "SENT":
                heartbeat.check()

                download_url = public_s3.generate_presigned_url(
                    "get_object",
                    Params={
                        "Bucket": ASSETS_BUCKET,
                        "Key": ticket_key,
                    },
                    ExpiresIn=900,
                )

                delivery = ses.send_email(
                    Source=SES_SENDER,
                    Destination={
                        "ToAddresses": [booking.email]
                    },
                    Message={
                        "Subject": {
                            "Data": "Your Flash-Ticket is ready",
                            "Charset": "UTF-8",
                        },
                        "Body": {
                            "Text": {
                                "Data": (
                                    f"Booking: {booking.id}\n"
                                    f"Quantity: {booking.quantity}\n\n"
                                    "Download within 15 minutes:\n"
                                    f"{download_url}\n\n"
                                    "Request a new link in Flash-Ticket "
                                    "if this link expires."
                                ),
                                "Charset": "UTF-8",
                            }
                        },
                    },
                )

                logger.info(
                    "Email accepted booking_id=%s ses_message_id=%s",
                    booking.id,
                    delivery.get("MessageId", "unknown"),
                )

                heartbeat.check()
                owned_update(booking, "sent")

        logger.info(
            "Ticket completed booking_id=%s",
            booking.id,
        )
    finally:
        try:
            release_booking(booking)
        except Exception as exc:
            logger.error(
                "Lease release failed booking_id=%s %s",
                booking.id,
                failure_details(exc),
            )


def main():
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())

    sqs = aws_client("sqs")
    s3 = aws_client("s3")
    public_s3 = aws_client("s3", public=True)
    ses = aws_client("ses")
    failures = 0

    logger.info("Ticket Worker started")

    try:
        while not stop.is_set():
            try:
                queue_url = get_queue_url()

                response = sqs.receive_message(
                    QueueUrl=queue_url,
                    MaxNumberOfMessages=1,
                    WaitTimeSeconds=20,
                    VisibilityTimeout=VISIBILITY_SECONDS,
                )

                failures = 0

                for message in response.get("Messages", []):
                    try:
                        process_message(
                            message,
                            sqs,
                            s3,
                            public_s3,
                            ses,
                            queue_url,
                        )

                        sqs.delete_message(
                            QueueUrl=queue_url,
                            ReceiptHandle=message["ReceiptHandle"],
                        )
                    except Exception as exc:
                        logger.error(
                            "Message failed message_id=%s %s",
                            message.get("MessageId", "unknown"),
                            failure_details(exc),
                        )

            except Exception as exc:
                failures += 1

                logger.error(
                    "Polling failed %s",
                    failure_details(exc),
                )

                stop.wait(
                    min(30, 2 ** min(failures, 5))
                    + random.random()
                )
    finally:
        for client in (sqs, s3, public_s3, ses):
            client.close()

        engine.dispose()
        logger.info("Ticket Worker stopped")


if __name__ == "__main__":
    main()
