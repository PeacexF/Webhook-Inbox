import hashlib
import hmac
from collections.abc import Mapping

from app.db import Doc
from app.models.endpoint import DEFAULT_AUTH_HEADER, AuthType


def verify(endpoint: Doc, headers: Mapping[str, str], raw_body: bytes) -> bool:
    # Fails closed: anything unrecognised or misconfigured is a rejection
    auth = endpoint.get("authentication") or {}
    try:
        auth_type = AuthType(auth.get("type", AuthType.NONE))
    except ValueError:
        return False

    if auth_type is AuthType.NONE:
        return True

    secret = endpoint.get("secret")
    if not secret:
        return False

    header_name = auth.get("header") or DEFAULT_AUTH_HEADER[auth_type]
    provided = headers.get(header_name)
    if not provided:
        return False

    if auth_type is AuthType.STATIC_SECRET:
        return hmac.compare_digest(provided, secret)

    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    prefix = auth.get("signature_prefix") or ""
    candidate = provided.removeprefix(prefix) if prefix else provided
    return hmac.compare_digest(candidate.strip().lower(), expected)
