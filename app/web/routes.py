from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Form, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from app.api.auth import authenticate, set_session_cookie
from app.auth import (
    SESSION_COOKIE,
    create_session,
    destroy_session,
    destroy_user_sessions,
    hash_password,
    verify_password,
)
from app.db import Database, Doc
from app.deps import DatabaseDep, SettingsDep
from app.ingest.parser import unescape_keys
from app.log import get_logger
from app.models.endpoint import (
    ALLOWED_METHODS,
    AuthConfig,
    EndpointCreate,
    EndpointOut,
    EndpointUpdate,
    utcnow,
)
from app.models.replay import ReplayCreate, ReplayOut
from app.models.user import UserCreate, UserOut
from app.replay import store
from app.replay.ssrf import DestinationError, validate
from app.search.query import Cursor, Filters, find_events
from app.web.json_view import render_json

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
logger = get_logger(__name__)

PAGE_SIZE = 50
EVENT_TABS = ("overview", "headers", "query", "body", "raw", "replays")


def render(request: Request, template: str, context: dict[str, Any]) -> Response:
    base = {
        "user": request.state.user,
        "csrf_token": request.state.csrf_token,
        "active": context.pop("active", None),
    }
    return templates.TemplateResponse(request, template, base | context)


def _refresh(url: str) -> Response:
    # HTMX swaps the whole body, so send it somewhere rather than returning a fragment
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"HX-Redirect": url})


