import re
from collections.abc import Iterable
from itertools import chain
from typing import Any

from app.ingest.parser import unescape_keys
from app.log import is_sensitive

SPLIT = re.compile(r"[^a-z0-9]+")

# Arrays land in multikey indexes, 
# where one huge event would cost as much as thousands of small ones
# Caps bound that and oversized payloads lose the tail
TOKEN_LIMIT = 512
TRIGRAM_LIMIT = 2048
MAX_TOKEN_LENGTH = 64
MIN_TRIGRAM_LENGTH = 3


def normalize(value: str) -> str:
    return value.strip().lower()


def split_terms(value: str) -> list[str]:
    return [part[:MAX_TOKEN_LENGTH] for part in SPLIT.split(normalize(value)) if part]


def tokenize(value: str) -> list[str]:
    normalized = normalize(value)
    parts = split_terms(normalized)
    # Identifiers like checkout.session.completed stay matchable whole,
    # but a whole sentence as one token would only bloat the index
    if len(parts) > 1 and " " not in normalized:
        parts.append(normalized[:MAX_TOKEN_LENGTH])
    return parts


def trigrams(token: str) -> list[str]:
    if len(token) < MIN_TRIGRAM_LENGTH:
        return []
    padded = f" {token} "
    return [padded[i : i + 3] for i in range(len(padded) - 2)]


def _capped(values: Iterable[str], limit: int) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        seen[value] = None
        if len(seen) >= limit:
            break
    return list(seen)


def _collect(value: Any, out: list[str]) -> None:
    match value:
        case dict():
            for key, item in value.items():
                if is_sensitive(key):
                    continue
                out.append(key)
                _collect(item, out)
        case list():
            for item in value:
                _collect(item, out)
        case str():
            out.append(value)
        case bool():
            pass
        case int() | float():
            out.append(str(value))


def source_strings(
    endpoint_name: str,
    event_type: str | None,
    headers: dict[str, str],
    query: dict[str, str],
    body: Any,
) -> list[str]:
    out: list[str] = [endpoint_name]
    if event_type:
        out.append(event_type)
    for pairs in (headers, query):
        for key, value in pairs.items():
            # sensutuve = not searchable
            if is_sensitive(key):
                continue
            out.append(key)
            out.append(value)
    _collect(body, out)
    return out


def build(
    endpoint_name: str,
    event_type: str | None,
    headers: dict[str, str],
    query: dict[str, str],
    body: Any,
) -> dict[str, list[str]]:
    sources = source_strings(endpoint_name, event_type, headers, query, body)
    tokens = _capped(chain.from_iterable(tokenize(s) for s in sources), TOKEN_LIMIT)
    grams = _capped(chain.from_iterable(trigrams(t) for t in tokens), TRIGRAM_LIMIT)
    return {"tokens": tokens, "trigrams": grams}


def build_from_event(document: dict[str, Any]) -> dict[str, list[str]]:
    # For documents already in the database, whose body keys are stored escaped
    request = document.get("request", {})
    return build(
        document.get("endpoint", {}).get("name", ""),
        document.get("event_type"),
        request.get("headers", {}),
        request.get("query", {}),
        unescape_keys(request.get("body")),
    )
