import pytest

from tests.helpers import create_endpoint

pytestmark = pytest.mark.integration


def form(**overrides):
    base = {
        "name": "stripe",
        "path": "stripe",
        "auth_type": "none",
        "secret": "",
        "allowed_methods": "POST",
        "max_payload_size": "",
        "retention_days": "",
        "enabled": "on",
    }
    return base | overrides


async def test_retention_can_be_set_from_the_endpoint_page(authed_client):
    endpoint = await create_endpoint(authed_client, name="stripe", path="stripe")

    response = await authed_client.post(
        f"/endpoints/{endpoint['id']}", data=form(retention_days="7")
    )
    assert response.status_code == 204

    stored = await authed_client.db.endpoints.find_one({"path": "stripe"})
    assert stored["retention_days"] == 7


async def test_clearing_retention_falls_back_to_the_global_default(authed_client):
    endpoint = await create_endpoint(authed_client, name="stripe", path="stripe", retention_days=7)

    await authed_client.post(f"/endpoints/{endpoint['id']}", data=form(retention_days=""))

    stored = await authed_client.db.endpoints.find_one({"path": "stripe"})
    assert stored["retention_days"] is None


async def test_changing_retention_rewrites_stored_expiries(authed_client):
    endpoint = await create_endpoint(authed_client, name="stripe", path="stripe")
    await authed_client.post("/webhooks/stripe", json={"type": "ping"})

    await authed_client.post(f"/endpoints/{endpoint['id']}", data=form(retention_days="3"))

    event = await authed_client.db.events.find_one({"endpoint.name": "stripe"})
    assert (event["expires_at"] - event["received_at"]).days == 3


async def test_the_form_still_saves_the_other_fields(authed_client):
    # retention_days was inserted mid-signature; the neighbouring fields must not shift
    endpoint = await create_endpoint(authed_client, name="stripe", path="stripe")

    await authed_client.post(
        f"/endpoints/{endpoint['id']}",
        data=form(max_payload_size="2048", allowed_methods="POST, PUT", enabled="on"),
    )

    stored = await authed_client.db.endpoints.find_one({"path": "stripe"})
    assert stored["max_payload_size"] == 2048
    assert stored["allowed_methods"] == ["POST", "PUT"]
    assert stored["enabled"] is True


async def test_an_endpoint_can_still_be_disabled(authed_client):
    endpoint = await create_endpoint(authed_client, name="stripe", path="stripe")

    data = form()
    del data["enabled"]
    await authed_client.post(f"/endpoints/{endpoint['id']}", data=data)

    stored = await authed_client.db.endpoints.find_one({"path": "stripe"})
    assert stored["enabled"] is False


async def test_the_retention_field_is_rendered_with_its_current_value(authed_client):
    endpoint = await create_endpoint(authed_client, name="stripe", path="stripe", retention_days=14)

    page = await authed_client.get(f"/endpoints/{endpoint['id']}")
    assert 'name="retention_days"' in page.text
    assert 'value="14"' in page.text
