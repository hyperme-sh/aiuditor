from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from panopticon_tests.fixtures.postgres import (
    async_db_url,
    normalize_sync_sqlalchemy_url,
    run_alembic_upgrade,
)


@pytest.fixture(scope="session")
def require_docker() -> None:
    if os.environ.get("SKIP_TESTCONTAINERS", "").lower() in ("1", "true", "yes"):
        pytest.skip("SKIP_TESTCONTAINERS is set")
    try:
        import docker

        docker.from_env().ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Docker not available for testcontainers: {exc}")


@pytest.fixture(scope="session")
def postgres_container(require_docker: None):
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def postgres_sync_url(postgres_container: PostgresContainer) -> str:
    return normalize_sync_sqlalchemy_url(postgres_container.get_connection_url())


@pytest.fixture(scope="session")
def migrated_postgres(postgres_sync_url: str) -> str:
    # Alembic must not disable application loggers in subsequent tests.
    from unittest.mock import patch

    with patch("logging.config.fileConfig"):
        run_alembic_upgrade(postgres_sync_url)
    from controlplane_db.migrations import stamp_baselines, upgrade

    stamp_baselines(postgres_sync_url)
    upgrade(postgres_sync_url)
    return postgres_sync_url


@pytest.fixture(scope="session")
def db_async_url(migrated_postgres: str) -> str:
    return async_db_url(migrated_postgres)


@pytest_asyncio.fixture
async def async_engine(db_async_url: str):
    engine = create_async_engine(db_async_url, echo=False)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def async_session(async_engine) -> AsyncSession:
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: needs Docker (testcontainers)")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def validate_published_wire_contracts(monkeypatch):
    """Validate real Postgres publisher/outbox envelopes before insertion in e2e tests."""
    from controlplane_events import store
    from controlplane_events.contracts import validate_event_envelope

    original = store.envelope_params

    def checked(envelope):
        validate_event_envelope(envelope)
        return original(envelope)

    monkeypatch.setattr(store, "envelope_params", checked)
