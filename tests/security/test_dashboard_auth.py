import httpx
import pytest

from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, login

pytestmark = pytest.mark.integration

GUARDED_PAGES = ["/", "/events", "/endpoints", "/settings"]
GUARDED_API = ["/api/endpoints", "/api/auth/me"]
PUBLIC = ["/health", "/ready", "/login"]


@pytest.mark.parametrize("path", GUARDED_PAGES)
async def test_pages_redirect_to_login_when_anonymous(client: httpx.AsyncClient, path: str) -> None:
    response = await client.get(path)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


@pytest.mark.parametrize("path", GUARDED_API)
async def test_api_returns_401_when_anonymous(client: httpx.AsyncClient, path: str) -> None:
    assert (await client.get(path)).status_code == 401


@pytest.mark.parametrize("path", PUBLIC)
async def test_public_paths_stay_open(client: httpx.AsyncClient, path: str) -> None:
    assert (await client.get(path)).status_code == 200


async def test_anonymous_cannot_create_endpoints(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/endpoints", json={"name": "x", "path": "x"})
    assert response.status_code == 401
    assert await client.post("/webhooks/x", json={}) is not None


async def test_login_rejects_bad_credentials(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/auth/login", json={"username": ADMIN_USERNAME, "password": "wrong"}
    )
    assert response.status_code == 401
    assert "wi_session" not in response.cookies


async def test_login_rejects_unknown_user(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/auth/login", json={"username": "ghost", "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 401


async def test_login_sets_a_hardened_cookie(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "path=/" in cookie


async def test_me_returns_the_session_user(authed_client: httpx.AsyncClient) -> None:
    response = await authed_client.get("/api/auth/me")
    assert response.json()["username"] == ADMIN_USERNAME


async def test_logout_revokes_the_session(authed_client: httpx.AsyncClient) -> None:
    assert (await authed_client.post("/api/auth/logout")).status_code == 204
    assert (await authed_client.get("/api/auth/me")).status_code == 401


async def test_session_token_is_not_stored_in_plaintext(
    authed_client: httpx.AsyncClient,
) -> None:
    token = authed_client.cookies["wi_session"]
    listing = await authed_client.get("/api/auth/me")
    assert listing.status_code == 200
    assert token not in listing.text


async def test_mutations_require_the_csrf_header(authed_client: httpx.AsyncClient) -> None:
    del authed_client.headers["X-CSRF-Token"]
    response = await authed_client.post("/api/endpoints", json={"name": "x", "path": "x"})
    assert response.status_code == 403


async def test_wrong_csrf_token_is_rejected(authed_client: httpx.AsyncClient) -> None:
    authed_client.headers["X-CSRF-Token"] = "forged"
    response = await authed_client.post("/api/endpoints", json={"name": "x", "path": "x"})
    assert response.status_code == 403


async def test_reads_do_not_need_csrf(authed_client: httpx.AsyncClient) -> None:
    del authed_client.headers["X-CSRF-Token"]
    assert (await authed_client.get("/api/endpoints")).status_code == 200


async def test_webhooks_never_require_csrf_or_a_session(client: httpx.AsyncClient) -> None:
    # Ingestion must stay reachable by external services
    assert (await client.post("/webhooks/demo", json={"a": 1})).status_code == 202


async def test_password_change_invalidates_sessions(authed_client: httpx.AsyncClient) -> None:
    response = await authed_client.post(
        "/settings/password",
        data={"current_password": ADMIN_PASSWORD, "new_password": "a-new-password"},
    )
    assert response.status_code == 204
    assert (await authed_client.get("/api/auth/me")).status_code == 401

    await login(authed_client, ADMIN_USERNAME, "a-new-password")
    assert (await authed_client.get("/api/auth/me")).status_code == 200


async def test_wrong_current_password_does_not_change_it(
    authed_client: httpx.AsyncClient,
) -> None:
    response = await authed_client.post(
        "/settings/password",
        data={"current_password": "not-it", "new_password": "a-new-password"},
    )
    assert response.status_code == 200
    assert "Current password is wrong" in response.text
    assert (await authed_client.get("/api/auth/me")).status_code == 200
