import os
import random
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
)
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError

from shared.auth import Principal, get_verifier, require_scope
from shared.aws import aws_client
from shared.inventory import create_redis_client

CACHE_KEY = "catalog:events:v1"
DATABASE = os.getenv("MONGODB_DATABASE", "flash_ticket")
BUCKET = os.getenv("ASSETS_BUCKET", "flash-ticket-assets")
ASSETS_BASE = os.getenv("ASSETS_PUBLIC_BASE_URL", "").rstrip("/")


class EventInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    title: str = Field(min_length=3, max_length=120)
    description: str = Field(default="", max_length=2000)
    banner_key: str

    @field_validator("banner_key")
    @classmethod
    def validate_banner_key(cls, value: str):
        if not re.fullmatch(
            r"banners/[A-Za-z0-9][A-Za-z0-9._/-]{0,180}",
            value,
        ):
            raise ValueError("Invalid banner key")

        if any(
            part in {"", ".", ".."}
            for part in value.split("/")
        ):
            raise ValueError("Invalid banner path")

        return value


class EventMetadata(EventInput):
    id: UUID


class EventView(BaseModel):
    id: UUID
    title: str
    description: str
    banner_url: str
    remaining: int | None
    inventory_version: int | None


metadata_adapter = TypeAdapter(list[EventMetadata])


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_verifier()

    app.state.mongo = MongoClient(
        os.environ["MONGODB_URL"],
        serverSelectionTimeoutMS=3000,
        connectTimeoutMS=3000,
        socketTimeoutMS=3000,
        waitQueueTimeoutMS=3000,
        maxPoolSize=20,
    )
    app.state.redis = create_redis_client()

    try:
        yield
    finally:
        app.state.mongo.close()
        app.state.redis.close()


app = FastAPI(
    title="Flash-Ticket Event Catalog",
    lifespan=lifespan,
)


def load_metadata(request: Request) -> list[EventMetadata]:
    redis = request.app.state.redis

    try:
        cached = redis.get(CACHE_KEY)
        if cached:
            return metadata_adapter.validate_json(cached)
    except (RedisError, ValidationError):
        pass

    try:
        collection = request.app.state.mongo[DATABASE].events
        documents = collection.find(
            {"status": "PUBLISHED"},
            {
                "title": 1,
                "description": 1,
                "banner_key": 1,
            },
        ).sort("_id", 1).limit(100)

        records = [
            EventMetadata(
                id=str(document["_id"]),
                title=document["title"],
                description=document.get("description", ""),
                banner_key=document["banner_key"],
            )
            for document in documents
        ]
    except (PyMongoError, ValidationError):
        raise HTTPException(
            503,
            "Event catalog temporarily unavailable",
        ) from None

    try:
        redis.setex(
            CACHE_KEY,
            60 + random.randint(0, 15),
            metadata_adapter.dump_json(records),
        )
    except RedisError:
        pass

    return records


def banner_url(key: str) -> str:
    if ASSETS_BASE:
        return f"{ASSETS_BASE}/{quote(key, safe='/')}"

    return aws_client(
        "s3", public=True
    ).generate_presigned_url(
        "get_object",
        Params={
            "Bucket": BUCKET,
            "Key": key,
        },
        ExpiresIn=600,
    )


def inventory_values(projection: dict):
    try:
        remaining = int(projection["remaining"])
        version = int(projection["version"])

        if remaining < 0 or version < 0:
            return None, None

        return remaining, version
    except (KeyError, TypeError, ValueError):
        return None, None


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz(request: Request):
    try:
        request.app.state.mongo.admin.command("ping")
    except PyMongoError:
        raise HTTPException(
            503, "Catalog storage unavailable"
        ) from None

    redis_status = "available"

    try:
        request.app.state.redis.ping()
    except RedisError:
        redis_status = "degraded"

    return {
        "status": "ready",
        "redis": redis_status,
    }


@app.get("/events", response_model=list[EventView])
def get_events(request: Request):
    records = load_metadata(request)

    try:
        pipeline = request.app.state.redis.pipeline(
            transaction=False
        )
        for record in records:
            pipeline.hgetall(f"inventory:{record.id}")
        projections = pipeline.execute()
    except RedisError:
        projections = [{} for _ in records]

    result = []

    for record, projection in zip(records, projections):
        remaining, version = inventory_values(projection)

        result.append(
            EventView(
                id=record.id,
                title=record.title,
                description=record.description,
                banner_url=banner_url(record.banner_key),
                remaining=remaining,
                inventory_version=version,
            )
        )

    return result


@app.post("/events", status_code=201)
def create_event(
    body: EventInput,
    request: Request,
    principal: Annotated[
        Principal,
        Depends(require_scope("events:write")),
    ],
):
    event_id = str(uuid4())

    try:
        request.app.state.mongo[DATABASE].events.insert_one({
            "_id": event_id,
            **body.model_dump(),
            "status": "DRAFT",
            "created_by": principal.subject,
            "created_at": datetime.now(UTC),
        })
    except PyMongoError:
        raise HTTPException(
            503,
            "Event catalog temporarily unavailable",
        ) from None

    return {
        "id": event_id,
        "status": "DRAFT",
    }