def _object_id(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except InvalidId, TypeError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found") from None


# --- auth ---------------------------------------------------------------


@router.get("/login")
async def login_page(request: Request) -> Response:
    if request.state.user is not None:
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html", {})


@router.post("/login")
async def login_submit(
    request: Request,
    db: DatabaseDep,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
) -> Response:
    user = await authenticate(db, username, password)
    if user is None:
        logger.warning("auth.failed", username=username)
        return render(request, "login.html", {"error": "Invalid username or password"})

    token, _ = await create_session(db, user["_id"])
    logger.info("auth.login", username=username)
    response = RedirectResponse("/", status_code=303)
    set_session_cookie(response, token, secure=request.url.scheme == "https")
    return response


@router.post("/logout")
async def logout_submit(request: Request, db: DatabaseDep) -> Response:
    await destroy_session(db, request.cookies.get(SESSION_COOKIE))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


# --- dashboard ----------------------------------------------------------


@router.get("/")
async def dashboard(request: Request, db: DatabaseDep) -> Response:
    since = datetime.now(UTC) - timedelta(hours=24)
    stats = {
        "total": await db.events.count_documents({}),
        "last_24h": await db.events.count_documents({"received_at": {"$gte": since}}),
        "failed": await db.events.count_documents({"processing.status": "failed"}),
        "endpoints": await db.endpoints.count_documents({}),
    }
    recent = [doc async for doc in db.events.find().sort("received_at", -1).limit(10)]
    return render(
        request, "dashboard.html", {"active": "dashboard", "stats": stats, "events": recent}
    )


# --- events -------------------------------------------------------------


@router.get("/events")
async def events_page(
    request: Request,
    db: DatabaseDep,
    q: str = "",
    endpoint: str = "",
    event_status: Annotated[str, Query(alias="status")] = "",
    method: str = "",
    event_type: Annotated[str, Query(alias="type")] = "",
    date_from: Annotated[str, Query(alias="from")] = "",
    date_to: Annotated[str, Query(alias="to")] = "",
    cursor: str | None = None,
) -> Response:
    filters = Filters(
        endpoint=endpoint or None,
        status=event_status or None,
        method=method or None,
        event_type=event_type or None,
        date_from=date_from or None,
        date_to=date_to or None,
    )
    rows, scored = await find_events(db, q, filters, PAGE_SIZE, Cursor.decode(cursor))

    params = {
        key: value
        for key, value in (
            ("q", q),
            ("endpoint", endpoint),
            ("status", event_status),
            ("method", method),
            ("type", event_type),
            ("from", date_from),
            ("to", date_to),
        )
        if value
    }
    next_url = None
    if len(rows) == PAGE_SIZE:
        # Keyset pagination: cheaper than skip once the collection grows
        forward = {**params, "cursor": Cursor.after(rows[-1], scored).encode()}
        next_url = f"/events?{urlencode(forward)}"

    return render(
        request,
        "events.html",
        {
            "active": "events",
            "events": rows,
            "scored": scored,
            # A total for a ranked search would mean a second pass over every match
            "total": None if scored else await db.events.count_documents(filters.to_query()),
            "next_url": next_url,
            "params": params,
            "endpoints": await _endpoint_list(db),
            "event_types": sorted(t for t in await db.events.distinct("event_type") if t),
            "statuses": sorted(await db.events.distinct("processing.status")),
            "methods": sorted(ALLOWED_METHODS),
        },
    )


async def _event_or_404(db: Database, event_id: str) -> Doc:
    event = await db.events.find_one({"_id": _object_id(event_id)})
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    return event


async def _event_context(db: Database, event: Doc, tab: str, settings: Any) -> dict[str, Any]:
    body = unescape_keys(event["request"].get("body"))
    return {
        "active": "events",
        "event": event,
        "tab": tab if tab in EVENT_TABS else "overview",
        "body": body,
        "body_html": render_json(body) if body is not None else "",
        "replays": [ReplayOut.from_document(doc) for doc in await store.history(db, event["_id"])],
        "replay_enabled": settings.replay.enabled,
    }


@router.get("/events/{event_id}")
async def event_detail(
    request: Request, db: DatabaseDep, settings: SettingsDep, event_id: str, tab: str = "overview"
) -> Response:
    event = await _event_or_404(db, event_id)
    return render(request, "event_detail.html", await _event_context(db, event, tab, settings))


@router.post("/events/{event_id}/replay")
async def replay_submit(
    request: Request,
    db: DatabaseDep,
    settings: SettingsDep,
    event_id: str,
    destination: Annotated[str, Form()],
    method: Annotated[str, Form()] = "POST",
) -> Response:
    event = await _event_or_404(db, event_id)
    if not settings.replay.enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Replay is disabled")

    try:
        payload = ReplayCreate(destination=destination, method=method)
        # Rejected here for immediate feedback; the worker validates again before connecting
        validate(payload.destination, settings.replay)
    except ValidationError as exc:
        error = exc.errors()[0]["msg"]
    except DestinationError as exc:
        error = str(exc)
    else:
        await store.enqueue(db, event["_id"], payload, settings.replay)
        logger.info("replay.queued", event_id=event_id, destination=payload.destination)
        return _refresh(f"/events/{event_id}?tab=replays")

    logger.warning("replay.rejected", event_id=event_id, reason=error)
    context = await _event_context(db, event, "replays", settings)
    return render(request, "event_detail.html", context | {"error": error})


# --- endpoints ----------------------------------------------------------


def _parse_endpoint_form(
    name: str,
    path: str,
    auth_type: str,
    secret: str,
    allowed_methods: str,
    max_payload_size: str = "",
    enabled: bool = True,
) -> dict[str, Any]:
    return {
        "name": name.strip(),
        "path": path.strip(),
        "enabled": enabled,
        "authentication": AuthConfig(type=auth_type),  # type: ignore[arg-type]
        "secret": secret or None,
        "allowed_methods": [m.strip() for m in allowed_methods.split(",") if m.strip()],
        "max_payload_size": int(max_payload_size) if max_payload_size.strip().isdigit() else None,
    }


async def _endpoint_list(db: Database) -> list[EndpointOut]:
    return [EndpointOut.from_document(doc) async for doc in db.endpoints.find().sort("name", 1)]


@router.get("/endpoints")
async def endpoints_page(request: Request, db: DatabaseDep) -> Response:
    return render(
        request, "endpoints.html", {"active": "endpoints", "endpoints": await _endpoint_list(db)}
    )


@router.post("/endpoints")
async def endpoint_create(
    request: Request,
    db: DatabaseDep,
    name: Annotated[str, Form()],
    path: Annotated[str, Form()],
    auth_type: Annotated[str, Form()] = "none",
    secret: Annotated[str, Form()] = "",
    allowed_methods: Annotated[str, Form()] = "POST",
) -> Response:
    try:
        payload = EndpointCreate(
            **_parse_endpoint_form(name, path, auth_type, secret, allowed_methods)
        )
    except ValidationError as exc:
        return render(
            request,
            "endpoints.html",
            {
                "active": "endpoints",
                "endpoints": await _endpoint_list(db),
                "error": exc.errors()[0]["msg"],
            },
        )

    try:
        await db.endpoints.insert_one(payload.to_document())
    except DuplicateKeyError:
        return render(
            request,
            "endpoints.html",
            {
                "active": "endpoints",
                "endpoints": await _endpoint_list(db),
                "error": f"Path '{payload.path}' is already in use",
            },
        )
    logger.info("endpoint.created", path=payload.path, name=payload.name)
    return _refresh("/endpoints")


@router.get("/endpoints/{endpoint_id}")
async def endpoint_detail(request: Request, db: DatabaseDep, endpoint_id: str) -> Response:
    document = await db.endpoints.find_one({"_id": _object_id(endpoint_id)})
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Endpoint not found")
    return render(
        request,
        "endpoint_detail.html",
        {"active": "endpoints", "endpoint": EndpointOut.from_document(document)},
    )


@router.post("/endpoints/{endpoint_id}")
async def endpoint_save(
    request: Request,
    db: DatabaseDep,
    endpoint_id: str,
    name: Annotated[str, Form()],
    path: Annotated[str, Form()],
    auth_type: Annotated[str, Form()] = "none",
    secret: Annotated[str, Form()] = "",
    allowed_methods: Annotated[str, Form()] = "POST",
    max_payload_size: Annotated[str, Form()] = "",
    enabled: Annotated[bool, Form()] = False,
) -> Response:
    object_id = _object_id(endpoint_id)
    document = await db.endpoints.find_one({"_id": object_id})
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Endpoint not found")

    fields = _parse_endpoint_form(
        name, path, auth_type, secret, allowed_methods, max_payload_size, enabled
    )
    # A blank secret field means "keep the stored one", not "clear it"
    if not fields["secret"]:
        fields.pop("secret")

    def fail(message: str) -> Response:
        return render(
            request,
            "endpoint_detail.html",
            {
                "active": "endpoints",
                "endpoint": EndpointOut.from_document(document),
                "error": message,
            },
        )

    try:
        changes = EndpointUpdate(**fields).to_changes()
    except ValidationError as exc:
        return fail(exc.errors()[0]["msg"])

    merged = {**document, **changes}
    if merged["authentication"]["type"] != "none" and not merged.get("secret"):
        return fail("This authentication type requires a secret")

    try:
        await db.endpoints.update_one({"_id": object_id}, {"$set": changes})
    except DuplicateKeyError:
        return fail("That path is already in use")
    logger.info("endpoint.updated", id=endpoint_id, fields=sorted(changes))
    return _refresh(f"/endpoints/{endpoint_id}")


@router.delete("/endpoints/{endpoint_id}")
async def endpoint_delete(db: DatabaseDep, endpoint_id: str) -> Response:
    await db.endpoints.delete_one({"_id": _object_id(endpoint_id)})
    logger.info("endpoint.deleted", id=endpoint_id)
    return _refresh("/endpoints")


# --- settings -----------------------------------------------------------


async def _settings_context(db: Database, settings: Any) -> dict[str, Any]:
    users = [UserOut.from_document(doc) async for doc in db.users.find().sort("username", 1)]
    return {"active": "settings", "users": users, "settings": settings}


@router.get("/settings")
async def settings_page(request: Request, db: DatabaseDep, settings: SettingsDep) -> Response:
    return render(request, "settings.html", await _settings_context(db, settings))


@router.post("/settings/password")
async def change_password(
    request: Request,
    db: DatabaseDep,
    settings: SettingsDep,
    current_password: Annotated[str, Form()],
    new_password: Annotated[str, Form()],
) -> Response:
    user = request.state.user
    context = await _settings_context(db, settings)

    if not verify_password(current_password, user["password_hash"]):
        return render(request, "settings.html", context | {"error": "Current password is wrong"})
    if len(new_password) < 8:
        return render(
            request, "settings.html", context | {"error": "New password is too short (min 8)"}
        )

    await db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {"password_hash": hash_password(new_password), "updated_at": utcnow()}},
    )
    # Every existing session dies, including this one, so the user signs in again
    await destroy_user_sessions(db, user["_id"])
    logger.info("user.password_changed", username=user["username"])
    return _refresh("/login")


@router.post("/settings/users")
async def create_user(
    request: Request,
    db: DatabaseDep,
    settings: SettingsDep,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
) -> Response:
    context = await _settings_context(db, settings)
    try:
        payload = UserCreate(username=username.strip(), password=password)
    except ValidationError as exc:
        return render(request, "settings.html", context | {"error": exc.errors()[0]["msg"]})

    try:
        await db.users.insert_one(
            {
                "username": payload.username,
                "password_hash": hash_password(payload.password),
                "created_at": utcnow(),
            }
        )
    except DuplicateKeyError:
        return render(
            request, "settings.html", context | {"error": "That username is already taken"}
        )
    logger.info("user.created", username=payload.username)
    return _refresh("/settings")


@router.delete("/settings/users/{user_id}")
async def delete_user(request: Request, db: DatabaseDep, user_id: str) -> Response:
    object_id = _object_id(user_id)
    if object_id == request.state.user["_id"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot delete your own account")
    if await db.users.count_documents({}) <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot delete the last user")

    await db.users.delete_one({"_id": object_id})
    await destroy_user_sessions(db, object_id)
    logger.info("user.deleted", id=user_id)
    return _refresh("/settings")
