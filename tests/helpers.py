import hashlib
import hmac
from typing import Any

import httpx


async def create_endpoint(client: httpx.AsyncClient, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": "test", "path": "test", **overrides}
    response = await client.post("/api/endpoints", json=payload)
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


def sign(secret: str, body: bytes, prefix: str = "sha256=") -> str:
    return prefix + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
