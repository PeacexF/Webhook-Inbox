import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import chain
from typing import Protocol

from bson import ObjectId
from bson.errors import InvalidId

from app.db import Database, Doc
from app.search.tokenize import normalize, split_terms, trigrams

EXACT_SCORE = 100.0
PREFIX_SCORE = 60.0
TOKEN_SCORE = 40.0
FUZZY_SCORE = 30.0
FUZZY_MIN_RATIO = 0.4
MIN_SCORE = FUZZY_SCORE * FUZZY_MIN_RATIO

TOKENS = {"$ifNull": ["$search.tokens", []]}
TRIGRAMS = {"$ifNull": ["$search.trigrams", []]}


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _maybe_object_id(value: str) -> ObjectId | None:
    try:
        return ObjectId(value)
    except InvalidId, TypeError:
        return None


@dataclass(frozen=True)
class Filters:
    endpoint: str | None = None
    status: str | None = None
    method: str | None = None
    event_type: str | None = None
    date_from: str | None = None
    date_to: str | None = None

    def to_query(self) -> Doc:
        query: Doc = {}
        if self.endpoint and (endpoint_id := _maybe_object_id(self.endpoint)):
            query["endpoint.id"] = endpoint_id
        if self.status:
            query["processing.status"] = self.status
        if self.method:
            query["request.method"] = self.method.upper()
        if self.event_type:
            query["event_type"] = self.event_type
        received: Doc = {}
        if start := _parse_date(self.date_from):
            received["$gte"] = start
        if end := _parse_date(self.date_to):
            # The input is a plain date, so include everything within that day
            received["$lt"] = end + timedelta(days=1)
        if received:
            query["received_at"] = received
        return query

    @property
    def active(self) -> bool:
        return bool(self.to_query())


@dataclass(frozen=True)
class Cursor:
    received_at: datetime
    score: float | None = None
    event_id: ObjectId | None = None

    def encode(self) -> str:
        if self.score is None:
            return self.received_at.isoformat()
        return f"{self.score}|{self.received_at.isoformat()}|{self.event_id}"

    @classmethod
    def decode(cls, value: str | None) -> Cursor | None:
        if not value:
            return None
        parts = value.split("|")
        try:
            if len(parts) == 1:
                return cls(datetime.fromisoformat(parts[0]))
            score, received_at, event_id = parts
            return cls(datetime.fromisoformat(received_at), float(score), ObjectId(event_id))
        except ValueError, InvalidId:
            return None

    @classmethod
    def after(cls, document: Doc, scored: bool) -> Cursor:
        if not scored:
            return cls(document["received_at"])
        return cls(document["received_at"], document["score"], document["_id"])


def _candidate_clause(exact: str, terms: list[str], grams: list[str]) -> Doc:
    # Each branch is served by a multikey index, so Mongo can OR the scans
    clauses: list[Doc] = [
        {"search.tokens": exact},
        {"search.tokens": {"$regex": f"^{re.escape(exact)}"}},
    ]
    if terms:
        clauses.append({"search.tokens": {"$all": terms}})
    if grams:
        clauses.append({"search.trigrams": {"$in": grams}})
    return {"$or": clauses}


def _score_expression(exact: str, terms: list[str], grams: list[str]) -> Doc:
    prefix = {
        "$anyElementTrue": {
            "$map": {
                "input": TOKENS,
                "as": "token",
                "in": {"$eq": [{"$substrCP": ["$$token", 0, len(exact)]}, exact]},
            }
        }
    }
    ratio: Doc | float = 0.0
    if grams:
        ratio = {"$divide": [{"$size": {"$setIntersection": [TRIGRAMS, grams]}}, float(len(grams))]}
    return {
        "$switch": {
            "branches": [
                {"case": {"$in": [exact, TOKENS]}, "then": EXACT_SCORE},
                {"case": prefix, "then": PREFIX_SCORE},
                {"case": {"$setIsSubset": [terms, TOKENS]}, "then": TOKEN_SCORE},
            ],
            "default": {"$multiply": [FUZZY_SCORE, ratio]},
        }
    }


def _cursor_clause(cursor: Cursor) -> Doc:
    # Keyset over the full sort key, so deep pages cost the same as the first
    return {
        "$or": [
            {"score": {"$lt": cursor.score}},
            {"score": cursor.score, "received_at": {"$lt": cursor.received_at}},
            {
                "score": cursor.score,
                "received_at": cursor.received_at,
                "_id": {"$lt": cursor.event_id},
            },
        ]
    }


class SearchBackend(Protocol):
    async def search(
        self, db: Database, query: str, filters: Filters, limit: int, cursor: Cursor | None
    ) -> list[Doc]: ...


class TrigramBackend:
    """Ranks with the tokens/trigrams multikey indexes - no text index needed."""

    def build_pipeline(
        self, query: str, filters: Filters, limit: int, cursor: Cursor | None
    ) -> list[Doc]:
        exact = normalize(query)
        terms = list(dict.fromkeys(split_terms(exact)))
        grams = list(dict.fromkeys(chain.from_iterable(trigrams(t) for t in terms)))

        pipeline: list[Doc] = [
            {"$match": {**filters.to_query(), **_candidate_clause(exact, terms, grams)}},
            {"$addFields": {"score": _score_expression(exact, terms, grams)}},
            {"$match": {"score": {"$gte": MIN_SCORE}}},
        ]
        if cursor and cursor.score is not None:
            pipeline.append({"$match": _cursor_clause(cursor)})
        pipeline += [
            {"$sort": {"score": -1, "received_at": -1, "_id": -1}},
            {"$limit": limit},
        ]
        return pipeline

    async def search(
        self, db: Database, query: str, filters: Filters, limit: int, cursor: Cursor | None
    ) -> list[Doc]:
        pipeline = self.build_pipeline(query, filters, limit, cursor)
        return [doc async for doc in await db.events.aggregate(pipeline)]


backend: SearchBackend = TrigramBackend()


async def list_events(
    db: Database, filters: Filters, limit: int, cursor: Cursor | None
) -> list[Doc]:
    # the index on received_at already gives the right order
    query = filters.to_query()
    if cursor:
        query["received_at"] = {**query.get("received_at", {}), "$lt": cursor.received_at}
    return [doc async for doc in db.events.find(query).sort("received_at", -1).limit(limit)]


async def find_events(
    db: Database, query: str, filters: Filters, limit: int, cursor: Cursor | None
) -> tuple[list[Doc], bool]:
    # Returns the page + whether results carry relevance
    if query.strip():
        return await backend.search(db, query, filters, limit, cursor), True
    return await list_events(db, filters, limit, cursor), False
