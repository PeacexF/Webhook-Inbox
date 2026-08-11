from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.models.endpoint import ALLOWED_METHODS


class ReplayState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class ReplayCreate(BaseModel):
    destination: str
    method: str = "POST"
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("destination")
    @classmethod
    def _require_destination(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Destination URL is required")
        return value.strip()

    @field_validator("method")
    @classmethod
    def _known_method(cls, value: str) -> str:
        method = value.strip().upper()
        if method not in ALLOWED_METHODS:
            raise ValueError(f"Method must be one of {', '.join(ALLOWED_METHODS)}")
        return method


class ReplayResponse(BaseModel):
    status_code: int | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    body: str = ""
    truncated: bool = False


class ReplayOut(BaseModel):
    id: str
    event_id: str
    destination: str
    method: str
    state: ReplayState
    attempt: int
    max_attempts: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None
    response: ReplayResponse | None
    error: str | None

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> ReplayOut:
        response = document.get("response")
        return cls(
            id=str(document["_id"]),
            event_id=str(document["event_id"]),
            destination=document["destination"],
            method=document["method"],
            state=ReplayState(document["state"]),
            attempt=document.get("attempt", 0),
            max_attempts=document.get("max_attempts", 1),
            created_at=document["created_at"],
            started_at=document.get("started_at"),
            completed_at=document.get("completed_at"),
            duration_ms=document.get("duration_ms"),
            response=ReplayResponse(**response) if response else None,
            error=document.get("error"),
        )
