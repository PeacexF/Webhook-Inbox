from collections.abc import Awaitable, Callable

from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.auth import SESSION_COOKIE, resolve_session

# Everything not listed here requires a session, so new routes are protected by default
PUBLIC_PREFIXES = ("/webhooks/", "/static/")
PUBLIC_PATHS = frozenset({"/health", "/ready", "/login", "/api/auth/login"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSRF_HEADER = "x-csrf-token"


def _unauthorized(request: Request) -> Response:
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        request.state.user = None
        request.state.csrf_token = None

        # Webhook ingestion and static files never touch the session collection
        if path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)

        resolved = await resolve_session(request.app.state.db, request.cookies.get(SESSION_COOKIE))
        if resolved is not None:
            session, user = resolved
            request.state.user = user
            request.state.csrf_token = session.get("csrf_token")

        if path in PUBLIC_PATHS:
            return await call_next(request)

        if request.state.user is None:
            return _unauthorized(request)

        # CSRF travels as a header only, forms post through HTMX
        if request.method not in SAFE_METHODS and (
            request.headers.get(CSRF_HEADER) != request.state.csrf_token
        ):
            return JSONResponse({"detail": "Invalid CSRF token"}, status_code=403)

        return await call_next(request)
