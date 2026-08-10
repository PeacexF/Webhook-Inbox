from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from testcontainers.community.mongodb import MongoDbContainer

from webhook_inbox.config import Settings
from webhook_inbox.main import create_app


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Keep a developer's local config.yaml out of the test run
    monkeypatch.setenv("CONFIG_FILE", str(tmp_path / "absent.yaml"))


@pytest.fixture(scope="session")
def mongo_uri() -> Iterator[str]:
    with MongoDbContainer("mongo:8") as mongo:
        yield mongo.get_connection_url()


@pytest.fixture
def make_client(
    mongo_uri: str,
) -> Callable[..., AsyncIterator[httpx.AsyncClient]]:
    def factory(**overrides: Any) -> AsyncIterator[httpx.AsyncClient]:
        settings = Settings(
            mongo_uri=mongo_uri,
            mongo_database=f"test_{uuid4().hex}",
            **overrides,
        )

        async def run() -> AsyncIterator[httpx.AsyncClient]:
            app = create_app(settings)
            async with app.router.lifespan_context(app):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    yield client

        return run()

    return factory


@pytest.fixture
async def client(
    make_client: Callable[..., AsyncIterator[httpx.AsyncClient]],
) -> AsyncIterator[httpx.AsyncClient]:
    async for c in make_client():
        yield c
