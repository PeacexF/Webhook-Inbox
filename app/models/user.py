from datetime import datetime

from pydantic import BaseModel, Field

USERNAME_PATTERN = r"^[A-Za-z0-9._-]{3,32}$"
MIN_PASSWORD_LENGTH = 8


class UserCreate(BaseModel):
    username: str = Field(pattern=USERNAME_PATTERN)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=256)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=256)


class UserOut(BaseModel):
    id: str
    username: str
    created_at: datetime

    @classmethod
    def from_document(cls, document: dict[str, object]) -> UserOut:
        return cls(
            id=str(document["_id"]),
            username=str(document["username"]),
            created_at=document["created_at"],  # type: ignore[arg-type]
        )
