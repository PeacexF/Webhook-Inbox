import base64
import json
from typing import Any
from urllib.parse import parse_qs

"""
MongoDB restricts '.' and a leading '$' in field names, so payload keys are
substituted with lookalike codepoints
Round-tripping a key that genuinely contained these lookalikes would alter it,
raw_body stays authoritative
"""

DOT = "．"
DOLLAR = "＄"

EVENT_TYPE_HEADERS = ("x-webhook-event", "x-github-event", "x-event-name", "x-event-type")
EVENT_TYPE_FIELDS = ("event", "type", "event_type", "eventType")


def _escape_key(key: str) -> str:
    key = key.replace(".", DOT)
    return DOLLAR + key[1:] if key.startswith("$") else key


def _unescape_key(key: str) -> str:
    key = key.replace(DOT, ".")
    return "$" + key[1:] if key.startswith(DOLLAR) else key


def escape_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {_escape_key(k): escape_keys(v) for k, v in value.items()}
    if isinstance(value, list):
        return [escape_keys(v) for v in value]
    return value


def unescape_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {_unescape_key(k): unescape_keys(v) for k, v in value.items()}
    if isinstance(value, list):
        return [unescape_keys(v) for v in value]
    return value


def decode_raw(raw: bytes) -> tuple[str, str]:
    # Returns (text, encoding) — base64 when the body is not valid UTF-8
    try:
        return raw.decode(), "utf-8"
    except UnicodeDecodeError:
        return base64.b64encode(raw).decode(), "base64"


def nesting_depth(raw: bytes) -> int:
    depth = maximum = 0
    in_string = escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:  # backslash
                escaped = True
            elif byte == 0x22:  # closing quote
                in_string = False
            continue
        if byte == 0x22:
            in_string = True
        elif byte in (0x7B, 0x5B):  # { [
            depth += 1
            maximum = max(maximum, depth)
        elif byte in (0x7D, 0x5D):  # } ]
            depth -= 1
    return maximum


def parse_body(raw: bytes, content_type: str, max_depth: int = 100) -> Any:
    # Structured representation of the body, or None when it has no useful one
    media_type = content_type.split(";")[0].strip().lower()
    if not raw:
        return None
    if media_type == "application/json" or media_type.endswith("+json"):
        # raw_body is still kept, so an over-nested payload is stored, just not walked
        if nesting_depth(raw) > max_depth:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError, UnicodeDecodeError, RecursionError:
            return None
    if media_type == "application/x-www-form-urlencoded":
        try:
            parsed = parse_qs(raw.decode())
        except UnicodeDecodeError:
            return None
        return {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}
    if media_type.startswith("text/"):
        try:
            return raw.decode()
        except UnicodeDecodeError:
            return None
    return None


def extract_event_type(headers: dict[str, str], body: Any) -> str | None:
    for header in EVENT_TYPE_HEADERS:
        if value := headers.get(header):
            return value
    if isinstance(body, dict):
        for field in EVENT_TYPE_FIELDS:
            value = body.get(field)
            if isinstance(value, str) and value:
                return value
    return None
