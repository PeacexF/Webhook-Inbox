import pytest

from app.ingest.signature import verify
from tests.helpers import sign

BODY = b'{"event":"user.created"}'
SECRET = "s3cret"


def endpoint(auth_type: str, **extra: object) -> dict[str, object]:
    header = {"static_secret": "x-webhook-secret", "hmac_sha256": "x-hub-signature-256"}.get(
        auth_type
    )
    return {
        "authentication": {"type": auth_type, "header": header, "signature_prefix": "sha256="},
        "secret": SECRET,
        **extra,
    }


def test_none_accepts_anything() -> None:
    assert verify(endpoint("none", secret=None), {}, BODY) is True


def test_static_secret_match() -> None:
    assert verify(endpoint("static_secret"), {"x-webhook-secret": SECRET}, BODY) is True


def test_static_secret_mismatch() -> None:
    assert verify(endpoint("static_secret"), {"x-webhook-secret": "wrong"}, BODY) is False


def test_static_secret_missing_header() -> None:
    assert verify(endpoint("static_secret"), {}, BODY) is False


def test_hmac_valid() -> None:
    headers = {"x-hub-signature-256": sign(SECRET, BODY)}
    assert verify(endpoint("hmac_sha256"), headers, BODY) is True


def test_hmac_accepts_signature_without_prefix() -> None:
    headers = {"x-hub-signature-256": sign(SECRET, BODY, prefix="")}
    assert verify(endpoint("hmac_sha256"), headers, BODY) is True


def test_hmac_is_case_insensitive_on_hex() -> None:
    headers = {"x-hub-signature-256": sign(SECRET, BODY).upper().replace("SHA256=", "sha256=")}
    assert verify(endpoint("hmac_sha256"), headers, BODY) is True


def test_hmac_rejects_tampered_body() -> None:
    headers = {"x-hub-signature-256": sign(SECRET, BODY)}
    assert verify(endpoint("hmac_sha256"), headers, b'{"event":"tampered"}') is False


def test_hmac_rejects_wrong_secret() -> None:
    headers = {"x-hub-signature-256": sign("other-secret", BODY)}
    assert verify(endpoint("hmac_sha256"), headers, BODY) is False


def test_hmac_rejects_garbage_signature() -> None:
    assert verify(endpoint("hmac_sha256"), {"x-hub-signature-256": "sha256=zzz"}, BODY) is False


@pytest.mark.parametrize("auth_type", ["static_secret", "hmac_sha256"])
def test_missing_secret_fails_closed(auth_type: str) -> None:
    config = endpoint(auth_type, secret=None)
    assert verify(config, {"x-webhook-secret": "x", "x-hub-signature-256": "y"}, BODY) is False


def test_unknown_auth_type_fails_closed() -> None:
    assert verify(endpoint("magic"), {"x-webhook-secret": SECRET}, BODY) is False


def test_custom_header_is_honoured() -> None:
    config = endpoint("static_secret")
    config["authentication"] = {"type": "static_secret", "header": "x-my-token"}  # type: ignore[index]
    assert verify(config, {"x-my-token": SECRET}, BODY) is True
    assert verify(config, {"x-webhook-secret": SECRET}, BODY) is False
