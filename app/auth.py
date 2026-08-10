import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.db import Database, Doc
from app.log import get_logger

SESSION_COOKIE = "wi_session"
SESSION_TTL = timedelta(days=7)

_hasher = PasswordHasher()
logger = get_logger(__name__)

# Verified against when the username is unknown, so login timing does not reveal it
DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError, InvalidHashError:
        return False


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_session(db: Database, user_id: object) -> tuple[str, str]:
    # Only the hash is stored, so a database leak cannot be replayed as a session
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    await db.sessions.insert_one(
        {
            "token_hash": _digest(token),
            "user_id": user_id,
            "csrf_token": csrf_token,
            "created_at": now,
            "expires_at": now + SESSION_TTL,
        }
    )
    return token, csrf_token


async def resolve_session(db: Database, token: str | None) -> tuple[Doc, Doc] | None:
    if not token:
        return None
    session = await db.sessions.find_one({"token_hash": _digest(token)})
    if session is None:
        return None
    if session["expires_at"] <= datetime.now(UTC):
        await db.sessions.delete_one({"_id": session["_id"]})
        return None
    user = await db.users.find_one({"_id": session["user_id"]})
    if user is None:
        return None
    return session, user


async def destroy_session(db: Database, token: str | None) -> None:
    if token:
        await db.sessions.delete_one({"token_hash": _digest(token)})


async def destroy_user_sessions(db: Database, user_id: object) -> None:
    await db.sessions.delete_many({"user_id": user_id})


async def seed_admin(db: Database, username: str, password: str) -> None:
    if await db.users.count_documents({}, limit=1):
        return
    generated = not password
    if generated:
        password = secrets.token_urlsafe(12)
    await db.users.insert_one(
        {
            "username": username,
            "password_hash": hash_password(password),
            "created_at": datetime.now(UTC),
        }
    )
    logger.info("admin.created", username=username, generated_password=generated)
    if generated:
        print(
            f"\n  ADMIN_PASSWORD was not set. Generated a password for '{username}':\n"
            f"\n      {password}\n"
            f"\n  Change it in Settings, or set ADMIN_PASSWORD and start with an empty database.\n",
            flush=True,
        )
