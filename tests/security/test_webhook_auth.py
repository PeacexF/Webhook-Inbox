from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from tests.helpers import create_endpoint, sign

pytestmark = pytest.mark.integration

BODY = b'{"event":"user.created"}'
SECRET = "s3cret"


async def _event_count(client: httpx.AsyncClient) -> int:
    page = await client.get("/events")
    return page.text.count("<tr>") - 1 if "<tr>" in page.text else 0


async def test_unknown_endpoint_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.post("/webhooks/nope", json={"a": 1})
    assert response.status_code == 404
    assert await _event_count(client) == 0


async def test_disabled_endpoint_is_rejected(client: httpx.AsyncClient) -> None:
    created = await create_endpoint(client, name="off", path="off")
    await client.patch(f"/api/endpoints/{created['id']}", json={"enabled": False})

    response = await client.post("/webhooks/off", json={"a": 1})
    assert response.status_code == 403
    assert await _event_count(client) == 0


async def test_method_not_allowed(client: httpx.AsyncClient) -> None:
    await create_endpoint(client, name="postonly", path="postonly", allowed_methods=["POST"])

    assert (await client.put("/webhooks/postonly", json={"a": 1})).status_code == 405
    assert (await client.post("/webhooks/postonly", json={"a": 1})).status_code == 202
    assert await _event_count(client) == 1


async def test_hmac_valid_signature_accepted(client: httpx.AsyncClient) -> None:
    await create_endpoint(
        client, name="gh", path="gh", authentication={"type": "hmac_sha256"}, secret=SECRET
    )
    response = await client.post(
        "/webhooks/gh",
        content=BODY,
        headers={"content-type": "application/json", "x-hub-signature-256": sign(SECRET, BODY)},
    )
    assert response.status_code == 202


async def test_hmac_invalid_signature_rejected_and_not_stored(client: httpx.AsyncClient) -> None:
    await create_endpoint(
        client, name="gh", path="gh", authentication={"type": "hmac_sha256"}, secret=SECRET
    )
    response = await client.post(
        "/webhooks/gh",
        content=BODY,
        headers={"content-type": "application/json", "x-hub-signature-256": "sha256=deadbeef"},
    )
    assert response.status_code == 401
    assert await _event_count(client) == 0


async def test_hmac_missing_signature_rejected(client: httpx.AsyncClient) -> None:
    await create_endpoint(
        client, name="gh", path="gh", authentication={"type": "hmac_sha256"}, secret=SECRET
    )
    response = await client.post(
        "/webhooks/gh", content=BODY, headers={"content-type": "application/json"}
    )
    assert response.status_code == 401


async def test_signature_covers_the_body(client: httpx.AsyncClient) -> None:
    await create_endpoint(
        client, name="gh", path="gh", authentication={"type": "hmac_sha256"}, secret=SECRET
    )
    response = await client.post(
        "/webhooks/gh",
        content=b'{"event":"tampered"}',
        headers={"content-type": "application/json", "x-hub-signature-256": sign(SECRET, BODY)},
    )
    assert response.status_code == 401


async def test_static_secret(client: httpx.AsyncClient) -> None:
    await create_endpoint(
        client, name="st", path="st", authentication={"type": "static_secret"}, secret=SECRET
    )
    assert (
        await client.post("/webhooks/st", json={"a": 1}, headers={"x-webhook-secret": SECRET})
    ).status_code == 202
    assert (
        await client.post("/webhooks/st", json={"a": 1}, headers={"x-webhook-secret": "wrong"})
    ).status_code == 401
    assert (await client.post("/webhooks/st", json={"a": 1})).status_code == 401


async def test_per_endpoint_payload_limit(client: httpx.AsyncClient) -> None:
    await create_endpoint(client, name="small", path="small", max_payload_size=64)

    assert (await client.post("/webhooks/small", content=b"x" * 512)).status_code == 413
    assert (await client.post("/webhooks/small", json={"a": 1})).status_code == 202


async def test_endpoint_limit_overrides_a_larger_global(
    make_client: Callable[..., AsyncIterator[httpx.AsyncClient]],
) -> None:
    async for client in make_client(limits={"max_payload_size": 10_000_000}):
        await create_endpoint(client, name="small", path="small", max_payload_size=64)
        assert (await client.post("/webhooks/small", content=b"x" * 512)).status_code == 413


async def test_rejections_are_logged_without_the_secret(
    client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    await create_endpoint(
        client, name="gh", path="gh", authentication={"type": "hmac_sha256"}, secret=SECRET
    )
    await client.post(
        "/webhooks/gh", content=BODY, headers={"x-hub-signature-256": "sha256=deadbeef"}
    )
    assert SECRET not in capsys.readouterr().err
