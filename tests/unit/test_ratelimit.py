from app.config import RateLimitConfig
from app.ratelimit import PRUNE_INTERVAL, RateLimiter, client_key


class FakeRequest:
    def __init__(self, host="1.2.3.4", headers=None):
        self.client = type("Client", (), {"host": host})()
        self.headers = headers or {}


def test_a_full_bucket_allows_exactly_its_capacity():
    limiter = RateLimiter(per_minute=5)
    assert [limiter.allow("k", now=0) for _ in range(5)] == [True] * 5
    assert limiter.allow("k", now=0) is False


def test_tokens_refill_over_time():
    limiter = RateLimiter(per_minute=60)  # one token per second
    for _ in range(60):
        limiter.allow("k", now=0)
    assert limiter.allow("k", now=0) is False

    assert limiter.allow("k", now=1) is True, "one second should buy one token"
    assert limiter.allow("k", now=1) is False


def test_refill_is_capped_at_capacity():
    limiter = RateLimiter(per_minute=5)
    limiter.allow("k", now=0)
    # An hour later the bucket is full, not overflowing
    assert [limiter.allow("k", now=3600) for _ in range(5)] == [True] * 5
    assert limiter.allow("k", now=3600) is False


def test_keys_are_independent():
    limiter = RateLimiter(per_minute=1)
    assert limiter.allow("a", now=0) is True
    assert limiter.allow("b", now=0) is True
    assert limiter.allow("a", now=0) is False


def test_disabled_limiter_always_allows():
    limiter = RateLimiter(per_minute=1, enabled=False)
    assert all(limiter.allow("k", now=0) for _ in range(100))


def test_a_zero_rate_disables_rather_than_blocking_everything():
    limiter = RateLimiter(per_minute=0)
    assert limiter.allow("k", now=0) is True


def test_retry_after_is_at_least_one_second():
    limiter = RateLimiter(per_minute=60)
    for _ in range(60):
        limiter.allow("k", now=0)
    assert limiter.retry_after("k", now=0) >= 1


def test_idle_buckets_are_pruned():
    limiter = RateLimiter(per_minute=60)
    limiter.allow("old", now=0)
    assert len(limiter._buckets) == 1

    # Far enough ahead that the bucket would have refilled completely anyway
    limiter.allow("new", now=PRUNE_INTERVAL + 120)
    assert "old" not in limiter._buckets, "unbounded keys would be a memory DoS"
    assert "new" in limiter._buckets


def test_active_buckets_survive_pruning():
    limiter = RateLimiter(per_minute=60)
    limiter.allow("busy", now=0)
    limiter.allow("busy", now=PRUNE_INTERVAL)
    limiter.allow("other", now=PRUNE_INTERVAL + 1)
    assert "busy" in limiter._buckets


# --- client identity -----------------------------------------------------


def test_the_socket_peer_is_used_by_default():
    config = RateLimitConfig(trust_forwarded_for=False)
    request = FakeRequest("9.9.9.9", {"x-forwarded-for": "1.1.1.1"})
    assert client_key(request, config) == "9.9.9.9"


def test_a_forged_forwarded_header_cannot_change_the_key_by_default():
    config = RateLimitConfig(trust_forwarded_for=False)
    forged = FakeRequest("9.9.9.9", {"x-forwarded-for": "5.5.5.5"})
    honest = FakeRequest("9.9.9.9")
    assert client_key(forged, config) == client_key(honest, config)


def test_the_last_forwarded_hop_is_used_when_trusted():
    # Earlier entries are client-supplied; only the last was appended by our proxy
    config = RateLimitConfig(trust_forwarded_for=True)
    request = FakeRequest("10.0.0.1", {"x-forwarded-for": "spoofed, 203.0.113.7"})
    assert client_key(request, config) == "203.0.113.7"


def test_a_missing_client_does_not_crash():
    config = RateLimitConfig()
    request = FakeRequest()
    request.client = None
    assert client_key(request, config) == "unknown"
