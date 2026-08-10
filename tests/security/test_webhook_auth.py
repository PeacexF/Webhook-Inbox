from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, login
from tests.helpers import create_endpoint, sign

pytestmark = pytest.mark.integration

BODY = b'{"event":"user.created"}'
SECRET = "s3cret"


async def _event_count(authed_client: httpx.AsyncClient) -> int:
    # Rows carry attributes, so match the opening tag rather than "<tr>", less the header
    page = await authed_client.get("/events")
    return max(page.text.count("<tr") - 1, 0)


async def test_unknown_endpoint_is_rejected(authed_client: httpx.AsyncClient) -> None:
    response = await authed_client.post("/webhooks/nope", json={"a": 1})
    assert response.status_code == 404
    assert await _event_count(authed_client) == 0


async def test_disabled_endpoint_is_rejected(authed_client: httpx.AsyncClient) -> None:
    created = await create_endpoint(authed_client, name="off", path="off")
    await authed_client.patch(f"/api/endpoints/{created['id']}", json={"enabled": False})

    response = await authed_client.post("/webhooks/off", json={"a": 1})
    assert response.status_code == 403
    assert await _event_count(authed_client) == 0


async def test_method_not_allowed(authed_client: httpx.AsyncClient) -> None:
    await create_endpoint(authed_client, name="postonly", path="postonly", allowed_methods=["POST"])

    assert (await authed_client.put("/webhooks/postonly", json={"a": 1})).status_code == 405
    assert (await authed_client.post("/webhooks/postonly", json={"a": 1})).status_code == 202
    assert await _event_count(authed_client) == 1


async def test_hmac_valid_signature_accepted(authed_client: httpx.AsyncClient) -> None:
    await create_endpoint(
        authed_client, name="gh", path="gh", authentication={"type": "hmac_sha256"}, secret=SECRET
    )
    response = await authed_client.post(
        "/webhooks/gh",
        content=BODY,
        headers={"content-type": "application/json", "x-hub-signature-256": sign(SECRET, BODY)},
    )
    assert response.status_code == 202


async def test_hmac_invalid_signature_rejected_and_not_stored(
    authed_client: httpx.AsyncClient,
) -> None:
    await create_endpoint(
        authed_client, name="gh", path="gh", authentication={"type": "hmac_sha256"}, secret=SECRET
    )
    response = await authed_client.post(
        "/webhooks/gh",
        content=BODY,
        headers={"content-type": "application/json", "x-hub-signature-256": "sha256=deadbeef"},
    )
    assert response.status_code == 401
    assert await _event_count(authed_client) == 0


async def test_hmac_missing_signature_rejected(authed_client: httpx.AsyncClient) -> None:
    await create_endpoint(
        authed_client, name="gh", path="gh", authentication={"type": "hmac_sha256"}, secret=SECRET
    )
    response = await authed_client.post(
        "/webhooks/gh", content=BODY, headers={"content-type": "application/json"}
    )
    assert response.status_code == 401


async def test_signature_covers_the_body(authed_client: httpx.AsyncClient) -> None:
    await create_endpoint(
        authed_client, name="gh", path="gh", authentication={"type": "hmac_sha256"}, secret=SECRET
    )
    response = await authed_client.post(
        "/webhooks/gh",
        content=b'{"event":"tampered"}',
        headers={"content-type": "application/json", "x-hub-signature-256": sign(SECRET, BODY)},
    )
    assert response.status_code == 401


async def test_static_secret(authed_client: httpx.AsyncClient) -> None:
    await create_endpoint(
        authed_client, name="st", path="st", authentication={"type": "static_secret"}, secret=SECRET
    )

    async def post(headers: dict[str, str] | None = None) -> int:
        response = await authed_client.post("/webhooks/st", json={"a": 1}, headers=headers)
        return response.status_code

    assert await post({"x-webhook-secret": SECRET}) == 202
    assert await post({"x-webhook-secret": "wrong"}) == 401
    assert await post() == 401


async def test_per_endpoint_payload_limit(authed_client: httpx.AsyncClient) -> None:
    await create_endpoint(authed_client, name="small", path="small", max_payload_size=64)

    assert (await authed_client.post("/webhooks/small", content=b"x" * 512)).status_code == 413
    assert (await authed_client.post("/webhooks/small", json={"a": 1})).status_code == 202


async def test_endpoint_limit_overrides_a_larger_global(
    make_client: Callable[..., AsyncIterator[httpx.AsyncClient]],
) -> None:
    async for anonymous in make_client(limits={"max_payload_size": 10_000_000}):
        authed_client = await login(anonymous, ADMIN_USERNAME, ADMIN_PASSWORD)
        await create_endpoint(authed_client, name="small", path="small", max_payload_size=64)
        assert (await authed_client.post("/webhooks/small", content=b"x" * 512)).status_code == 413


async def test_rejections_are_logged_without_the_secret(
    authed_client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    await create_endpoint(
        authed_client, name="gh", path="gh", authentication={"type": "hmac_sha256"}, secret=SECRET
    )
    await authed_client.post(
        "/webhooks/gh", content=BODY, headers={"x-hub-signature-256": "sha256=deadbeef"}
    )
    assert SECRET not in capsys.readouterr().err
