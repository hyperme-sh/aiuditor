from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.engine import make_url
from sqlalchemy import create_engine
from sqlalchemy.schema import CreateTable
from alembic import command
from controlplane_db.models import Tenant
from aiuditor_service import schema_startup


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


def provision_owner(url, revision='head'):
    """Fresh local owner + tenant-reference fixture, without User/payment schemas."""
    engine = create_engine(url)
    try:
        with engine.begin() as db:
            db.execute(CreateTable(Tenant.__table__, include_foreign_key_constraints=[]))
            command.upgrade(schema_startup.owner_config(db), revision)
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def migrated_postgres(postgres_sync_url: str):
    # Imported fixture definitions can be registered by more than one module.
    # Give each instance a genuinely fresh database rather than repairing or
    # adopting an earlier module's schema.
    name = 'aiuditor_suite_' + uuid.uuid4().hex
    admin = create_engine(postgres_sync_url, isolation_level='AUTOCOMMIT')
    with admin.connect() as db:
        db.exec_driver_sql(f'CREATE DATABASE "{name}"')
    url = make_url(postgres_sync_url).set(database=name).render_as_string(hide_password=False)
    try:
        provision_owner(url)
        schema_startup.upgrade_schema(url)
        yield url
    finally:
        with admin.connect() as db:
            db.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.dispose()


@pytest.fixture
def legacy_directory(migrated_postgres):
    """Only legacy-compatibility cases install local directory fixtures.

    Canonical owner provisioning never installs these tables. Current User
    authority is independently qualified by its owner; no shared TEST is used.
    """
    from controlplane_db.models import Role, User, Group, UserGroupMembership
    tables = [model.__table__ for model in (Role, User, Group, UserGroupMembership)]
    engine = create_engine(migrated_postgres)
    try:
        with engine.begin() as db:
            for table in tables:
                db.execute(CreateTable(table, include_foreign_key_constraints=[]))
        yield
    finally:
        with engine.begin() as db:
            for table in reversed(tables):
                table.drop(db, checkfirst=True)
        engine.dispose()


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
