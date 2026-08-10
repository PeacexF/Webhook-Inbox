from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth import (
    DUMMY_HASH,
    SESSION_COOKIE,
    create_session,
    destroy_session,
    verify_password,
)
from app.deps import DatabaseDep
from app.log import get_logger
from app.models.user import UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = get_logger(__name__)


class LoginRequest(BaseModel):
    username: str
    password: str


def set_session_cookie(response: Response, token: str, secure: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


async def authenticate(db: DatabaseDep, username: str, password: str) -> dict[str, object] | None:
    user = await db.users.find_one({"username": username})
    if user is None:
        # hash anyway
        verify_password(password, DUMMY_HASH)
        return None
    if not verify_password(password, user["password_hash"]):
        return None
    return user


@router.post("/login")
async def login(payload: LoginRequest, request: Request, db: DatabaseDep) -> Response:
    user = await authenticate(db, payload.username, payload.password)
    if user is None:
        logger.warning("auth.failed", username=payload.username)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    token, csrf_token = await create_session(db, user["_id"])
    logger.info("auth.login", username=payload.username)
    # API clients need the CSRF token to mutate cuz the dashboard reads it from the page
    response = JSONResponse({"csrf_token": csrf_token})
    set_session_cookie(response, token, secure=request.url.scheme == "https")
    return response


@router.post("/logout")
async def logout(request: Request, db: DatabaseDep) -> Response:
    await destroy_session(db, request.cookies.get(SESSION_COOKIE))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/me")
async def me(request: Request) -> UserOut:
    return UserOut.from_document(request.state.user)
