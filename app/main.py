from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import log
from app.api import auth as auth_api
from app.api import endpoints, health
from app.auth import seed_admin
from app.config import Settings
from app.db import create_client, ensure_indexes, seed_demo_endpoint
from app.ingest import receiver
from app.middleware import AuthMiddleware
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
        logger.info("app.started", database=settings.mongo_database)
        yield
        await client.close()

    app = FastAPI(title="Webhook Inbox", lifespan=lifespan)
    app.add_middleware(AuthMiddleware)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(health.router)
    app.include_router(auth_api.router)
    app.include_router(endpoints.router)
    app.include_router(receiver.router)
    app.include_router(routes.router)
    return app


app = create_app()
