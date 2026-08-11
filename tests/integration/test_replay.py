import socket
from datetime import UTC, datetime, timedelta

import pytest

from app.config import ReplayConfig
from app.replay.worker import run_once
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, login
from tests.helpers import create_endpoint
from tests.stub import StubServer

pytestmark = pytest.mark.integration


@pytest.fixture
def stub():
    server = StubServer()
    server.start()
    yield server
    server.stop()


def replay_config(**overrides):
    # Loopback is the only destination a test may reach, so the escape hatch is on
    base = {
        "allow_private_networks": True,
        "worker_enabled": False,
        "timeout": 5,
        "max_retries": 2,
        "retry_delay_seconds": 0,
    }
    return ReplayConfig(**(base | overrides))


@pytest.fixture
async def replay_client(make_client):
    async for client in make_client(replay=replay_config()):
        yield await login(client, ADMIN_USERNAME, ADMIN_PASSWORD)


async def seed_event(client, headers=None):
    await create_endpoint(client, name="stripe", path="stripe")
    response = await client.post(
        "/webhooks/stripe",
        content=b'{"type":"checkout.session.completed","amount":4200}',
        headers={"content-type": "application/json", **(headers or {})},
    )
    assert response.status_code == 202, response.text
    return response.json()["id"]


async def queue(client, event_id, destination, method="POST"):
    response = await client.post(
        f"/api/events/{event_id}/replay", json={"destination": destination, "method": method}
    )
    assert response.status_code == 202, response.text
    return response.json()


async def latest(client, event_id):
    response = await client.get(f"/api/events/{event_id}/replays")
    assert response.status_code == 200
    return response.json()[0]


async def make_due(client):
    await client.db.replays.update_many({}, {"$set": {"next_attempt_at": datetime.now(UTC)}})


# --- delivery -----------------------------------------------------------


async def test_replay_delivers_the_original_bytes(replay_client, stub):
    event_id = await seed_event(replay_client)
    await queue(replay_client, event_id, f"{stub.url}/hook")

    assert await run_once(replay_client.db, replay_client.settings)

    assert len(stub.requests) == 1
    sent = stub.requests[0]
    assert sent["method"] == "POST"
    assert sent["path"] == "/hook"
    assert sent["body"] == b'{"type":"checkout.session.completed","amount":4200}'

    record = await latest(replay_client, event_id)
    assert record["state"] == "success"
    assert record["response"]["status_code"] == 200


async def test_replay_connects_to_the_pinned_ip_but_sends_the_real_host(
    replay_client, stub, monkeypatch
):
    real = socket.getaddrinfo

    def fake(host, port, *args, **kwargs):
        if host == "webhook.test":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake)

    event_id = await seed_event(replay_client)
    await queue(replay_client, event_id, f"http://webhook.test:{stub.port}/hook")
    await run_once(replay_client.db, replay_client.settings)

    assert stub.requests[0]["headers"]["host"] == f"webhook.test:{stub.port}"


async def test_method_override_is_honoured(replay_client, stub):
    event_id = await seed_event(replay_client)
    await queue(replay_client, event_id, f"{stub.url}/hook", method="PUT")
    await run_once(replay_client.db, replay_client.settings)

    assert stub.requests[0]["method"] == "PUT"


# --- header handling ----------------------------------------------------


async def test_credentials_are_never_forwarded_but_signatures_are(replay_client, stub):
    event_id = await seed_event(
        replay_client,
        headers={
            "authorization": "Bearer super-secret-token",
            "cookie": "session=abc123",
            "x-hub-signature-256": "sha256=deadbeef",
            "x-github-event": "push",
        },
    )
    await queue(replay_client, event_id, f"{stub.url}/hook")
    await run_once(replay_client.db, replay_client.settings)

    sent = stub.requests[0]["headers"]
    assert "authorization" not in sent
    assert "cookie" not in sent
    assert sent["x-hub-signature-256"] == "sha256=deadbeef"
    assert sent["x-github-event"] == "push"


# --- retries ------------------------------------------------------------


async def test_server_errors_are_retried(replay_client, stub):
    stub.enqueue(500)
    event_id = await seed_event(replay_client)
    await queue(replay_client, event_id, f"{stub.url}/hook")

    await run_once(replay_client.db, replay_client.settings)
    record = await latest(replay_client, event_id)

    assert record["state"] == "pending", "a 5xx must be queued for another attempt"
    assert record["attempt"] == 1


