import csv
import io
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from app.db import Doc
from app.ingest.parser import unescape_keys

FORMATS = ("json", "jsonl", "csv")

CONTENT_TYPES = {
    "json": "application/json",
    "jsonl": "application/x-ndjson",
    "csv": "text/csv",
}

CSV_COLUMNS = (
    "id",
    "received_at",
    "endpoint",
    "event_type",
    "method",
    "status",
    "response_status",
    "content_type",
    "body_size",
    "source_ip",
    "user_agent",
    "body",
)

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

Rows = AsyncIterator[Doc]


def _encode(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def public(document: Doc) -> Doc:
    # Search terms are an internal index, and stored body keys are escaped
    request = dict(document.get("request", {}))
    request["body"] = unescape_keys(request.get("body"))
    return {
        "id": str(document["_id"]),
        "received_at": document["received_at"],
        "endpoint": document.get("endpoint", {}).get("name"),
        "event_type": document.get("event_type"),
        "request": request,
        "processing": document.get("processing", {}),
        "metadata": document.get("metadata", {}),
    }


async def stream_jsonl(rows: Rows) -> AsyncIterator[str]:
    async for document in rows:
        yield json.dumps(public(document), default=_encode) + "\n"


async def stream_json(rows: Rows) -> AsyncIterator[str]:
    # Emitted incrementally: a large export never sits in memory as one string
    yield "[\n"
    first = True
    async for document in rows:
        yield ("" if first else ",\n") + json.dumps(public(document), default=_encode)
        first = False
    yield "\n]\n"


def csv_safe(value: Any) -> Any:
    # A cell starting with one of these is evaluated as a formula by spreadsheet software
    # Event types and payloads are attacker-controlled, so neutralise them
    if isinstance(value, str) and value[:1] in FORMULA_PREFIXES:
        return "'" + value
    return value


def csv_row(document: Doc) -> list[Any]:
    request = document.get("request", {})
    processing = document.get("processing", {})
    metadata = document.get("metadata", {})
    cells = [
        str(document["_id"]),
        _encode(document["received_at"]),
        document.get("endpoint", {}).get("name"),
        document.get("event_type"),
        request.get("method"),
        processing.get("status"),
        processing.get("response_status"),
        request.get("content_type"),
        request.get("body_size"),
        metadata.get("source_ip"),
        metadata.get("user_agent"),
        request.get("raw_body"),
    ]
    return [csv_safe(cell) for cell in cells]


async def stream_csv(rows: Rows) -> AsyncIterator[str]:
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    def flush() -> str:
        value = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        return value

    writer.writerow(CSV_COLUMNS)
    yield flush()
    async for document in rows:
        writer.writerow(csv_row(document))
        yield flush()


STREAMERS = {"json": stream_json, "jsonl": stream_jsonl, "csv": stream_csv}


def filename(export_format: str, now: datetime) -> str:
    return f"events-{now:%Y%m%d-%H%M%S}.{export_format}"
