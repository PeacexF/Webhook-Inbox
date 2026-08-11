import hmac
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


# 'unsafe-inline' is still required: the theme script and a few onclick handlers are inline.
# It stops remote script and frame injection, which is the bulk of the value.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def is_https(request: Request) -> bool:
    # Forging the header can only add Secure, never remove it, so it is safe to honour
    return request.url.scheme == "https" or (
        request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if is_https(request):
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


def _unauthorized(request: Request) -> Response:
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


def _csrf_ok(request: Request) -> bool:
    expected = request.state.csrf_token
    provided = request.headers.get(CSRF_HEADER)
    if not expected or not provided:
        return False
    # Compared in constant time so the token cannot be recovered a byte at a time
    return hmac.compare_digest(provided, expected)


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
        if request.method not in SAFE_METHODS and not _csrf_ok(request):
            return JSONResponse({"detail": "Invalid CSRF token"}, status_code=403)

        return await call_next(request)
