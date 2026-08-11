import time
from dataclasses import dataclass, field

import httpx

from app.config import ReplayConfig
from app.log import get_logger
from app.replay.ssrf import Destination, DestinationError, validate

logger = get_logger(__name__)

# Reusable credentials must never be forwarded to an arbitrary destination
# Signature headers (x-hub-signature-256, stripe-signature) are allowed
BLOCKED_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "host",
        "content-length",
        "connection",
        "transfer-encoding",
        "te",
        "upgrade",
        "expect",
    }
)
HOP_BY_HOP_HEADERS = frozenset(
    {"host", "content-length", "connection", "transfer-encoding", "te", "upgrade", "expect"}
)
FORWARD_EXACT = frozenset({"content-type", "accept", "user-agent"})
FORWARD_PREFIXES = ("x-", "stripe-")

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504, 507, 508, 509})


@dataclass
class Attempt:
    status_code: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""
    truncated: bool = False
    duration_ms: int = 0
    error: str | None = None
    retryable: bool = False

    @property
    def succeeded(self) -> bool:
        return self.error is None and self.status_code is not None and self.status_code < 400


def forwardable(headers: dict[str, str]) -> dict[str, str]:
    forwarded = {}
    for key, value in headers.items():
        lowered = key.lower()
        if lowered in BLOCKED_HEADERS:
            continue
        if lowered in FORWARD_EXACT or lowered.startswith(FORWARD_PREFIXES):
            forwarded[lowered] = value
    return forwarded


def merge_headers(original: dict[str, str], custom: dict[str, str]) -> dict[str, str]:
    # Headers the user typed are their own decision, so only hop-by-hop ones are dropped
    chosen = {
        key.lower(): value for key, value in custom.items() if key.lower() not in HOP_BY_HOP_HEADERS
    }
    return forwardable(original) | chosen


async def _request(
    client: httpx.AsyncClient,
    target: Destination,
    method: str,
    headers: dict[str, str],
    body: bytes,
    config: ReplayConfig,
) -> tuple[httpx.Response, bytes, bool]:
    # Host and SNI carry the real name; only the connection goes to the pinned IP
    sent = {**headers, "Host": target.authority}
    async with client.stream(
        method,
        target.pinned,
        headers=sent,
        content=body or None,
        extensions={"sni_hostname": target.url.host},
    ) as response:
        chunks: list[bytes] = []
        total = 0
        truncated = False
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > config.max_response_size:
                truncated = True
                break
            chunks.append(chunk)
    return response, b"".join(chunks), truncated


def _result(response: httpx.Response, body: bytes, truncated: bool) -> Attempt:
    return Attempt(
        status_code=response.status_code,
        headers=dict(response.headers),
        body=body.decode("utf-8", "replace"),
        truncated=truncated,
        retryable=response.status_code in RETRYABLE_STATUS,
    )


async def _send(
    url: str, method: str, headers: dict[str, str], body: bytes, config: ReplayConfig
) -> Attempt:
    async with httpx.AsyncClient(timeout=config.timeout, follow_redirects=False) as client:
        for _ in range(config.max_redirects + 1):
            # Revalidated from scratch on every hop, so a redirect cannot reach inside
            target = validate(url, config)
            response, body_bytes, truncated = await _request(
                client, target, method, headers, body, config
            )

            location = response.headers.get("location")
            redirecting = 300 <= response.status_code < 400 and location
            if not redirecting or not config.allow_redirects:
                return _result(response, body_bytes, truncated)
            url = str(target.url.join(location))

    raise DestinationError("Too many redirects")


async def execute(
    url: str, method: str, headers: dict[str, str], body: bytes, config: ReplayConfig
) -> Attempt:
    started = time.perf_counter()

    def elapsed() -> int:
        return int((time.perf_counter() - started) * 1000)

    try:
        attempt = await _send(url, method, headers, body, config)
    except DestinationError as exc:
        # A rejected destination is a permanent failure; retrying cannot change it
        return Attempt(error=str(exc), retryable=False, duration_ms=elapsed())
    except httpx.TimeoutException:
        return Attempt(
            error=f"Timed out after {config.timeout}s", retryable=True, duration_ms=elapsed()
        )
    except httpx.TransportError as exc:
        return Attempt(error=f"Connection failed: {exc}", retryable=True, duration_ms=elapsed())

    attempt.duration_ms = elapsed()
    return attempt
