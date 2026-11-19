import hashlib
import json
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

from shared.auth import Principal, get_verifier, require_scope
from shared.aws import aws_client
from shared.database import create_db_engine

logger = logging.getLogger(__name__)

engine = create_db_engine(pool_size=5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_verifier()
    yield
    engine.dispose()


app = FastAPI(title="Flash-Ticket Booking", lifespan=lifespan)

Buyer = Annotated[Principal, Depends(require_scope("bookings:write"))]


class BookingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    quantity: int = Field(default=1, ge=1, le=5, strict=True)
    email: EmailStr = Field(max_length=254)


class BookingView(BaseModel):
    id: UUID
    event_id: UUID
    quantity: int
    status: Literal["PENDING", "ISSUED"]
    email_status: Literal["PENDING", "SENT"]
    created_at: datetime


BY_KEY = text("""
    SELECT id, event_id, quantity, status, email_status,
           created_at, request_hash
    FROM bookings
    WHERE user_id = :user_id
      AND idempotency_key = :idempotency_key
""")

BY_ID = text("""
    SELECT id, event_id, quantity, status, email_status,
           created_at, request_hash
    FROM bookings
    WHERE id = :booking_id
      AND user_id = :user_id
""")


def view(row) -> BookingView:
    return BookingView(
        id=row["id"],
        event_id=row["event_id"],
        quantity=row["quantity"],
        status=row["status"],
        email_status=row["email_status"],
        created_at=row["created_at"].replace(tzinfo=UTC),
    )


def replay(row, request_hash: str) -> BookingView:
    if row["request_hash"] != request_hash:
        raise HTTPException(
            409,
            "Idempotency-Key was used with a different request",
        )
    return view(row)


@app.exception_handler(SQLAlchemyError)
def database_error(request: Request, exc: SQLAlchemyError):
    logger.error("Database operation failed: %s", type(exc).__name__)
    return JSONResponse(
        status_code=503,
        content={"detail": "Booking temporarily unavailable"},
        headers={"Retry-After": "2"},
    )


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    with engine.connect() as connection:
        connection.execute(text("SELECT 1 FROM inventory LIMIT 1"))
    return {"status": "ready"}


@app.post("/bookings", response_model=BookingView, status_code=202)
def create_booking(
    body: BookingInput,
    buyer: Buyer,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=128),
    ],
):
    if not all(33 <= ord(char) <= 126 for char in idempotency_key):
        raise HTTPException(
            422,
            "Idempotency-Key must contain visible ASCII characters",
        )

    payload = body.model_dump(mode="json")
    request_hash = hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()

    params = {
        **payload,
        "user_id": buyer.subject,
        "idempotency_key": idempotency_key,
        "request_hash": request_hash,
    }

    for attempt in range(3):
        try:
            with engine.begin() as connection:
                previous = connection.execute(
                    BY_KEY, params
                ).mappings().first()

                if previous:
                    return replay(previous, request_hash)

                updated = connection.execute(
                    text("""
                        UPDATE inventory
                        SET remaining = remaining - :quantity,
                            version = version + 1
                        WHERE event_id = :event_id
                          AND remaining >= :quantity
                    """),
                    params,
                )

                if updated.rowcount != 1:
                    previous = connection.execute(
                        BY_KEY, params
                    ).mappings().first()

                    if previous:
                        return replay(previous, request_hash)

                    raise HTTPException(
                        409, "Sold out or event unavailable"
                    )

                inventory = connection.execute(
                    text("""
                        SELECT remaining, version
                        FROM inventory
                        WHERE event_id = :event_id
                    """),
                    params,
                ).mappings().one()

                params["booking_id"] = str(uuid4())

                connection.execute(
                    text("""
                        INSERT INTO bookings (
                            id, user_id, event_id, quantity, email,
                            idempotency_key, request_hash
                        )
                        VALUES (
                            :booking_id, :user_id, :event_id,
                            :quantity, :email,
                            :idempotency_key, :request_hash
                        )
                    """),
                    params,
                )

                connection.execute(
                    text("""
                        INSERT INTO outbox (
                            id, aggregate_id, event_type, payload
                        )
                        VALUES (
                            :outbox_id,
                            :booking_id,
                            'BookingCreated',
                            CAST(:payload AS JSON)
                        )
                    """),
                    {
                        "outbox_id": str(uuid4()),
                        "booking_id": params["booking_id"],
                        "payload": json.dumps({
                            "booking_id": params["booking_id"],
                            "event_id": params["event_id"],
                            "remaining": inventory["remaining"],
                            "version": inventory["version"],
                        }),
                    },
                )

                result = connection.execute(
                    BY_ID, params
                ).mappings().one()

                response = view(result)

            return response

        except IntegrityError:
            with engine.connect() as connection:
                previous = connection.execute(
                    BY_KEY, params
                ).mappings().first()

            if previous:
                return replay(previous, request_hash)

            raise HTTPException(
                503,
                "Booking temporarily unavailable",
                headers={"Retry-After": "2"},
            ) from None

        except OperationalError as exc:
            code = exc.orig.args[0] if exc.orig.args else None

            if code in (1205, 1213) and attempt < 2:
                time.sleep(random.uniform(0.02, 0.08) * (attempt + 1))
                continue

            raise

    raise HTTPException(503, "Booking temporarily unavailable")


@app.get("/bookings/{booking_id}", response_model=BookingView)
def get_booking(booking_id: UUID, buyer: Buyer):
    with engine.connect() as connection:
        row = connection.execute(
            BY_ID,
            {
                "booking_id": str(booking_id),
                "user_id": buyer.subject,
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(404, "Booking not found")

    return view(row)

@app.get("/bookings/{booking_id}/ticket")
def get_ticket(booking_id: UUID, buyer: Buyer):
    with engine.connect() as connection:
        row = connection.execute(
            text("""
                SELECT status, ticket_key
                FROM bookings
                WHERE id = :booking_id
                  AND user_id = :user_id
            """),
            {
                "booking_id": str(booking_id),
                "user_id": buyer.subject,
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(404, "Booking not found")

    if row["status"] != "ISSUED" or not row["ticket_key"]:
        raise HTTPException(409, "Ticket is not ready")

    expires_in = 120

    url = aws_client("s3", public=True).generate_presigned_url(
        "get_object",
        Params={
            "Bucket": os.getenv(
                "ASSETS_BUCKET", "flash-ticket-assets"
            ),
            "Key": row["ticket_key"],
            "ResponseContentDisposition": (
                f'attachment; filename="ticket-{booking_id}.pdf"'
            ),
        },
        ExpiresIn=expires_in,
    )

    return {
        "url": url,
        "expires_in": expires_in,
    }