async def test_client_errors_are_never_retried(replay_client, stub):
    stub.enqueue(404)
    event_id = await seed_event(replay_client)
    await queue(replay_client, event_id, f"{stub.url}/hook")

    await run_once(replay_client.db, replay_client.settings)
    record = await latest(replay_client, event_id)

    assert record["state"] == "failed"
    assert record["attempt"] == 1
    assert not await run_once(replay_client.db, replay_client.settings), "must not requeue"


async def test_retries_are_exhausted_then_the_replay_fails(replay_client, stub):
    for _ in range(3):
        stub.enqueue(503)
    event_id = await seed_event(replay_client)
    await queue(replay_client, event_id, f"{stub.url}/hook")

    for _ in range(3):
        await make_due(replay_client)
        await run_once(replay_client.db, replay_client.settings)

    record = await latest(replay_client, event_id)
    assert record["state"] == "failed"
    assert record["attempt"] == 3, "max_retries=2 means three attempts in total"
    assert len(stub.requests) == 3


async def test_a_retry_can_succeed(replay_client, stub):
    stub.enqueue(500)
    stub.enqueue(200)
    event_id = await seed_event(replay_client)
    await queue(replay_client, event_id, f"{stub.url}/hook")

    await run_once(replay_client.db, replay_client.settings)
    await make_due(replay_client)
    await run_once(replay_client.db, replay_client.settings)

    record = await latest(replay_client, event_id)
    assert record["state"] == "success"
    assert record["attempt"] == 2


async def test_timeouts_are_retryable(make_client, stub):
    stub.enqueue(200, delay=1.5)
    async for client in make_client(replay=replay_config(timeout=1)):
        await login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
        event_id = await seed_event(client)
        await queue(client, event_id, f"{stub.url}/slow")

        await run_once(client.db, client.settings)
        record = await latest(client, event_id)

        assert record["state"] == "pending"
        assert "Timed out" in record["error"]
        break


async def test_a_blocked_destination_is_not_retried(make_client, stub):
    # Queued while permissive, executed while strict: the worker must still refuse
    async for client in make_client(replay=replay_config()):
        await login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
        event_id = await seed_event(client)
        await queue(client, event_id, f"{stub.url}/hook")

        client.settings.replay.allow_private_networks = False
        await run_once(client.db, client.settings)

        record = await latest(client, event_id)
        assert record["state"] == "failed", "a rejected destination must not be retried"
        assert "blocked address" in record["error"]
        assert stub.requests == []
        break


# --- redirects ----------------------------------------------------------


async def test_redirects_are_not_followed_by_default(replay_client, stub):
    stub.redirect("/elsewhere")
    event_id = await seed_event(replay_client)
    await queue(replay_client, event_id, f"{stub.url}/hook")
    await run_once(replay_client.db, replay_client.settings)

    assert len(stub.requests) == 1
    assert (await latest(replay_client, event_id))["response"]["status_code"] == 302


async def test_redirects_are_followed_when_enabled(make_client, stub):
    stub.redirect("/final")
    stub.enqueue(200, body=b"arrived")
    async for client in make_client(replay=replay_config(allow_redirects=True)):
        await login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
        event_id = await seed_event(client)
        await queue(client, event_id, f"{stub.url}/hook")
        await run_once(client.db, client.settings)

        assert [r["path"] for r in stub.requests] == ["/hook", "/final"]
        assert (await latest(client, event_id))["state"] == "success"
        break


async def test_every_redirect_hop_is_revalidated(make_client, stub):
    # The first hop is allowed; the second points somewhere validation must refuse
    stub.redirect("file:///etc/passwd")
    async for client in make_client(replay=replay_config(allow_redirects=True)):
        await login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
        event_id = await seed_event(client)
        await queue(client, event_id, f"{stub.url}/hook")
        await run_once(client.db, client.settings)

        record = await latest(client, event_id)
        assert record["state"] == "failed"
        assert "not allowed" in record["error"]
        break


