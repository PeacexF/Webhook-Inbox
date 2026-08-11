import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import log
from app.api import auth as auth_api
from app.api import endpoints, health, replays
from app.auth import seed_admin
from app.config import Settings
from app.db import create_client, ensure_indexes, seed_demo_endpoint
from app.ingest import receiver
from app.middleware import AuthMiddleware, SecurityHeadersMiddleware
from app.ratelimit import RateLimiter
from app.replay.worker import run_forever
from app.web import routes

logger = log.get_logger(__name__)
STATIC_DIR = Path(__file__).parent / "web" / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    log.configure(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        client = create_client(settings.mongo_uri)
        app.state.settings = settings
        app.state.db = client[settings.mongo_database]
        await ensure_indexes(app.state.db)
        await seed_demo_endpoint(app.state.db)
        await seed_admin(app.state.db, settings.admin_username, settings.admin_password)

        stop = asyncio.Event()
        worker: asyncio.Task[None] | None = None
        if settings.replay.enabled and settings.replay.worker_enabled:
            worker = asyncio.create_task(run_forever(app.state.db, settings, stop))

        logger.info("app.started", database=settings.mongo_database)
        yield

        stop.set()
        if worker is not None:
            await worker
        await client.close()

    app = FastAPI(title="Webhook Inbox", lifespan=lifespan)
    app.state.rate_limiter = RateLimiter(
        settings.rate_limit.requests_per_minute, settings.rate_limit.enabled
    )
    app.state.login_limiter = RateLimiter(
        settings.rate_limit.login_per_minute, settings.rate_limit.enabled
    )
    app.add_middleware(AuthMiddleware)
    # Added last, so it runs first and stamps every response including error paths
    app.add_middleware(SecurityHeadersMiddleware)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(health.router)
    app.include_router(auth_api.router)
    app.include_router(endpoints.router)
    app.include_router(replays.router)
    app.include_router(receiver.router)
    app.include_router(routes.router)
    return app


app = create_app()
