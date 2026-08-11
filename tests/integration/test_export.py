import csv
import io
import json

import pytest

from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, login
from tests.helpers import create_endpoint

pytestmark = pytest.mark.integration

FIXTURES = [
    {"type": "checkout.session.completed", "amount": 4200},
    {"type": "payment_intent.succeeded", "amount": 100},
    {"type": "invoice.paid", "customer": "acme"},
]


async def seed(client):
    await create_endpoint(client, name="stripe", path="stripe")
    for payload in FIXTURES:
        response = await client.post("/webhooks/stripe", json=payload)
        assert response.status_code == 202


async def fetch(client, query=""):
    response = await client.get(f"/events/export{query}")
    assert response.status_code == 200, response.text
    return response


async def test_jsonl_exports_one_object_per_line(authed_client):
    await seed(authed_client)
    response = await fetch(authed_client, "?format=jsonl")

    lines = [line for line in response.text.splitlines() if line]
    assert len(lines) == len(FIXTURES)
    assert {json.loads(line)["event_type"] for line in lines} == {f["type"] for f in FIXTURES}


async def test_json_exports_a_valid_array(authed_client):
    await seed(authed_client)
    response = await fetch(authed_client, "?format=json")

    rows = json.loads(response.text)
    assert len(rows) == len(FIXTURES)
    assert rows[0]["request"]["body"]["type"]


async def test_csv_exports_a_header_and_a_row_per_event(authed_client):
    await seed(authed_client)
    response = await fetch(authed_client, "?format=csv")

    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0][0] == "id"
    assert len(rows) == len(FIXTURES) + 1
    assert all(len(row) == len(rows[0]) for row in rows)


async def test_export_defaults_to_jsonl(authed_client):
    await seed(authed_client)
    response = await fetch(authed_client)
    assert response.headers["content-type"].startswith("application/x-ndjson")


async def test_export_is_sent_as_a_download(authed_client):
    await seed(authed_client)
    response = await fetch(authed_client, "?format=csv")

    assert "attachment" in response.headers["content-disposition"]
    assert ".csv" in response.headers["content-disposition"]
    assert response.headers["content-type"].startswith("text/csv")


async def test_an_unknown_format_is_rejected(authed_client):
    await seed(authed_client)
    assert (await authed_client.get("/events/export?format=xlsx")).status_code == 400


# --- filters carry over -------------------------------------------------


async def test_export_honours_the_event_type_filter(authed_client):
    await seed(authed_client)
    response = await fetch(authed_client, "?format=jsonl&type=invoice.paid")

    rows = [json.loads(line) for line in response.text.splitlines() if line]
    assert [row["event_type"] for row in rows] == ["invoice.paid"]


async def test_export_honours_the_search_query(authed_client):
    await seed(authed_client)
    response = await fetch(authed_client, "?format=jsonl&q=acme")

    rows = [json.loads(line) for line in response.text.splitlines() if line]
    assert [row["event_type"] for row in rows] == ["invoice.paid"]


async def test_export_honours_a_fuzzy_search_query(authed_client):
    await seed(authed_client)
    response = await fetch(authed_client, "?format=jsonl&q=chekout")

    rows = [json.loads(line) for line in response.text.splitlines() if line]
    assert [row["event_type"] for row in rows] == ["checkout.session.completed"]


async def test_an_empty_result_still_produces_valid_output(authed_client):
    await seed(authed_client)

    assert (await fetch(authed_client, "?format=jsonl&q=zzzznothing")).text.strip() == ""
    assert json.loads((await fetch(authed_client, "?format=json&q=zzzznothing")).text) == []

    csv_rows = list(csv.reader(io.StringIO((await fetch(authed_client, "?format=csv&q=zzz")).text)))
    assert len(csv_rows) == 1, "the header must still be written"


# --- content -------------------------------------------------------------


async def test_export_unescapes_restricted_body_keys(authed_client):
    await create_endpoint(authed_client, name="odd", path="odd")
    await authed_client.post("/webhooks/odd", json={"a.b": 1, "$set": 2})

    row = json.loads((await fetch(authed_client, "?format=jsonl")).text.splitlines()[0])
    assert row["request"]["body"] == {"a.b": 1, "$set": 2}


async def test_export_omits_the_internal_search_index(authed_client):
    await seed(authed_client)
    response = await fetch(authed_client, "?format=jsonl")

    assert "trigrams" not in response.text
    assert '"search"' not in response.text


async def test_export_requires_authentication(client):
    assert (await client.get("/events/export?format=jsonl")).status_code == 303


async def test_the_events_page_links_the_current_filters_into_the_export(authed_client):
    await seed(authed_client)
    page = await authed_client.get("/events?q=acme&type=invoice.paid")

    assert "/events/export?format=jsonl&amp;q=acme&amp;type=invoice.paid" in page.text


# --- retention -----------------------------------------------------------


async def test_events_are_stored_with_an_expiry(authed_client):
    await seed(authed_client)
    event = await authed_client.db.events.find_one({})

    expected = authed_client.settings.retention.default_days
    assert (event["expires_at"] - event["received_at"]).days == expected


async def test_the_ttl_index_exists(authed_client):
    indexes = await authed_client.db.events.index_information()
    assert indexes["event_ttl"]["expireAfterSeconds"] == 0


async def test_changing_an_endpoints_retention_rewrites_stored_expiries(authed_client):
    endpoint = await create_endpoint(authed_client, name="short", path="short")
    await authed_client.post("/webhooks/short", json={"type": "ping"})

    response = await authed_client.patch(
        f"/api/endpoints/{endpoint['id']}", json={"retention_days": 2}
    )
    assert response.status_code == 200

    event = await authed_client.db.events.find_one({"endpoint.name": "short"})
    assert (event["expires_at"] - event["received_at"]).days == 2


async def test_events_are_kept_forever_when_retention_is_disabled(make_client):
    from app.config import RetentionConfig

    async for client in make_client(retention=RetentionConfig(enabled=False)):
        await login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
        await create_endpoint(client, name="keep", path="keep")
        await client.post("/webhooks/keep", json={"type": "ping"})

        event = await client.db.events.find_one({})
        assert "expires_at" not in event, "no expiry means the TTL monitor never touches it"
        break