async def test_a_redirect_loop_is_bounded(make_client, stub):
    async for client in make_client(replay=replay_config(allow_redirects=True, max_redirects=2)):
        await login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
        event_id = await seed_event(client)
        stub.default.status = 302
        stub.default.headers = {"Location": "/loop"}
        stub.default.body = b""

        await queue(client, event_id, f"{stub.url}/hook")
        await run_once(client.db, client.settings)

        assert len(stub.requests) == 3, "one initial request plus max_redirects hops"
        assert "Too many redirects" in (await latest(client, event_id))["error"]
        break


# --- response handling --------------------------------------------------


async def test_large_responses_are_truncated(make_client, stub):
    stub.enqueue(200, body=b"x" * 5000)
    async for client in make_client(replay=replay_config(max_response_size=100)):
        await login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
        event_id = await seed_event(client)
        await queue(client, event_id, f"{stub.url}/hook")
        await run_once(client.db, client.settings)

        response = (await latest(client, event_id))["response"]
        assert response["truncated"] is True
        assert len(response["body"]) <= 200
        break


# --- queue mechanics ----------------------------------------------------


async def test_an_empty_queue_does_no_work(replay_client):
    assert not await run_once(replay_client.db, replay_client.settings)


async def test_a_stale_lease_is_reclaimed(replay_client, stub):
    event_id = await seed_event(replay_client)
    await queue(replay_client, event_id, f"{stub.url}/hook")

    # Simulate a worker that claimed the job and died
    stale = datetime.now(UTC) - timedelta(seconds=replay_client.settings.replay.lease_timeout + 5)
    await replay_client.db.replays.update_many(
        {}, {"$set": {"state": "running", "leased_at": stale}}
    )

    assert await run_once(replay_client.db, replay_client.settings)
    assert (await latest(replay_client, event_id))["state"] == "success"


async def test_a_replay_whose_event_vanished_fails_cleanly(replay_client, stub):
    event_id = await seed_event(replay_client)
    await queue(replay_client, event_id, f"{stub.url}/hook")
    await replay_client.db.events.delete_many({})

    assert await run_once(replay_client.db, replay_client.settings)
    assert stub.requests == []

    # The history endpoint 404s along with its event, so read the record directly
    record = await replay_client.db.replays.find_one({})
    assert record["state"] == "failed"
    assert "no longer exists" in record["error"]


# --- api and ui ---------------------------------------------------------


async def test_the_api_rejects_an_internal_destination(make_client, stub):
    async for client in make_client(replay=replay_config(allow_private_networks=False)):
        await login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
        event_id = await seed_event(client)

        response = await client.post(
            f"/api/events/{event_id}/replay",
            json={"destination": "http://169.254.169.254/latest/meta-data/"},
        )
        assert response.status_code == 400
        assert await client.db.replays.count_documents({}) == 0, "nothing may be queued"
        break


async def test_the_api_rejects_an_unknown_event(replay_client, stub):
    response = await replay_client.post(
        "/api/events/deadbeefdeadbeefdeadbeef/replay", json={"destination": f"{stub.url}/hook"}
    )
    assert response.status_code == 404


async def test_replay_is_refused_when_disabled(make_client, stub):
    async for client in make_client(replay=replay_config(enabled=False)):
        await login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
        event_id = await seed_event(client)

        response = await client.post(
            f"/api/events/{event_id}/replay", json={"destination": f"{stub.url}/hook"}
        )
        assert response.status_code == 403
        break


async def test_the_replays_tab_shows_history(replay_client, stub):
    event_id = await seed_event(replay_client)
    await queue(replay_client, event_id, f"{stub.url}/hook")
    await run_once(replay_client.db, replay_client.settings)

    page = await replay_client.get(f"/events/{event_id}?tab=replays")
    assert page.status_code == 200
    assert f"{stub.url}/hook" in page.text
    assert "success" in page.text


async def test_the_replay_form_reports_a_blocked_destination(make_client, stub):
    async for client in make_client(replay=replay_config(allow_private_networks=False)):
        await login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
        event_id = await seed_event(client)

        page = await client.post(
            f"/events/{event_id}/replay",
            data={"destination": "http://127.0.0.1:9/hook", "method": "POST"},
        )
        assert page.status_code == 200
        assert "blocked address" in page.text
        assert await client.db.replays.count_documents({}) == 0
        break
