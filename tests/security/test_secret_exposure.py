import pytest

from app.auth import SESSION_COOKIE
from app.log import REDACTED, SENSITIVE_KEYS, redact
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME
from tests.helpers import create_endpoint, sign

pytestmark = pytest.mark.integration

SECRET = "s3cr3t-endpoint-signing-key"


async def signed_endpoint(client):
    return await create_endpoint(
        client,
        name="signed",
        path="signed",
        authentication={"type": "hmac_sha256"},
        secret=SECRET,
    )


# --- the redaction processor itself --------------------------------------


@pytest.mark.parametrize("key", sorted(SENSITIVE_KEYS))
def test_every_sensitive_key_is_redacted_at_the_top_level(key):
    assert redact(None, "", {key: "leak"})[key] == REDACTED


@pytest.mark.parametrize("key", sorted(SENSITIVE_KEYS))
def test_every_sensitive_key_is_redacted_one_level_down(key):
    event = redact(None, "", {"headers": {key: "leak"}})
    assert event["headers"][key] == REDACTED


def test_redaction_matches_prefixed_and_suffixed_variants():
    event = redact(None, "", {"X-Hub-Signature-256": "leak", "user_password": "leak"})
    assert set(event.values()) == {REDACTED}


def test_redaction_leaves_ordinary_fields_alone():
    assert redact(None, "", {"endpoint": "github", "count": 3}) == {
        "endpoint": "github",
        "count": 3,
    }


# --- secrets in responses -------------------------------------------------


async def test_an_endpoint_secret_never_comes_back_from_the_api(authed_client):
    created = await signed_endpoint(authed_client)
    assert SECRET not in str(created)

    listing = await authed_client.get("/api/endpoints")
    detail = await authed_client.get(f"/api/endpoints/{created['id']}")
    assert SECRET not in listing.text
    assert SECRET not in detail.text
    assert detail.json()["has_secret"] is True, "its existence is shown, its value is not"


async def test_an_endpoint_secret_never_renders_in_the_dashboard(authed_client):
    created = await signed_endpoint(authed_client)

    for path in ("/endpoints", f"/endpoints/{created['id']}"):
        page = await authed_client.get(path)
        assert SECRET not in page.text


async def test_the_password_hash_never_leaves_the_process(authed_client):
    for path in ("/api/auth/me", "/settings"):
        response = await authed_client.get(path)
        assert "argon2" not in response.text
        assert "password_hash" not in response.text


async def test_session_tokens_are_stored_hashed(authed_client):
    cookie = authed_client.cookies.get(SESSION_COOKIE)
    stored = await authed_client.db.sessions.find_one({})

    assert cookie, "the test client must hold a session"
    assert cookie not in str(stored), "a database dump must not be replayable as a session"
    assert stored["token_hash"] != cookie


async def test_the_admin_password_is_never_stored_in_the_clear(authed_client):
    user = await authed_client.db.users.find_one({"username": ADMIN_USERNAME})
    assert ADMIN_PASSWORD not in str(user)
    assert user["password_hash"].startswith("$argon2")


# --- secrets in logs ------------------------------------------------------


async def test_no_secret_reaches_the_logs_during_a_signed_delivery(authed_client, capsys):
    await signed_endpoint(authed_client)
    body = b'{"event":"push"}'

    response = await authed_client.post(
        "/webhooks/signed",
        content=body,
        headers={
            "content-type": "application/json",
            "x-hub-signature-256": sign(SECRET, body),
            "authorization": "Bearer another-secret-value",
        },
    )
    assert response.status_code == 202

    output = capsys.readouterr()
    written = output.out + output.err
    assert SECRET not in written
    assert "another-secret-value" not in written


async def test_no_secret_reaches_the_logs_when_a_signature_fails(authed_client, capsys):
    await signed_endpoint(authed_client)

    response = await authed_client.post(
        "/webhooks/signed",
        content=b'{"event":"push"}',
        headers={"x-hub-signature-256": "sha256=wrong"},
    )
    assert response.status_code == 401
    assert SECRET not in capsys.readouterr().out


async def test_a_password_is_not_logged_when_sign_in_fails(authed_client, capsys):
    await authed_client.post(
        "/api/auth/login", json={"username": ADMIN_USERNAME, "password": "hunter2-not-logged"}
    )
    assert "hunter2-not-logged" not in capsys.readouterr().out
