from app.ingest.parser import (
    decode_raw,
    escape_keys,
    extract_event_type,
    parse_body,
    unescape_keys,
)


def test_escape_round_trips_restricted_keys() -> None:
    payload = {"a.b": 1, "$set": 2, "nested": [{"x.y": {"$z": 3}}]}
    escaped = escape_keys(payload)

    assert "a.b" not in escaped
    assert "$set" not in escaped
    assert unescape_keys(escaped) == payload


def test_escape_leaves_ordinary_keys_alone() -> None:
    payload = {"event": "user.created", "user": {"id": 1827}}
    assert escape_keys(payload) == payload


def test_parse_json_body() -> None:
    assert parse_body(b'{"event":"user.created"}', "application/json") == {"event": "user.created"}


def test_parse_json_with_charset_and_suffix() -> None:
    assert parse_body(b'{"a":1}', "application/json; charset=utf-8") == {"a": 1}
    assert parse_body(b'{"a":1}', "application/vnd.api+json") == {"a": 1}


def test_parse_form_body() -> None:
    body = parse_body(b"a=1&b=2&b=3", "application/x-www-form-urlencoded")
    assert body == {"a": "1", "b": ["2", "3"]}


def test_parse_text_body() -> None:
    assert parse_body(b"hello", "text/plain") == "hello"


def test_malformed_json_returns_none() -> None:
    assert parse_body(b"{not json", "application/json") is None


def test_binary_body_returns_none_and_encodes_raw() -> None:
    raw = b"\x89PNG\r\n\x1a\n\xff\xfe"
    assert parse_body(raw, "application/octet-stream") is None
    text, encoding = decode_raw(raw)
    assert encoding == "base64"
    assert text


def test_decode_raw_utf8() -> None:
    assert decode_raw(b"hello") == ("hello", "utf-8")


def test_event_type_from_header_wins() -> None:
    headers = {"x-github-event": "push"}
    assert extract_event_type(headers, {"event": "ignored"}) == "push"


def test_event_type_from_body() -> None:
    assert extract_event_type({}, {"type": "checkout.session.completed"}) == (
        "checkout.session.completed"
    )


def test_event_type_absent() -> None:
    assert extract_event_type({}, {"no": "type"}) is None
    assert extract_event_type({}, "plain text") is None
