from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from testcontainers.community.mongodb import MongoDbContainer

from app.config import Settings
from app.main import create_app

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "test-password"


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
            admin_username=ADMIN_USERNAME,
            admin_password=ADMIN_PASSWORD,
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
    # Anonymous: for webhook ingestion and for asserting routes are guarded
    async for c in make_client():
        yield c


async def login(client: httpx.AsyncClient, username: str, password: str) -> httpx.AsyncClient:
    response = await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


@pytest.fixture
async def authed_client(
    make_client: Callable[..., AsyncIterator[httpx.AsyncClient]],
) -> AsyncIterator[httpx.AsyncClient]:
    async for c in make_client():
        yield await login(c, ADMIN_USERNAME, ADMIN_PASSWORD)
