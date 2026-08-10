from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, HTTPException, Response, status
from pymongo.errors import DuplicateKeyError

from app.db import Database, Doc
from app.deps import DatabaseDep
from app.log import get_logger
from app.models.endpoint import EndpointCreate, EndpointOut, EndpointUpdate

router = APIRouter(prefix="/api/endpoints", tags=["endpoints"])
logger = get_logger(__name__)


async def _find_or_404(db: Database, endpoint_id: str) -> Doc:
    try:
        object_id = ObjectId(endpoint_id)
    except InvalidId, TypeError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Endpoint not found") from None
    document = await db.endpoints.find_one({"_id": object_id})
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Endpoint not found")
    return document


@router.get("")
async def list_endpoints(db: DatabaseDep) -> list[EndpointOut]:
    cursor = db.endpoints.find().sort("name", 1)
    return [EndpointOut.from_document(doc) async for doc in cursor]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_endpoint(payload: EndpointCreate, db: DatabaseDep) -> EndpointOut:
    document = payload.to_document()
    try:
        result = await db.endpoints.insert_one(document)
    except DuplicateKeyError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Path '{payload.path}' is already in use"
        ) from None
    logger.info("endpoint.created", path=payload.path, name=payload.name)
    return EndpointOut.from_document({**document, "_id": result.inserted_id})


@router.get("/{endpoint_id}")
async def get_endpoint(endpoint_id: str, db: DatabaseDep) -> EndpointOut:
    return EndpointOut.from_document(await _find_or_404(db, endpoint_id))


@router.patch("/{endpoint_id}")
async def update_endpoint(
    endpoint_id: str, payload: EndpointUpdate, db: DatabaseDep
) -> EndpointOut:
    document = await _find_or_404(db, endpoint_id)
    changes = payload.to_changes()

    merged = {**document, **changes}
    if merged.get("authentication", {}).get("type", "none") != "none" and not merged.get("secret"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Authenticated endpoints require a secret"
        )

    try:
        updated = await db.endpoints.find_one_and_update(
            {"_id": document["_id"]}, {"$set": changes}, return_document=True
        )
    except DuplicateKeyError:
        raise HTTPException(status.HTTP_409_CONFLICT, "Path is already in use") from None
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Endpoint not found")
    logger.info("endpoint.updated", id=endpoint_id, fields=sorted(changes))
    return EndpointOut.from_document(updated)


@router.delete("/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_endpoint(endpoint_id: str, db: DatabaseDep) -> Response:
    document = await _find_or_404(db, endpoint_id)
    await db.endpoints.delete_one({"_id": document["_id"]})
    logger.info("endpoint.deleted", id=endpoint_id, path=document["path"])
    return Response(status_code=status.HTTP_204_NO_CONTENT)
