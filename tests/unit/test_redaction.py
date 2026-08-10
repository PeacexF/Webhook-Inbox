from webhook_inbox.log import REDACTED, redact


def test_redacts_sensitive_top_level_keys() -> None:
    result = redact(None, "info", {"event": "login", "password": "hunter2"})
    assert result["password"] == REDACTED
    assert result["event"] == "login"


def test_redacts_inside_nested_dicts() -> None:
    result = redact(None, "info", {"headers": {"authorization": "Bearer x", "accept": "*/*"}})
    assert result["headers"]["authorization"] == REDACTED
    assert result["headers"]["accept"] == "*/*"


def test_matches_are_case_insensitive_and_partial() -> None:
    result = redact(None, "info", {"X-Hub-Signature-256": "sha256=abc", "API_KEY": "k"})
    assert result["X-Hub-Signature-256"] == REDACTED
    assert result["API_KEY"] == REDACTED
