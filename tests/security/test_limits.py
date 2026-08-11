import pytest

from app.config import LimitsConfig, RateLimitConfig
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, login
from tests.helpers import create_endpoint

pytestmark = pytest.mark.integration


async def endpoint(client):
    await create_endpoint(client, name="limits", path="limits")


# --- request shape -------------------------------------------------------


async def test_too_many_headers_are_refused(make_client):
    async for client in make_client(limits=LimitsConfig(max_header_count=20)):
        await login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
        await endpoint(client)

        headers = {f"x-pad-{i}": "v" for i in range(50)}
        response = await client.post("/webhooks/limits", json={}, headers=headers)
        assert response.status_code == 431
        break


async def test_oversized_headers_are_refused(make_client):
    async for client in make_client(limits=LimitsConfig(max_header_bytes=512)):
        await login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
        await endpoint(client)

        response = await client.post("/webhooks/limits", json={}, headers={"x-big": "a" * 2000})
        assert response.status_code == 431
        break


async def test_an_overlong_query_string_is_refused(make_client):
    async for client in make_client(limits=LimitsConfig(max_query_length=100)):
        await login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
        await endpoint(client)

        response = await client.post(f"/webhooks/limits?pad={'x' * 500}", json={})
        assert response.status_code == 414
        break


async def test_shape_limits_are_enforced_before_the_endpoint_is_looked_up(make_client):
    # An unknown path would normally be a 404; the cheap check must win
    async for client in make_client(limits=LimitsConfig(max_query_length=50)):
        response = await client.post(f"/webhooks/does-not-exist?pad={'x' * 500}", json={})
        assert response.status_code == 414
        break


async def test_a_normal_request_is_unaffected(make_client):
    async for client in make_client():
        await login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
        await endpoint(client)
        assert (await client.post("/webhooks/limits", json={"ok": True})).status_code == 202
        break


# --- rate limiting -------------------------------------------------------


async def test_webhook_ingestion_is_rate_limited(make_client):
    async for client in make_client(rate_limit=RateLimitConfig(requests_per_minute=5)):
        await login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
        await endpoint(client)

        codes = [(await client.post("/webhooks/limits", json={})).status_code for _ in range(8)]
        assert codes[:5] == [202] * 5
        assert codes[5:] == [429] * 3
        break


async def test_a_throttled_webhook_says_when_to_retry(make_client):
    async for client in make_client(rate_limit=RateLimitConfig(requests_per_minute=1)):
        await login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
        await endpoint(client)

        await client.post("/webhooks/limits", json={})
        blocked = await client.post("/webhooks/limits", json={})

        assert blocked.status_code == 429
        assert int(blocked.headers["retry-after"]) >= 1
        break


async def test_throttled_events_are_not_stored(make_client):
    async for client in make_client(rate_limit=RateLimitConfig(requests_per_minute=2)):
        await login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
        await endpoint(client)

        for _ in range(6):
            await client.post("/webhooks/limits", json={})

        assert await client.db.events.count_documents({}) == 2
        break


async def test_rate_limits_are_per_endpoint(make_client):
    async for client in make_client(rate_limit=RateLimitConfig(requests_per_minute=2)):
        await login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
        await create_endpoint(client, name="one", path="one")
        await create_endpoint(client, name="two", path="two")

        for _ in range(3):
            await client.post("/webhooks/one", json={})
        # A flood against one endpoint must not silence another
        assert (await client.post("/webhooks/two", json={})).status_code == 202
        break


async def test_rate_limiting_can_be_disabled(make_client):
    async for client in make_client(rate_limit=RateLimitConfig(enabled=False)):
        await login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
        await endpoint(client)

        codes = [(await client.post("/webhooks/limits", json={})).status_code for _ in range(30)]
        assert set(codes) == {202}
        break


# --- sign-in throttling --------------------------------------------------


async def test_password_guessing_is_throttled(make_client):
    async for client in make_client(rate_limit=RateLimitConfig(login_per_minute=3)):
        codes = []
        for _ in range(6):
            response = await client.post(
                "/api/auth/login", json={"username": ADMIN_USERNAME, "password": "wrong"}
            )
            codes.append(response.status_code)

        assert codes[:3] == [401] * 3
        assert codes[3:] == [429] * 3, "guessing must stop, not merely fail"
        break


async def test_the_login_form_is_throttled_too(make_client):
    async for client in make_client(rate_limit=RateLimitConfig(login_per_minute=2)):
        for _ in range(2):
            await client.post("/login", data={"username": "admin", "password": "wrong"})

        response = await client.post("/login", data={"username": "admin", "password": "wrong"})
        assert response.status_code == 429
        assert "Too many sign-in attempts" in response.text
        break


async def test_throttling_blocks_the_correct_password_too(make_client):
    # Otherwise an attacker learns they guessed right from the status code
    async for client in make_client(rate_limit=RateLimitConfig(login_per_minute=2)):
        for _ in range(2):
            await client.post(
                "/api/auth/login", json={"username": ADMIN_USERNAME, "password": "wrong"}
            )

        response = await client.post(
            "/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 429
        break


async def test_sign_in_and_ingestion_budgets_are_separate(make_client):
    async for client in make_client(
        rate_limit=RateLimitConfig(requests_per_minute=100, login_per_minute=2)
    ):
        await login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
        await endpoint(client)

        for _ in range(5):
            await client.post("/api/auth/login", json={"username": "x", "password": "y"})
        # Exhausting the login budget must not stop webhooks arriving
        assert (await client.post("/webhooks/limits", json={})).status_code == 202
        break
