from datetime import UTC, datetime
from typing import Any

from pymongo import AsyncMongoClient, IndexModel
from pymongo.asynchronous.database import AsyncDatabase

Doc = dict[str, Any]
Database = AsyncDatabase[Doc]

EVENT_INDEXES = [
    # Default listing: filter by endpoint, sort by recency. The hottest query.
    IndexModel([("endpoint.id", 1), ("received_at", -1)], name="endpoint_received"),
    IndexModel([("received_at", -1)], name="received_desc"),
    IndexModel([("processing.status", 1), ("received_at", -1)], name="status_received"),
    IndexModel([("event_type", 1)], name="event_type"),
]

ENDPOINT_INDEXES = [
    # Every inbound webhook dispatches on this, and paths must not collide
    IndexModel([("path", 1)], name="path_unique", unique=True),
]

USER_INDEXES = [
    IndexModel([("username", 1)], name="username_unique", unique=True),
]

SESSION_INDEXES = [
    IndexModel([("token_hash", 1)], name="token_unique", unique=True),
    # Expired sessions are swept by MongoDB rather than by code
    IndexModel([("expires_at", 1)], name="session_ttl", expireAfterSeconds=0),
    IndexModel([("user_id", 1)], name="session_user"),
]

DEMO_ENDPOINT = {
    "name": "demo",
    "path": "demo",
    "enabled": True,
    "authentication": {"type": "none", "header": None, "signature_prefix": "sha256="},
    "secret": None,
    "allowed_methods": ["GET", "POST", "PUT", "PATCH", "DELETE"],
    "max_payload_size": None,
    "retention_days": None,
}


def create_client(uri: str) -> AsyncMongoClient[Doc]:
    return AsyncMongoClient(uri, tz_aware=True)


async def ensure_indexes(db: Database) -> None:
    await db.events.create_indexes(EVENT_INDEXES)
    await db.endpoints.create_indexes(ENDPOINT_INDEXES)
    await db.users.create_indexes(USER_INDEXES)
    await db.sessions.create_indexes(SESSION_INDEXES)


async def seed_demo_endpoint(db: Database) -> None:
    if await db.endpoints.count_documents({}, limit=1):
        return
    now = datetime.now(UTC)
    await db.endpoints.insert_one({**DEMO_ENDPOINT, "created_at": now, "updated_at": now})
