from datetime import datetime, timedelta

from app.config import RetentionConfig
from app.db import Database, Doc
from app.log import get_logger

logger = get_logger(__name__)


def retention_days(endpoint: Doc, config: RetentionConfig) -> int | None:
    if not config.enabled:
        return None
    override = endpoint.get("retention_days")
    return override if override else config.default_days


def expires_at(endpoint: Doc, config: RetentionConfig, received_at: datetime) -> datetime | None:
    days = retention_days(endpoint, config)
    return received_at + timedelta(days=days) if days else None


async def reapply(db: Database, endpoint: Doc, config: RetentionConfig) -> int:
    # Recompute expiry for an endpoint's stored events
    days = retention_days(endpoint, config)
    if days is None:
        # Unsetting the field is what stops the TTL monitor from ever deleting these
        result = await db.events.update_many(
            {"endpoint.id": endpoint["_id"]}, {"$unset": {"expires_at": ""}}
        )
    else:
        result = await db.events.update_many(
            {"endpoint.id": endpoint["_id"]},
            [
                {
                    "$set": {
                        "expires_at": {
                            "$dateAdd": {
                                "startDate": "$received_at",
                                "unit": "day",
                                "amount": days,
                            }
                        }
                    }
                }
            ],
        )
    logger.info(
        "retention.reapplied",
        endpoint=endpoint.get("name"),
        days=days,
        events=result.modified_count,
    )
    return int(result.modified_count)
