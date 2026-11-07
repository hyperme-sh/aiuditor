from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.engine import make_url
from controlplane_db import migrations


def _database_url(value: str, driver: str) -> str:
    url = make_url(value)
    if url.get_backend_name() not in {"postgresql", "postgres"}:
        raise ValueError("Disposable PostgreSQL URL required")
    return url.set(drivername=driver).render_as_string(hide_password=False)


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
    PostgresContainer = pytest.importorskip("testcontainers.postgres").PostgresContainer
    with PostgresContainer("postgres:17-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def postgres_sync_url(request) -> str:
    external = os.environ.get("TEST_DATABASE_URL")
    if external:
        return _database_url(external, "postgresql+psycopg")
    container = request.getfixturevalue("postgres_container")
    return _database_url(container.get_connection_url(), "postgresql+psycopg")


@pytest.fixture(scope="session")
def migrated_postgres(postgres_sync_url: str) -> str:
    # This integration fixture only uses the installed standalone owner runner.
    # It never locates a sibling monorepo or implicitly stamps a legacy database.
    # Bootstrap foreign fixtures with the pinned shared package, but never ask
    # that older graph to interpret this service's newer owner revision.
    migrations.upgrade(postgres_sync_url, chains=tuple(name for name in migrations._ORDER if name != 'aiuditor'))
    if migrations.current_heads(postgres_sync_url)['aiuditor'] is None:
        migrations.upgrade(postgres_sync_url, chains=('aiuditor',))
    from aiuditor_service.schema_startup import upgrade_schema
    upgrade_schema(postgres_sync_url)
    return postgres_sync_url


@pytest.fixture(scope="session")
def db_async_url(migrated_postgres: str) -> str:
    return _database_url(migrated_postgres, "postgresql+asyncpg")


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
