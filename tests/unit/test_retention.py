from datetime import UTC, datetime

from app.config import RetentionConfig
from app.retention import expires_at, retention_days

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
ON = RetentionConfig(enabled=True, default_days=30)
OFF = RetentionConfig(enabled=False, default_days=30)


def test_global_default_applies_without_an_override():
    assert retention_days({}, ON) == 30
    assert expires_at({}, ON, NOW) == datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def test_endpoint_override_wins():
    assert retention_days({"retention_days": 7}, ON) == 7
    assert expires_at({"retention_days": 7}, ON, NOW) == datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


def test_an_override_longer_than_the_default_is_honoured():
    assert retention_days({"retention_days": 90}, ON) == 90


def test_disabling_retention_beats_every_override():
    assert retention_days({"retention_days": 7}, OFF) is None
    assert expires_at({"retention_days": 7}, OFF, NOW) is None


def test_no_expiry_means_the_event_is_kept_forever():
    # A missing expires_at is what keeps the TTL monitor away from the document
    assert expires_at({}, OFF, NOW) is None
