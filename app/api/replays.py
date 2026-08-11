from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, HTTPException, status

from app.db import Database, Doc
from app.deps import DatabaseDep, SettingsDep
from app.log import get_logger
from app.models.replay import ReplayCreate, ReplayOut
from app.replay import store
from app.replay.ssrf import DestinationError, validate

router = APIRouter(prefix="/api/events/{event_id}", tags=["replays"])
logger = get_logger(__name__)


async def event_or_404(db: Database, event_id: str) -> Doc:
    try:
        object_id = ObjectId(event_id)
    except InvalidId, TypeError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found") from None
    document = await db.events.find_one({"_id": object_id})
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    return document


@router.post("/replay", status_code=status.HTTP_202_ACCEPTED)
async def create_replay(
    event_id: str, payload: ReplayCreate, db: DatabaseDep, settings: SettingsDep
) -> ReplayOut:
    if not settings.replay.enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Replay is disabled")
    event = await event_or_404(db, event_id)

    # Checked here for immediate feedback, and again by the worker before it connects
    try:
        validate(payload.destination, settings.replay)
    except DestinationError as exc:
        logger.warning("replay.rejected", destination=payload.destination, reason=str(exc))
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None

    document = await store.enqueue(db, event["_id"], payload, settings.replay)
    logger.info("replay.queued", event_id=event_id, destination=payload.destination)
    return ReplayOut.from_document(document)


@router.get("/replays")
async def list_replays(event_id: str, db: DatabaseDep) -> list[ReplayOut]:
    event = await event_or_404(db, event_id)
    return [ReplayOut.from_document(doc) for doc in await store.history(db, event["_id"])]
