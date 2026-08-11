import csv
import io

import pytest

from app.config import LimitsConfig
from app.export import csv_safe
from app.ingest.parser import nesting_depth, parse_body
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, login
from tests.helpers import create_endpoint

pytestmark = pytest.mark.integration

JSON = {"content-type": "application/json"}


# --- nesting depth -------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"{}", 1),
        (b"[[[]]]", 3),
        (b'{"a":{"b":[1,2]}}', 3),
        (b'{"a":"[[[["}', 1),
        (b'{"a":"\\"[["}', 1),
        (b"", 0),
    ],
)
def test_nesting_depth_counts_structure_not_string_contents(raw, expected):
    assert nesting_depth(raw) == expected


def test_over_nested_json_is_not_parsed():
    deep = b"[" * 5000 + b"]" * 5000
    assert parse_body(deep, "application/json", max_depth=100) is None


def test_json_within_the_limit_still_parses():
    nested = b"[" * 50 + b"]" * 50
    assert parse_body(nested, "application/json", max_depth=100) is not None


async def test_a_deeply_nested_payload_does_not_crash_ingestion(make_client):
    # Unauthenticated endpoint: a stack overflow here would be a free 500 generator
    async for client in make_client(limits=LimitsConfig(max_json_depth=50)):
        await login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
        await create_endpoint(client, name="deep", path="deep")

        payload = b"[" * 20000 + b"]" * 20000
        response = await client.post("/webhooks/deep", content=payload, headers=JSON)

        assert response.status_code == 202, "the event must be accepted, not blow up"

        stored = await client.db.events.find_one({})
        assert stored["request"]["body"] is None, "too deep to walk safely"
        assert stored["request"]["raw_body"], "the original bytes are still kept"
        break


async def test_the_service_survives_repeated_nesting_attacks(make_client):
    async for client in make_client(limits=LimitsConfig(max_json_depth=50)):
        await login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
        await create_endpoint(client, name="deep", path="deep")

        payload = b"[" * 10000 + b"]" * 10000
        for _ in range(5):
            assert (
                await client.post("/webhooks/deep", content=payload, headers=JSON)
            ).status_code == 202

        assert (await client.get("/health")).status_code == 200
        break


# --- spreadsheet formula injection ---------------------------------------


@pytest.mark.parametrize("payload", ["=cmd|'/c calc'!A1", "+1+1", "-1+1", "@SUM(A1)", "\tx", "\rx"])
def test_formula_prefixes_are_neutralised(payload):
    assert csv_safe(payload).startswith("'")


@pytest.mark.parametrize("payload", ["normal", "checkout.session.completed", "", "1+1"])
def test_ordinary_values_are_untouched(payload):
    assert csv_safe(payload) == payload


def test_non_strings_pass_through():
    assert csv_safe(202) == 202
    assert csv_safe(None) is None


async def test_a_malicious_event_type_cannot_smuggle_a_formula_into_csv(authed_client):
    await create_endpoint(authed_client, name="calc", path="calc")
    await authed_client.post(
        "/webhooks/calc",
        json={"x": 1},
        headers={"x-webhook-event": "=cmd|'/c calc'!A1"},
    )

    export = await authed_client.get("/events/export?format=csv")
    rows = list(csv.reader(io.StringIO(export.text)))
    column = rows[0].index("event_type")
    values = [row[column] for row in rows[1:] if row[column]]

    assert values, "the event must still be exported"
    assert not any(value[0] in "=+-@" for value in values), "no cell may open with a formula"
    assert any(value.startswith("'=cmd") for value in values), "the value is kept, just defused"
