import httpx
import pytest

from tests.conftest import ADMIN_USERNAME
from tests.helpers import create_endpoint

pytestmark = pytest.mark.integration


async def test_dashboard_shows_stats(authed_client: httpx.AsyncClient) -> None:
    await authed_client.post("/webhooks/demo", json={"event": "user.created"})

    page = await authed_client.get("/")
    assert page.status_code == 200
    assert "Dashboard" in page.text
    assert ADMIN_USERNAME in page.text


async def test_event_detail_tabs(authed_client: httpx.AsyncClient) -> None:
    await authed_client.post(
        "/webhooks/demo?src=test",
        json={"event": "checkout.session.completed", "data": {"amount": 2500}},
        headers={"x-webhook-event": "checkout.session.completed"},
    )
    listing = await authed_client.get("/events")
    event_id = listing.text.split("/events/")[1].split("'")[0]

    overview = await authed_client.get(f"/events/{event_id}")
    assert "checkout.session.completed" in overview.text
    assert "Source IP" in overview.text

    headers = await authed_client.get(f"/events/{event_id}?tab=headers")
    assert "content-type" in headers.text

    query = await authed_client.get(f"/events/{event_id}?tab=query")
    assert "src" in query.text

    body = await authed_client.get(f"/events/{event_id}?tab=body")
    assert "json-view" in body.text
    assert "amount" in body.text

    raw = await authed_client.get(f"/events/{event_id}?tab=raw")
    assert "checkout.session.completed" in raw.text


async def test_event_detail_unescapes_restricted_keys(authed_client: httpx.AsyncClient) -> None:
    await authed_client.post("/webhooks/demo", json={"a.b": 1})
    listing = await authed_client.get("/events")
    event_id = listing.text.split("/events/")[1].split("'")[0]

    body = await authed_client.get(f"/events/{event_id}?tab=body")
    assert "a.b" in body.text


async def test_unknown_event_is_404(authed_client: httpx.AsyncClient) -> None:
    assert (await authed_client.get("/events/507f1f77bcf86cd799439011")).status_code == 404
    assert (await authed_client.get("/events/nonsense")).status_code == 404


async def test_endpoints_page_lists_and_creates(authed_client: httpx.AsyncClient) -> None:
    page = await authed_client.get("/endpoints")
    assert "demo" in page.text

    response = await authed_client.post(
        "/endpoints",
        data={"name": "github", "path": "github", "auth_type": "none", "allowed_methods": "POST"},
    )
    assert response.status_code == 204
    assert response.headers["hx-redirect"] == "/endpoints"
    assert "github" in (await authed_client.get("/endpoints")).text


async def test_endpoint_form_reports_duplicate_path(authed_client: httpx.AsyncClient) -> None:
    response = await authed_client.post(
        "/endpoints",
        data={"name": "dup", "path": "demo", "auth_type": "none", "allowed_methods": "POST"},
    )
    assert "already in use" in response.text


async def test_endpoint_edit_keeps_secret_when_blank(authed_client: httpx.AsyncClient) -> None:
    created = await create_endpoint(
        authed_client, name="gh", path="gh", authentication={"type": "hmac_sha256"}, secret="keep"
    )
    response = await authed_client.post(
        f"/endpoints/{created['id']}",
        data={
            "name": "gh",
            "path": "gh",
            "auth_type": "hmac_sha256",
            "secret": "",
            "allowed_methods": "POST",
            "enabled": "true",
        },
    )
    assert response.status_code == 204

    detail = await authed_client.get(f"/api/endpoints/{created['id']}")
    assert detail.json()["has_secret"] is True


async def test_settings_lists_users_and_adds_one(authed_client: httpx.AsyncClient) -> None:
    page = await authed_client.get("/settings")
    assert ADMIN_USERNAME in page.text

    response = await authed_client.post(
        "/settings/users", data={"username": "second", "password": "another-password"}
    )
    assert response.status_code == 204
    assert "second" in (await authed_client.get("/settings")).text


async def test_duplicate_username_is_reported(authed_client: httpx.AsyncClient) -> None:
    response = await authed_client.post(
        "/settings/users", data={"username": ADMIN_USERNAME, "password": "another-password"}
    )
    assert "already taken" in response.text


async def test_cannot_delete_yourself_or_the_last_user(authed_client: httpx.AsyncClient) -> None:
    me = await authed_client.get("/api/auth/me")
    response = await authed_client.delete(f"/settings/users/{me.json()['id']}")
    assert response.status_code == 400


async def test_theme_toggle_is_present(authed_client: httpx.AsyncClient) -> None:
    page = await authed_client.get("/")
    assert "toggleTheme" in page.text
    assert "prefers-color-scheme" in page.text

    css = await authed_client.get("/static/app.css")
    assert css.status_code == 200
    assert '[data-theme="dark"]' in css.text
    assert "#7fb08f" in css.text.lower()
