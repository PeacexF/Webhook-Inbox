import httpx
import pytest

from tests.helpers import create_endpoint

pytestmark = pytest.mark.integration


async def test_demo_endpoint_is_seeded(client: httpx.AsyncClient) -> None:
    listing = await client.get("/api/endpoints")
    assert [e["path"] for e in listing.json()] == ["demo"]


async def test_create_and_fetch(client: httpx.AsyncClient) -> None:
    created = await create_endpoint(client, name="github", path="/webhooks/github")
    assert created["path"] == "github"
    assert created["url"] == "/webhooks/github"
    assert created["enabled"] is True
    assert created["allowed_methods"] == ["POST"]

    fetched = await client.get(f"/api/endpoints/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == created


async def test_secret_is_never_returned(client: httpx.AsyncClient) -> None:
    created = await create_endpoint(
        client,
        name="stripe",
        path="stripe",
        authentication={"type": "hmac_sha256"},
        secret="super-secret",
    )
    assert created["has_secret"] is True
    assert "secret" not in created
    assert "super-secret" not in (await client.get("/api/endpoints")).text
    assert "super-secret" not in (await client.get(f"/api/endpoints/{created['id']}")).text


async def test_duplicate_path_conflicts(client: httpx.AsyncClient) -> None:
    await create_endpoint(client, name="one", path="shared")
    response = await client.post("/api/endpoints", json={"name": "two", "path": "shared"})
    assert response.status_code == 409


async def test_patch_updates_only_given_fields(client: httpx.AsyncClient) -> None:
    created = await create_endpoint(client, name="github", path="github")
    response = await client.patch(f"/api/endpoints/{created['id']}", json={"enabled": False})

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert response.json()["name"] == "github"


async def test_patch_cannot_strand_an_authenticated_endpoint(client: httpx.AsyncClient) -> None:
    created = await create_endpoint(
        client, name="s", path="s", authentication={"type": "hmac_sha256"}, secret="k"
    )
    response = await client.patch(f"/api/endpoints/{created['id']}", json={"secret": None})
    assert response.status_code == 422


async def test_delete(client: httpx.AsyncClient) -> None:
    created = await create_endpoint(client, name="temp", path="temp")
    assert (await client.delete(f"/api/endpoints/{created['id']}")).status_code == 204
    assert (await client.get(f"/api/endpoints/{created['id']}")).status_code == 404


async def test_unknown_and_malformed_ids_are_404(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/endpoints/not-an-objectid")).status_code == 404
    assert (await client.get("/api/endpoints/507f1f77bcf86cd799439011")).status_code == 404


async def test_invalid_payloads_rejected(client: httpx.AsyncClient) -> None:
    assert (await client.post("/api/endpoints", json={"name": "x"})).status_code == 422
    assert (
        await client.post("/api/endpoints", json={"name": "x", "path": "bad path"})
    ).status_code == 422
    no_secret = {"name": "x", "path": "p", "authentication": {"type": "hmac_sha256"}}
    assert (await client.post("/api/endpoints", json=no_secret)).status_code == 422
