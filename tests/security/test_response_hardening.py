import httpx
import pytest

from app.auth import SESSION_COOKIE
from app.middleware import SECURITY_HEADERS
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME

pytestmark = pytest.mark.integration

HTTPS = {"x-forwarded-proto": "https"}


# --- response headers -----------------------------------------------------


@pytest.mark.parametrize("header", sorted(SECURITY_HEADERS))
async def test_security_headers_are_present(authed_client: httpx.AsyncClient, header: str) -> None:
    response = await authed_client.get("/events")
    assert response.headers[header] == SECURITY_HEADERS[header]


async def test_the_page_cannot_be_framed(authed_client: httpx.AsyncClient) -> None:
    response = await authed_client.get("/events")
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


async def test_remote_scripts_are_blocked_by_the_policy(authed_client: httpx.AsyncClient) -> None:
    policy = (await authed_client.get("/events")).headers["content-security-policy"]
    assert "script-src 'self' 'unsafe-inline'" in policy
    assert "default-src 'self'" in policy


async def test_headers_are_set_on_unauthenticated_responses(client: httpx.AsyncClient) -> None:
    # Error and redirect paths must be covered too, not just rendered pages
    for path in ("/login", "/events", "/api/endpoints"):
        response = await client.get(path, follow_redirects=False)
        assert response.headers["x-content-type-options"] == "nosniff", path


async def test_headers_are_set_on_webhook_responses(client: httpx.AsyncClient) -> None:
    response = await client.post("/webhooks/nope", json={})
    assert response.status_code == 404
    assert "content-security-policy" in response.headers


async def test_hsts_is_absent_on_plain_http(authed_client: httpx.AsyncClient) -> None:
    response = await authed_client.get("/events")
    assert "strict-transport-security" not in response.headers


async def test_hsts_appears_behind_a_tls_proxy(authed_client: httpx.AsyncClient) -> None:
    response = await authed_client.get("/events", headers=HTTPS)
    assert "max-age=" in response.headers["strict-transport-security"]


# --- session cookie -------------------------------------------------------


async def test_the_session_cookie_is_httponly_and_samesite(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


async def test_the_session_cookie_is_not_secure_on_plain_http(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )
    assert "secure" not in response.headers["set-cookie"].lower()


async def test_the_session_cookie_is_secure_behind_a_tls_proxy(client: httpx.AsyncClient) -> None:
    # The usual deployment terminates TLS at a proxy and forwards plain HTTP
    response = await client.post(
        "/api/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        headers=HTTPS,
    )
    assert "secure" in response.headers["set-cookie"].lower()


async def test_the_login_form_also_marks_the_cookie_secure(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/login",
        data={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        headers=HTTPS,
        follow_redirects=False,
    )
    assert "secure" in response.headers["set-cookie"].lower()


# --- csrf -----------------------------------------------------------------


async def test_a_wrong_csrf_token_is_rejected(authed_client: httpx.AsyncClient) -> None:
    authed_client.headers["X-CSRF-Token"] = "not-the-real-token"
    response = await authed_client.post("/api/endpoints", json={"name": "x", "path": "x"})
    assert response.status_code == 403


async def test_a_missing_csrf_token_is_rejected(authed_client: httpx.AsyncClient) -> None:
    del authed_client.headers["X-CSRF-Token"]
    response = await authed_client.post("/api/endpoints", json={"name": "x", "path": "x"})
    assert response.status_code == 403


async def test_an_empty_csrf_token_cannot_match_an_absent_session(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/endpoints", json={"name": "x", "path": "x"}, headers={"X-CSRF-Token": ""}
    )
    assert response.status_code == 401


async def test_logout_clears_the_cookie(authed_client: httpx.AsyncClient) -> None:
    response = await authed_client.post("/api/auth/logout")
    assert response.status_code == 204
    assert SESSION_COOKIE in response.headers["set-cookie"]
