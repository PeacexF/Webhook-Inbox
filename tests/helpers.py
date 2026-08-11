import hashlib
import hmac
import re
from typing import Any

import httpx

EVENT_LINK = re.compile(r"/events/([0-9a-f]{24})")


def first_event_id(html: str) -> str:
    # Matches an id specifically, so other /events/ links cannot be picked up
    match = EVENT_LINK.search(html)
    assert match, "no event link found in the page"
    return match.group(1)


async def create_endpoint(client: httpx.AsyncClient, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": "test", "path": "test", **overrides}
    response = await client.post("/api/endpoints", json=payload)
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


def sign(secret: str, body: bytes, prefix: str = "sha256=") -> str:
    return prefix + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
