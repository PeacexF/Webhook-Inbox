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


def create_client(uri: str) -> AsyncMongoClient[Doc]:
    return AsyncMongoClient(uri, tz_aware=True)


async def ensure_indexes(db: Database) -> None:
    await db.events.create_indexes(EVENT_INDEXES)
