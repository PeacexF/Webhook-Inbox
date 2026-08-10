from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from webhook_inbox import log
from webhook_inbox.api import health
from webhook_inbox.config import Settings
from webhook_inbox.db import create_client, ensure_indexes
from webhook_inbox.ingest import receiver
from webhook_inbox.web import routes

logger = log.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    log.configure(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = create_client(settings.mongo_uri)
        app.state.settings = settings
        app.state.db = client[settings.mongo_database]
        await ensure_indexes(app.state.db)
        logger.info("app.started", database=settings.mongo_database)
        yield
        await client.close()

    app = FastAPI(title="Webhook Inbox", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(receiver.router)
    app.include_router(routes.router)
    return app


app = create_app()
