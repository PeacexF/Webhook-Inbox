import base64
from datetime import UTC, datetime, timedelta

from bson import ObjectId
from pymongo import ReturnDocument

from app.config import ReplayConfig
from app.db import Database, Doc
from app.models.replay import ReplayCreate, ReplayState
from app.replay.runner import Attempt


def event_body(event: Doc) -> bytes:
    # Replays send the bytes that arrived, not a re-serialised parse of them
    request = event.get("request", {})
    raw = request.get("raw_body") or ""
    if request.get("raw_encoding") == "base64":
        return base64.b64decode(raw)
    return raw.encode()


async def enqueue(
    db: Database, event_id: ObjectId, payload: ReplayCreate, config: ReplayConfig
) -> Doc:
    now = datetime.now(UTC)
    document: Doc = {
        "event_id": event_id,
        "destination": payload.destination,
        "method": payload.method,
        "headers": payload.headers,
        "state": ReplayState.PENDING.value,
        "attempt": 0,
        "max_attempts": config.max_retries + 1,
        "next_attempt_at": now,
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "duration_ms": None,
        "response": None,
        "error": None,
    }
    result = await db.replays.insert_one(document)
    return {**document, "_id": result.inserted_id}


async def claim(db: Database, config: ReplayConfig) -> Doc | None:
    now = datetime.now(UTC)
    # A worker that died mid-attempt leaves the job running forever; take it back
    await db.replays.update_many(
        {
            "state": ReplayState.RUNNING.value,
            "leased_at": {"$lt": now - timedelta(seconds=config.lease_timeout)},
        },
        {"$set": {"state": ReplayState.PENDING.value}},
    )
    claimed: Doc | None = await db.replays.find_one_and_update(
        {"state": ReplayState.PENDING.value, "next_attempt_at": {"$lte": now}},
        {
            "$set": {"state": ReplayState.RUNNING.value, "leased_at": now, "started_at": now},
            "$inc": {"attempt": 1},
        },
        sort=[("next_attempt_at", 1)],
        return_document=ReturnDocument.AFTER,
    )
    return claimed


async def finish(db: Database, replay: Doc, attempt: Attempt, config: ReplayConfig) -> str:
    now = datetime.now(UTC)
    changes: Doc = {
        "duration_ms": attempt.duration_ms,
        "error": attempt.error,
        "response": (
            {
                "status_code": attempt.status_code,
                "headers": attempt.headers,
                "body": attempt.body,
                "truncated": attempt.truncated,
            }
            if attempt.status_code is not None
            else None
        ),
    }

    if attempt.succeeded:
        changes |= {"state": ReplayState.SUCCESS.value, "completed_at": now}
    elif attempt.retryable and replay["attempt"] < replay["max_attempts"]:
        # Exponential backoff from the configured base delay
        delay = config.retry_delay_seconds * 2 ** (replay["attempt"] - 1)
        changes |= {
            "state": ReplayState.PENDING.value,
            "next_attempt_at": now + timedelta(seconds=delay),
        }
    else:
        changes |= {"state": ReplayState.FAILED.value, "completed_at": now}

    await db.replays.update_one({"_id": replay["_id"]}, {"$set": changes})
    state: str = changes["state"]
    return state


async def abandon(db: Database, replay: Doc, reason: str) -> None:
    await db.replays.update_one(
        {"_id": replay["_id"]},
        {
            "$set": {
                "state": ReplayState.FAILED.value,
                "error": reason,
                "completed_at": datetime.now(UTC),
            }
        },
    )


async def history(db: Database, event_id: ObjectId, limit: int = 20) -> list[Doc]:
    cursor = db.replays.find({"event_id": event_id}).sort("created_at", -1).limit(limit)
    return [doc async for doc in cursor]
