import asyncio
import sys

from pymongo import UpdateOne

from app.config import Settings
from app.db import Database, create_client
from app.log import configure, get_logger
from app.search.tokenize import build_from_event

BATCH_SIZE = 500
logger = get_logger(__name__)


async def backfill(db: Database, rebuild: bool = False) -> int:
    # Derive search terms for events stored before the field existed
    query = {} if rebuild else {"search": {"$exists": False}}
    operations: list[UpdateOne] = []
    updated = 0

    async for document in db.events.find(query):
        operations.append(
            UpdateOne({"_id": document["_id"]}, {"$set": {"search": build_from_event(document)}})
        )
        if len(operations) >= BATCH_SIZE:
            await db.events.bulk_write(operations)
            updated += len(operations)
            operations = []

    if operations:
        await db.events.bulk_write(operations)
        updated += len(operations)
    return updated


async def main() -> None:
    settings = Settings()
    configure(settings.log_level)
    client = create_client(settings.mongo_uri)
    try:
        # --rebuild also rewrites events that already have terms
        updated = await backfill(client[settings.mongo_database], "--rebuild" in sys.argv)
        logger.info("search.backfilled", events=updated)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
