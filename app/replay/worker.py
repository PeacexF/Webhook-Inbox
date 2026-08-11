import asyncio
import contextlib

from app.config import Settings
from app.db import Database, create_client
from app.log import configure, get_logger
from app.replay import store
from app.replay.runner import execute, merge_headers

logger = get_logger(__name__)


async def run_once(db: Database, settings: Settings) -> bool:
    """Execute one queued replay. Returns False when the queue is empty."""
    replay = await store.claim(db, settings.replay)
    if replay is None:
        return False

    event = await db.events.find_one({"_id": replay["event_id"]})
    if event is None:
        await store.abandon(db, replay, "The event no longer exists")
        return True

    attempt = await execute(
        replay["destination"],
        replay["method"],
        merge_headers(event["request"].get("headers", {}), replay.get("headers", {})),
        store.event_body(event),
        settings.replay,
    )
    state = await store.finish(db, replay, attempt, settings.replay)
    logger.info(
        "replay.attempted",
        id=str(replay["_id"]),
        attempt=replay["attempt"],
        state=state,
        status=attempt.status_code,
        duration_ms=attempt.duration_ms,
        error=attempt.error,
    )
    return True


async def run_forever(db: Database, settings: Settings, stop: asyncio.Event) -> None:
    logger.info("replay.worker_started", poll_interval=settings.replay.poll_interval)
    while not stop.is_set():
        try:
            worked = await run_once(db, settings)
        except Exception:
            logger.exception("replay.worker_error")
            worked = False
        if not worked:
            # Waiting on the stop event rather than sleeping makes shutdown immediate
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.replay.poll_interval)
    logger.info("replay.worker_stopped")


async def main() -> None:
    settings = Settings()
    configure(settings.log_level)
    client = create_client(settings.mongo_uri)
    try:
        await run_forever(client[settings.mongo_database], settings, asyncio.Event())
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
