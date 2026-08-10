import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from app.db import Doc


class AuthType(StrEnum):
    NONE = "none"
    STATIC_SECRET = "static_secret"
    HMAC_SHA256 = "hmac_sha256"


DEFAULT_AUTH_HEADER = {
    AuthType.STATIC_SECRET: "x-webhook-secret",
    AuthType.HMAC_SHA256: "x-hub-signature-256",
}

ALLOWED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
PATH_PATTERN = re.compile(r"^[A-Za-z0-9._~/-]+$")


def utcnow() -> datetime:
    # MongoDB keeps milliseconds, so truncate to match what will be read back
    now = datetime.now(UTC)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


def normalize_path(path: str) -> str:
    # Accepts both "github" and the "/webhooks/github" form used in the plan
    cleaned = path.strip().strip("/")
    if cleaned.startswith("webhooks/"):
        cleaned = cleaned.removeprefix("webhooks/")
    return cleaned


def _validate_path(value: str) -> str:
    cleaned = normalize_path(value)
    if not cleaned or not PATH_PATTERN.match(cleaned) or ".." in cleaned:
        raise ValueError("path must be a non-empty slug such as 'github' or 'team/github'")
    return cleaned


def _validate_methods(value: list[str]) -> list[str]:
    methods = [method.upper() for method in value]
    if not methods:
        raise ValueError("at least one HTTP method is required")
    if unknown := set(methods) - set(ALLOWED_METHODS):
        raise ValueError(f"unsupported methods: {', '.join(sorted(unknown))}")
    return methods


class AuthConfig(BaseModel):
    type: AuthType = AuthType.NONE
    header: str | None = None
    signature_prefix: str = "sha256="

    @model_validator(mode="after")
    def apply_default_header(self) -> Self:
        self.header = self.header.lower() if self.header else DEFAULT_AUTH_HEADER.get(self.type)
        return self


class EndpointCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    path: str
    enabled: bool = True
    authentication: AuthConfig = AuthConfig()
    secret: str | None = None
    allowed_methods: list[str] = ["POST"]
    max_payload_size: int | None = Field(default=None, gt=0)
    retention_days: int | None = Field(default=None, gt=0)

    _check_path = field_validator("path")(_validate_path)
    _check_methods = field_validator("allowed_methods")(_validate_methods)

    @model_validator(mode="after")
    def require_secret_when_authenticated(self) -> Self:
        if self.authentication.type is not AuthType.NONE and not self.secret:
            raise ValueError(f"{self.authentication.type} requires a secret")
        return self

    def to_document(self) -> Doc:
        now = utcnow()
        document = self.model_dump()
        document["authentication"]["type"] = self.authentication.type.value
        document["created_at"] = now
        document["updated_at"] = now
        return document


class EndpointUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    path: str | None = None
    enabled: bool | None = None
    authentication: AuthConfig | None = None
    secret: str | None = None
    allowed_methods: list[str] | None = None
    max_payload_size: int | None = Field(default=None, gt=0)
    retention_days: int | None = Field(default=None, gt=0)

    @field_validator("path")
    @classmethod
    def check_path(cls, value: str | None) -> str | None:
        return _validate_path(value) if value is not None else None

    @field_validator("allowed_methods")
    @classmethod
    def check_methods(cls, value: list[str] | None) -> list[str] | None:
        return _validate_methods(value) if value is not None else None

    def to_changes(self) -> Doc:
        changes = self.model_dump(exclude_unset=True)
        if "authentication" in changes and self.authentication is not None:
            changes["authentication"]["type"] = self.authentication.type.value
        changes["updated_at"] = utcnow()
        return changes


class EndpointOut(BaseModel):
    id: str
    name: str
    path: str
    url: str
    enabled: bool
    authentication: AuthConfig
    has_secret: bool
    allowed_methods: list[str]
    max_payload_size: int | None
    retention_days: int | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> EndpointOut:
        return cls(
            id=str(document["_id"]),
            name=document["name"],
            path=document["path"],
            url=f"/webhooks/{document['path']}",
            enabled=document["enabled"],
            authentication=AuthConfig(**document.get("authentication", {})),
            has_secret=bool(document.get("secret")),
            allowed_methods=document["allowed_methods"],
            max_payload_size=document.get("max_payload_size"),
            retention_days=document.get("retention_days"),
            created_at=document["created_at"],
            updated_at=document["updated_at"],
        )
