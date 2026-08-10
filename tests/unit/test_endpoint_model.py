import pytest
from pydantic import ValidationError

from app.models.endpoint import AuthType, EndpointCreate, EndpointUpdate, normalize_path


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("github", "github"),
        ("/github", "github"),
        ("/webhooks/github", "github"),
        ("webhooks/github", "github"),
        ("team/github/", "team/github"),
        ("  spaced  ", "spaced"),
    ],
)
def test_path_normalization(given: str, expected: str) -> None:
    assert normalize_path(given) == expected
    assert EndpointCreate(name="n", path=given).path == expected


@pytest.mark.parametrize("path", ["", "/", "has space", "bad!char", "../escape"])
def test_invalid_paths_rejected(path: str) -> None:
    with pytest.raises(ValidationError):
        EndpointCreate(name="n", path=path)


def test_methods_uppercased() -> None:
    endpoint = EndpointCreate(name="n", path="p", allowed_methods=["post", "get"])
    assert endpoint.allowed_methods == ["POST", "GET"]


def test_unsupported_method_rejected() -> None:
    with pytest.raises(ValidationError, match="unsupported methods"):
        EndpointCreate(name="n", path="p", allowed_methods=["TRACE"])


def test_empty_methods_rejected() -> None:
    with pytest.raises(ValidationError):
        EndpointCreate(name="n", path="p", allowed_methods=[])


def test_auth_without_secret_rejected() -> None:
    with pytest.raises(ValidationError, match="requires a secret"):
        EndpointCreate(name="n", path="p", authentication={"type": "hmac_sha256"})  # type: ignore[arg-type]


def test_default_auth_header_applied() -> None:
    endpoint = EndpointCreate(
        name="n", path="p", authentication={"type": "hmac_sha256"}, secret="s"
    )  # type: ignore[arg-type]
    assert endpoint.authentication.header == "x-hub-signature-256"


def test_no_auth_needs_no_secret() -> None:
    endpoint = EndpointCreate(name="n", path="p")
    assert endpoint.authentication.type is AuthType.NONE
    assert endpoint.secret is None


def test_update_only_carries_set_fields() -> None:
    changes = EndpointUpdate(enabled=False).to_changes()
    assert set(changes) == {"enabled", "updated_at"}


def test_update_can_clear_secret_explicitly() -> None:
    changes = EndpointUpdate(secret=None).to_changes()
    assert "secret" in changes and changes["secret"] is None
