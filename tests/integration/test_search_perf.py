import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId

from app.search.query import Cursor, Filters, TrigramBackend, find_events
from app.search.tokenize import build

pytestmark = [pytest.mark.integration, pytest.mark.slow]

TOTAL = 100_000
BATCH = 5_000
EVENT_TYPES = [
    "checkout.session.completed",
    "payment_intent.succeeded",
    "invoice.paid",
    "customer.subscription.updated",
    "charge.refunded",
]


def synthetic(index: int, base: datetime) -> dict:
    event_type = EVENT_TYPES[index % len(EVENT_TYPES)]
    body = {"id": f"evt_{index}", "type": event_type, "amount": index}
    return {
        "endpoint": {"id": ObjectId(), "name": "stripe"},
        "received_at": base + timedelta(milliseconds=index),
        "event_type": event_type,
        "request": {
            "method": "POST",
            "headers": {"user-agent": "Stripe/1.0"},
            "query": {},
            "body": body,
            "raw_body": "{}",
            "raw_encoding": "utf-8",
            "content_type": "application/json",
            "body_size": 64,
        },
        "search": build("stripe", event_type, {"user-agent": "Stripe/1.0"}, {}, body),
        "processing": {"status": "received", "response_status": 202},
        "metadata": {"source_ip": "10.0.0.1", "user_agent": "Stripe/1.0"},
    }


@pytest.fixture
async def large_dataset(authed_client):
    db = authed_client.db
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for start in range(0, TOTAL, BATCH):
        await db.events.insert_many(
            [synthetic(i, base) for i in range(start, start + BATCH)], ordered=False
        )
    assert await db.events.count_documents({}) == TOTAL
    return authed_client


async def explain(db, pipeline) -> str:
    plan = await db.command(
        {
            "explain": {"aggregate": "events", "pipeline": pipeline, "cursor": {}},
            "verbosity": "queryPlanner",
        }
    )
    return json.dumps(plan, default=str)


async def test_ranked_search_uses_indexes(large_dataset):
    pipeline = TrigramBackend().build_pipeline("chekout", Filters(), 50, None)
    plan = await explain(large_dataset.db, pipeline)

    assert "COLLSCAN" not in plan
    assert "IXSCAN" in plan
    assert "search_trigrams" in plan


async def test_exact_search_uses_the_token_index(large_dataset):
    pipeline = TrigramBackend().build_pipeline("invoice.paid", Filters(), 50, None)
    plan = await explain(large_dataset.db, pipeline)

    assert "COLLSCAN" not in plan
    assert "search_tokens" in plan


async def test_filtered_listing_uses_an_index(large_dataset):
    filters = Filters(event_type="invoice.paid")
    plan = await explain(large_dataset.db, [{"$match": filters.to_query()}])

    assert "COLLSCAN" not in plan


async def test_typo_still_finds_the_real_events_at_scale(large_dataset):
    started = time.perf_counter()
    rows, _ = await find_events(large_dataset.db, "chekout", Filters(), 50, None)
    elapsed = time.perf_counter() - started

    assert rows, "the typo must still match at 100k events"
    assert all(row["event_type"] == "checkout.session.completed" for row in rows)
    assert elapsed < 10, f"fuzzy search took {elapsed:.1f}s"


async def test_deep_pagination_stays_cheap(large_dataset):
    filters = Filters()
    cursor = None
    timings = []

    for _ in range(5):
        started = time.perf_counter()
        rows, scored = await find_events(large_dataset.db, "", filters, 50, cursor)
        timings.append(time.perf_counter() - started)
        assert len(rows) == 50
        cursor = Cursor.after(rows[-1], scored)

    # Keyset pagination means the last page must not cost more than the first
    assert max(timings) < 2
