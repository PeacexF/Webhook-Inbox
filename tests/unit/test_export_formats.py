import csv
import io
import json
from datetime import UTC, datetime

from bson import ObjectId

from app.export import CSV_COLUMNS, csv_row, public, stream_csv, stream_json, stream_jsonl

RECEIVED = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def event(**overrides):
    document = {
        "_id": ObjectId(),
        "received_at": RECEIVED,
        "endpoint": {"id": ObjectId(), "name": "stripe"},
        "event_type": "invoice.paid",
        "request": {
            "method": "POST",
            "headers": {"content-type": "application/json"},
            "query": {},
            "body": {"amount": 100},
            "raw_body": '{"amount":100}',
            "content_type": "application/json",
            "body_size": 14,
        },
        "processing": {"status": "received", "response_status": 202},
        "metadata": {"source_ip": "10.0.0.1", "user_agent": "curl"},
        "search": {"tokens": ["invoice"], "trigrams": [" in"]},
    }
    return document | overrides


async def rows(*documents):
    for document in documents:
        yield document


async def collect(generator):
    return "".join([chunk async for chunk in generator])


def test_public_drops_the_internal_search_index():
    assert "search" not in public(event())


def test_public_unescapes_restricted_keys():
    stored = event()
    stored["request"]["body"] = {"a．b": 1, "＄set": 2}
    assert public(stored)["request"]["body"] == {"a.b": 1, "$set": 2}


def test_csv_row_matches_the_header_width():
    assert len(csv_row(event())) == len(CSV_COLUMNS)


async def test_datetimes_are_serialised_as_iso():
    line = json.loads(await collect(stream_jsonl(rows(event()))))
    assert line["received_at"] == "2026-08-11T12:00:00+00:00"


async def test_object_ids_become_strings():
    line = json.loads(await collect(stream_jsonl(rows(event()))))
    assert isinstance(line["id"], str)


async def test_json_array_stays_valid_for_one_many_and_zero_rows():
    assert len(json.loads(await collect(stream_json(rows(event()))))) == 1
    assert len(json.loads(await collect(stream_json(rows(event(), event(), event()))))) == 3
    assert json.loads(await collect(stream_json(rows()))) == []


async def test_csv_quotes_bodies_containing_commas_and_newlines():
    awkward = event()
    awkward["request"]["raw_body"] = 'a,b\n"quoted"'

    parsed = list(csv.reader(io.StringIO(await collect(stream_csv(rows(awkward))))))
    assert len(parsed) == 2, "an awkward body must not split into extra rows"
    assert parsed[1][-1] == 'a,b\n"quoted"'


async def test_csv_writes_a_header_even_with_no_rows():
    parsed = list(csv.reader(io.StringIO(await collect(stream_csv(rows())))))
    assert parsed == [list(CSV_COLUMNS)]


# --- the streaming promise ----------------------------------------------


def counting_source(total=100):
    state = {"consumed": 0}

    async def source():
        for _ in range(total):
            state["consumed"] += 1
            yield event()

    return state, source()


async def test_jsonl_reads_only_what_it_has_emitted():
    state, source = counting_source()
    generator = stream_jsonl(source)

    await anext(generator)
    await anext(generator)

    # Two lines out means two documents read, not a hundred buffered in memory
    assert state["consumed"] == 2
    await generator.aclose()


async def test_json_emits_the_opening_bracket_before_reading_anything():
    state, source = counting_source()
    generator = stream_json(source)

    assert await anext(generator) == "[\n"
    assert state["consumed"] == 0

    await anext(generator)
    assert state["consumed"] == 1
    await generator.aclose()


async def test_csv_emits_the_header_before_reading_anything():
    state, source = counting_source()
    generator = stream_csv(source)

    assert (await anext(generator)).startswith("id,")
    assert state["consumed"] == 0
    await generator.aclose()
