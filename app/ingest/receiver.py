from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.db import Doc
from app.deps import DatabaseDep, SettingsDep
from app.ingest.parser import (
    decode_raw,
    escape_keys,
    extract_event_type,
    parse_body,
)
from app.log import get_logger

router = APIRouter()
logger = get_logger(__name__)

METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


class PayloadTooLarge(Exception):
    pass


async def read_limited(request: Request, limit: int) -> bytes:
    # Read the body, refusing oversized payloads before buffering them
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise PayloadTooLarge

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise PayloadTooLarge
        chunks.append(chunk)
    return b"".join(chunks)


@router.api_route("/webhooks/{path:path}", methods=METHODS)
async def receive(
    path: str,
    request: Request,
    db: DatabaseDep,
    settings: SettingsDep,
) -> JSONResponse:
    try:
        raw = await read_limited(request, settings.limits.max_payload_size)
    except PayloadTooLarge:
        logger.warning("event.rejected", endpoint=path, reason="payload_too_large")
        return JSONResponse({"detail": "Payload too large"}, status_code=413)

    headers = dict(request.headers)
    content_type = headers.get("content-type", "")
    body = parse_body(raw, content_type)
    raw_text, raw_encoding = decode_raw(raw)

    document: Doc = {
        "endpoint": {"id": None, "name": path},
        "received_at": datetime.now(UTC),
        "event_type": extract_event_type(headers, body),
        "request": {
            "method": request.method,
            "headers": headers,
            "query": dict(request.query_params),
            "body": escape_keys(body),
            "raw_body": raw_text,
            "raw_encoding": raw_encoding,
            "content_type": content_type,
            "body_size": len(raw),
        },
        "processing": {"status": "received", "response_status": 200},
        "metadata": {
            "source_ip": request.client.host if request.client else None,
            "user_agent": headers.get("user-agent"),
        },
    }

    result = await db.events.insert_one(document)
    logger.info("event.received", endpoint=path, id=str(result.inserted_id))
    return JSONResponse({"id": str(result.inserted_id), "status": "received"}, status_code=202)
