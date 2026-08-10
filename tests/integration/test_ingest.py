from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from tests.helpers import create_endpoint

pytestmark = pytest.mark.integration

ALL_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


async def test_health_and_ready(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health")).json() == {"status": "ok"}

    ready = await client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["database"] == "up"


async def test_json_webhook_is_stored_and_listed(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/webhooks/demo",
        json={"event": "user.created", "user": {"id": 1827, "name": "John Doe"}},
        headers={"x-webhook-event": "user.created"},
    )
    assert response.status_code == 202
    assert response.json()["id"]

    page = await client.get("/events")
    assert page.status_code == 200
    assert "demo" in page.text
    assert "user.created" in page.text


async def test_non_json_body_is_preserved(client: httpx.AsyncClient) -> None:
    await create_endpoint(client, name="text", path="text")
    response = await client.post(
        "/webhooks/text", content=b"plain payload", headers={"content-type": "text/plain"}
    )
    assert response.status_code == 202

    page = await client.get("/events")
    assert "text" in page.text


async def test_restricted_keys_are_accepted(client: httpx.AsyncClient) -> None:
    response = await client.post("/webhooks/demo", json={"a.b": 1, "$set": {"c.d": 2}})
    assert response.status_code == 202


async def test_all_methods_are_captured(client: httpx.AsyncClient) -> None:
    await create_endpoint(client, name="multi", path="multi", allowed_methods=ALL_METHODS)
    for method in ALL_METHODS:
        response = await client.request(method, "/webhooks/multi")
        assert response.status_code == 202


async def test_events_page_empty_state(client: httpx.AsyncClient) -> None:
    page = await client.get("/events")
    assert "No events yet" in page.text


async def test_oversized_payload_rejected(
    make_client: Callable[..., AsyncIterator[httpx.AsyncClient]],
) -> None:
    async for client in make_client(limits={"max_payload_size": 1024}):
        response = await client.post("/webhooks/demo", content=b"x" * 2048)
        assert response.status_code == 413

        page = await client.get("/events")
        assert "No events yet" in page.text
