import logging
from typing import Any

import structlog

SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "proxy-authorization",
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "x-webhook-secret",
        "x-hub-signature",
        "x-hub-signature-256",
        "stripe-signature",
        "session",
    }
)

REDACTED = "[redacted]"


def is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in SENSITIVE_KEYS)


def redact(
    _logger: Any, _method: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    for key, value in event_dict.items():
        if is_sensitive(key):
            event_dict[key] = REDACTED
        elif isinstance(value, dict):
            event_dict[key] = {k: (REDACTED if is_sensitive(k) else v) for k, v in value.items()}
    return event_dict


def configure(level: str = "INFO") -> None:
    logging.basicConfig(format="%(message)s", level=getattr(logging, level.upper(), logging.INFO))
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            redact,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
