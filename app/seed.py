"""
Seed the inbox with realistic demo data.

Endpoints are written straight to the database, but every event is sent through the
real HTTP ingest path, so signatures, search terms and retention all behave as normal.
Timestamps are spread backwards afterwards: an inbox where everything arrived in the
same second tells you nothing about how the tool looks in use.
"""

import asyncio
import hashlib
import hmac
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from app.config import Settings
from app.db import Database, Doc, create_client
from app.log import configure, get_logger
from app.models.endpoint import utcnow

logger = get_logger(__name__)
EXAMPLES = Path(__file__).parent.parent / "examples"

GITHUB_SECRET = "github-demo-secret"
STRIPE_SECRET = "stripe-demo-secret"

ENDPOINTS = [
    {
        "name": "github",
        "path": "github",
        "enabled": True,
        "authentication": {
            "type": "hmac_sha256",
            "header": "x-hub-signature-256",
            "signature_prefix": "sha256=",
        },
        "secret": GITHUB_SECRET,
        "allowed_methods": ["POST"],
        "max_payload_size": None,
        "retention_days": None,
    },
    {
        "name": "stripe",
        "path": "stripe",
        "enabled": True,
        "authentication": {
            "type": "hmac_sha256",
            "header": "stripe-signature",
            "signature_prefix": "",
        },
        "secret": STRIPE_SECRET,
        "allowed_methods": ["POST"],
        "max_payload_size": None,
        "retention_days": 90,
    },
    {
        "name": "custom",
        "path": "custom",
        "enabled": True,
        "authentication": {"type": "none", "header": None, "signature_prefix": "sha256="},
        "secret": None,
        "allowed_methods": ["GET", "POST", "PUT", "PATCH", "DELETE"],
        "allowed_methods_note": None,
        "max_payload_size": None,
        "retention_days": 7,
    },
]

# (directory, filename stem, minutes back from now, extra query string)
TIMELINE = [
    ("custom", "user.created", 5760, ""),
    ("stripe", "checkout.session.completed", 5700, "attempt=1"),
    ("github", "push", 5580, ""),
    ("custom", "chekout", 4320, ""),
    ("custom", "checkout_session", 4310, ""),
    ("stripe", "payment_intent.succeeded", 4200, ""),
    ("github", "pull_request", 2880, ""),
    ("custom", "checkout", 2820, ""),
    ("stripe", "checkout.session.expired", 1440, "attempt=2"),
    ("custom", "order.updated", 1380, "source=admin"),
    ("github", "issues", 720, ""),
    ("stripe", "invoice.payment_failed", 240, "attempt=3"),
    ("custom", "user.created", 90, ""),
    ("stripe", "checkout.session.completed", 30, "attempt=1"),
]

SECRETS = {"github": GITHUB_SECRET, "stripe": STRIPE_SECRET}
SIGNATURE_HEADER = {"github": "x-hub-signature-256", "stripe": "stripe-signature"}
PREFIX = {"github": "sha256=", "stripe": ""}


async def reset(db: Database) -> None:
    # Endpoints are left alone: wiping them would take the auto-seeded `demo`
    # endpoint with them, and the quick start in the README posts to it
    for collection in ("events", "replays"):
        await db[collection].delete_many({})


async def ensure_endpoints(db: Database) -> None:
    now = utcnow()
    for endpoint in ENDPOINTS:
        document = {k: v for k, v in endpoint.items() if k != "allowed_methods_note"}
        await db.endpoints.update_one(
            {"path": document["path"]},
            {"$set": document, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
    await db.endpoints.update_many({}, {"$set": {"updated_at": now}})


def sign(endpoint: str, body: bytes) -> dict[str, str]:
    secret = SECRETS.get(endpoint)
    if not secret:
        return {}
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {SIGNATURE_HEADER[endpoint]: PREFIX[endpoint] + digest}


async def send(client: httpx.AsyncClient, endpoint: str, name: str, query: str) -> str | None:
    payload = (EXAMPLES / endpoint / f"{name}.json").read_bytes()
    # Compact, so the stored raw body matches what a real sender would transmit
    body = json.dumps(json.loads(payload), separators=(",", ":")).encode()

    url = f"/webhooks/{endpoint}" + (f"?{query}" if query else "")
    headers = {
        "content-type": "application/json",
        "user-agent": f"{endpoint.capitalize()}-Hookshot/1.0",
        "x-webhook-event": name,
        **sign(endpoint, body),
    }
    response = await client.post(url, content=body, headers=headers)
    if response.status_code != 202:
        logger.warning("seed.rejected", endpoint=endpoint, status=response.status_code)
        return None
    event_id: str = response.json()["id"]
    return event_id


async def backdate(db: Database, event_id: str, minutes: int) -> None:
    from bson import ObjectId

    received = datetime.now(UTC) - timedelta(minutes=minutes)
    document: Doc | None = await db.events.find_one({"_id": ObjectId(event_id)})
    if document is None:
        return
    changes: Doc = {"received_at": received}
    if "expires_at" in document:
        # Keep the retention window the same length, just anchored to the new arrival
        window = document["expires_at"] - document["received_at"]
        changes["expires_at"] = received + window
    await db.events.update_one({"_id": ObjectId(event_id)}, {"$set": changes})


async def seed(db: Database, base_url: str) -> int:
    await ensure_endpoints(db)
    seeded = 0
    async with httpx.AsyncClient(base_url=base_url, timeout=10) as client:
        for endpoint, name, minutes, query in TIMELINE:
            event_id = await send(client, endpoint, name, query)
            if event_id:
                await backdate(db, event_id, minutes)
                seeded += 1
    return seeded


async def main() -> None:
    settings = Settings()
    configure(settings.log_level)
    base_url = f"http://localhost:{settings.app_port}"

    client = create_client(settings.mongo_uri)
    db = client[settings.mongo_database]
    try:
        if "--reset" in sys.argv:
            await reset(db)
            logger.info("seed.reset")
        seeded = await seed(db, base_url)
        logger.info("seed.done", events=seeded, endpoints=len(ENDPOINTS))
        print(
            f"\n  Seeded {seeded} events across {len(ENDPOINTS)} endpoints.\n"
            f"\n  Try searching for 'chekout' at /events to see fuzzy ranking.\n"
            f"  Signing secrets: github='{GITHUB_SECRET}', stripe='{STRIPE_SECRET}'\n",
            flush=True,
        )
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
