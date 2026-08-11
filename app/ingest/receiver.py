from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers

from app.db import Doc
from app.deps import DatabaseDep, RateLimiterDep, SettingsDep
from app.ingest.parser import (
    decode_raw,
    escape_keys,
    extract_event_type,
    parse_body,
)
from app.ingest.signature import verify
from app.log import get_logger
from app.models.endpoint import ALLOWED_METHODS, normalize_path
from app.ratelimit import client_key
from app.retention import expires_at
from app.search import tokenize

router = APIRouter()
logger = get_logger(__name__)


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


def _reject(
    status: int, reason: str, path: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    logger.warning("event.rejected", endpoint=path, reason=reason, status=status)
    return JSONResponse({"detail": reason}, status_code=status, headers=headers)


def _header_bytes(headers: Headers) -> int:
    return sum(len(key) + len(value) + 4 for key, value in headers.items())


@router.api_route("/webhooks/{path:path}", methods=list(ALLOWED_METHODS))
async def receive(
    path: str,
    request: Request,
    db: DatabaseDep,
    settings: SettingsDep,
    limiter: RateLimiterDep,
) -> JSONResponse:
    path = normalize_path(path)
    limits = settings.limits

    # Shape checks and the rate limit come first: both are free
    # and neither should cost a database round trip to answer
    if len(request.headers) > limits.max_header_count:
        return _reject(431, "Too many headers", path)
    if _header_bytes(request.headers) > limits.max_header_bytes:
        return _reject(431, "Headers too large", path)
    if len(request.url.query) > limits.max_query_length:
        return _reject(414, "Query string too long", path)

    bucket = f"{path}:{client_key(request, settings.rate_limit)}"
    if not limiter.allow(bucket):
        retry = str(limiter.retry_after(bucket))
        return _reject(429, "Rate limit exceeded", path, {"Retry-After": retry})

    endpoint = await db.endpoints.find_one({"path": path})
    if endpoint is None:
        return _reject(404, "Unknown endpoint", path)
    if not endpoint.get("enabled", True):
        return _reject(403, "Endpoint is disabled", path)
    if request.method not in endpoint.get("allowed_methods", ["POST"]):
        return _reject(405, "Method not allowed for this endpoint", path)

    limit = endpoint.get("max_payload_size") or settings.limits.max_payload_size
    try:
        raw = await read_limited(request, limit)
    except PayloadTooLarge:
        return _reject(413, "Payload too large", path)

    headers = dict(request.headers)
    # Signature is checked against the untouched bytes, before any parsing
    if not verify(endpoint, headers, raw):
        return _reject(401, "Invalid signature", path)

    content_type = headers.get("content-type", "")
    body = parse_body(raw, content_type, limits.max_json_depth)
    raw_text, raw_encoding = decode_raw(raw)
    event_type = extract_event_type(headers, body)
    query = dict(request.query_params)

    received_at = datetime.now(UTC)
    document: Doc = {
        "endpoint": {"id": endpoint["_id"], "name": endpoint["name"]},
        "received_at": received_at,
        "event_type": event_type,
        "request": {
            "method": request.method,
            "headers": headers,
            "query": query,
            "body": escape_keys(body),
            "raw_body": raw_text,
            "raw_encoding": raw_encoding,
            "content_type": content_type,
            "body_size": len(raw),
        },
        # Derived at write time so search never has to touch the payload
        "search": tokenize.build(endpoint["name"], event_type, headers, query, body),
        "processing": {"status": "received", "response_status": 202},
        "metadata": {
            "source_ip": request.client.host if request.client else None,
            "user_agent": headers.get("user-agent"),
        },
    }

    # Absent when retention is off, so the TTL monitor leaves the event alone
    if expiry := expires_at(endpoint, settings.retention, received_at):
        document["expires_at"] = expiry

    result = await db.events.insert_one(document)
    logger.info("event.received", endpoint=path, id=str(result.inserted_id))
    return JSONResponse({"id": str(result.inserted_id), "status": "received"}, status_code=202)
