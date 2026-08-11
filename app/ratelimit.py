import time
from dataclasses import dataclass

from fastapi import Request

from app.config import RateLimitConfig

PRUNE_INTERVAL = 60.0


@dataclass
class Bucket:
    tokens: float
    updated: float


class RateLimiter:
    """
    Token bucket per key, held in memory.

    State is per process, so two app processes each allow the full rate.
    """

    def __init__(self, per_minute: int, enabled: bool = True) -> None:
        self.enabled = enabled and per_minute > 0
        self.capacity = float(per_minute)
        self.rate = per_minute / 60.0
        self._buckets: dict[str, Bucket] = {}
        self._pruned_at = 0.0

    def allow(self, key: str, now: float | None = None) -> bool:
        if not self.enabled:
            return True
        now = time.monotonic() if now is None else now
        self._prune(now)

        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = self._buckets[key] = Bucket(self.capacity, now)
        bucket.tokens = min(self.capacity, bucket.tokens + (now - bucket.updated) * self.rate)
        bucket.updated = now

        if bucket.tokens < 1.0:
            return False
        bucket.tokens -= 1.0
        return True

    def retry_after(self, key: str, now: float | None = None) -> int:
        bucket = self._buckets.get(key)
        if bucket is None or not self.enabled:
            return 1
        now = time.monotonic() if now is None else now
        missing = max(0.0, 1.0 - bucket.tokens)
        return max(1, int(missing / self.rate) + 1)

    def _prune(self, now: float) -> None:
        # Unbounded keys would be a memory DoS in their own right. A bucket idle long enough
        # to have fully refilled is identical to a fresh one, so dropping it loses nothing.
        if now - self._pruned_at < PRUNE_INTERVAL:
            return
        self._pruned_at = now
        idle_limit = self.capacity / self.rate if self.rate else PRUNE_INTERVAL
        self._buckets = {
            key: bucket
            for key, bucket in self._buckets.items()
            if now - bucket.updated < idle_limit
        }


def client_key(request: Request, config: RateLimitConfig) -> str:
    if config.trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            # The last hop is the one our own proxy appended; earlier entries are
            # whatever the client claimed and can be forged freely.
            return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"
