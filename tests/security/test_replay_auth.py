import httpx
import pytest

from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, login
from tests.helpers import create_endpoint

pytestmark = pytest.mark.integration

EVENT_ID = "deadbeefdeadbeefdeadbeef"


async def test_anonymous_cannot_queue_a_replay(client: httpx.AsyncClient) -> None:
    response = await client.post(
        f"/api/events/{EVENT_ID}/replay", json={"destination": "https://example.com/hook"}
    )
    assert response.status_code == 401


async def test_anonymous_cannot_read_replay_history(client: httpx.AsyncClient) -> None:
    assert (await client.get(f"/api/events/{EVENT_ID}/replays")).status_code == 401


async def test_queueing_a_replay_requires_the_csrf_token(client: httpx.AsyncClient) -> None:
    # Authenticated but without the header the session handed out
    await client.post(
        "/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )
    response = await client.post(
        f"/api/events/{EVENT_ID}/replay", json={"destination": "https://example.com/hook"}
    )
    assert response.status_code == 403


async def test_a_replay_destination_is_validated_before_anything_is_stored(
    client: httpx.AsyncClient,
) -> None:
    await login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    await create_endpoint(client, name="stripe", path="stripe")
    posted = await client.post("/webhooks/stripe", json={"type": "ping"})
    event_id = posted.json()["id"]

    for destination in (
        "http://127.0.0.1/hook",
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
        "gopher://internal:70/_x",
        "http://[::1]/hook",
    ):
        response = await client.post(
            f"/api/events/{event_id}/replay", json={"destination": destination}
        )
        assert response.status_code == 400, f"{destination} was accepted"

    assert await client.db.replays.count_documents({}) == 0
