from datetime import UTC, datetime, timedelta

import pytest

from app.search.backfill import backfill
from app.search.query import Cursor, Filters, find_events
from tests.helpers import create_endpoint

pytestmark = pytest.mark.integration

# Deliberate near-matches, per the fuzzy-search example in the plan
FIXTURES = [
    {"type": "checkout.session.completed", "amount": 4200, "currency": "usd"},
    {"type": "checkout_session", "note": "checkout session started"},
    {"type": "chekout", "note": "deliberate typo fixture"},
    {"type": "payment_intent.succeeded", "amount": 100},
    {"type": "invoice.paid", "customer": "acme"},
]


async def seed(client):
    await create_endpoint(client, name="stripe", path="stripe")
    for payload in FIXTURES:
        response = await client.post("/webhooks/stripe", json=payload)
        assert response.status_code == 202, response.text


async def search(client, query="", **filters):
    rows, _ = await find_events(client.db, query, Filters(**filters), 50, None)
    return rows


def types_of(rows):
    return [row["event_type"] for row in rows]


def result_rows(html):
    # The filter dropdown lists every event type, so only the table body proves a match
    _, _, rest = html.partition("<tbody>")
    body, _, _ = rest.partition("</tbody>")
    return body


async def test_ingest_writes_search_terms(authed_client):
    await seed(authed_client)
    event = await authed_client.db.events.find_one({"event_type": "invoice.paid"})
    assert "acme" in event["search"]["tokens"]
    assert "invoice.paid" in event["search"]["tokens"]
    assert event["search"]["trigrams"]


async def test_exact_match_scores_highest(authed_client):
    await seed(authed_client)
    rows = await search(authed_client, "checkout.session.completed")
    assert types_of(rows)[0] == "checkout.session.completed"
    assert rows[0]["score"] == 100


async def test_prefix_beats_plain_token_match(authed_client):
    await seed(authed_client)
    scores = {
        row["event_type"]: row["score"] for row in await search(authed_client, "checkout.session")
    }
    assert scores["checkout.session.completed"] == 60
    assert scores["checkout_session"] == 40


async def test_typo_finds_the_real_event_ranked_below_the_exact_match(authed_client):
    await seed(authed_client)
    rows = await search(authed_client, "chekout")
    types = types_of(rows)

    assert types[0] == "chekout", "the literal match must win"
    assert "checkout.session.completed" in types, "the typo must still find the real event"
    assert rows[0]["score"] > rows[types.index("checkout.session.completed")]["score"]


async def test_unrelated_query_matches_nothing(authed_client):
    await seed(authed_client)
    assert await search(authed_client, "zzzznothing") == []


async def test_search_covers_payload_values_not_just_event_type(authed_client):
    await seed(authed_client)
    assert types_of(await search(authed_client, "acme")) == ["invoice.paid"]


async def test_filter_by_event_type(authed_client):
    await seed(authed_client)
    rows = await search(authed_client, event_type="invoice.paid")
    assert types_of(rows) == ["invoice.paid"]


async def test_filter_by_endpoint(authed_client):
    await seed(authed_client)
    other = await create_endpoint(authed_client, name="github", path="github")
    await authed_client.post("/webhooks/github", json={"type": "push"})

    rows = await search(authed_client, endpoint=other["id"])
    assert types_of(rows) == ["push"]


async def test_filter_by_method(authed_client):
    await seed(authed_client)
    await create_endpoint(authed_client, name="any", path="any", allowed_methods=["GET", "POST"])
    await authed_client.get("/webhooks/any?probe=1")

    assert all(
        row["request"]["method"] == "GET" for row in await search(authed_client, method="get")
    )


async def test_filter_by_date_range(authed_client):
    await seed(authed_client)
    today = datetime.now(UTC).date()

    assert await search(authed_client, date_from=str(today))
    assert await search(authed_client, date_to=str(today))
    assert await search(authed_client, date_from=str(today + timedelta(days=1))) == []


async def test_filters_combine_with_the_query(authed_client):
    await seed(authed_client)
    rows = await search(authed_client, "checkout", event_type="checkout_session")
    assert types_of(rows) == ["checkout_session"]


async def test_keyset_pagination_does_not_repeat_or_skip(authed_client):
    await seed(authed_client)
    first, scored = await find_events(authed_client.db, "checkout", Filters(), 2, None)
    assert len(first) == 2

    cursor = Cursor.after(first[-1], scored)
    second, _ = await find_events(authed_client.db, "checkout", Filters(), 2, cursor)

    ids = [row["_id"] for row in first + second]
    assert len(ids) == len(set(ids)), "pages must not overlap"


async def test_listing_pagination_without_a_query(authed_client):
    await seed(authed_client)
    first, scored = await find_events(authed_client.db, "", Filters(), 2, None)
    second, _ = await find_events(
        authed_client.db, "", Filters(), 2, Cursor.after(first[-1], scored)
    )

    assert len(first) == 2
    assert not {row["_id"] for row in first} & {row["_id"] for row in second}


async def test_backfill_makes_old_events_searchable(authed_client):
    await seed(authed_client)
    legacy = await authed_client.db.events.find_one({"event_type": "invoice.paid"})
    await authed_client.db.events.update_one({"_id": legacy["_id"]}, {"$unset": {"search": ""}})

    assert types_of(await search(authed_client, "acme")) == []

    assert await backfill(authed_client.db) == 1
    assert types_of(await search(authed_client, "acme")) == ["invoice.paid"]


async def test_backfill_skips_events_that_already_have_terms(authed_client):
    await seed(authed_client)
    assert await backfill(authed_client.db) == 0
    assert await backfill(authed_client.db, rebuild=True) == len(FIXTURES)


async def test_search_page_renders_results(authed_client):
    await seed(authed_client)
    page = await authed_client.get("/events?q=chekout")

    assert page.status_code == 200
    assert "checkout.session.completed" in result_rows(page.text)
    assert "invoice.paid" not in result_rows(page.text)


async def test_search_page_filters_and_keeps_form_state(authed_client):
    await seed(authed_client)
    page = await authed_client.get("/events?type=invoice.paid")

    assert "invoice.paid" in result_rows(page.text)
    assert "checkout.session.completed" not in result_rows(page.text)
    assert 'value="invoice.paid" selected' in " ".join(page.text.split())


async def test_search_page_reports_no_matches(authed_client):
    await seed(authed_client)
    page = await authed_client.get("/events?q=zzzzznothing")
    assert "No events match" in page.text
