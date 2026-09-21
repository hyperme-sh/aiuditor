"""Bounce-proof SqlPersistSession against a migrated Postgres.

Skipped without Docker. Prompt bodies and HMAC tickets stay off the row.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from aiuditor_service.domain.models import (
    Contract,
    ContractParty,
    ContractStatus,
)
from aiuditor_service.persist import SqlPersistSession, resolve_persist
from aiuditor_service.store import new_stores

pytestmark = pytest.mark.integration


def _alembic_ini() -> Path:
    return Path(__file__).resolve().parents[3] / "core/lib/controlplane-db/alembic.ini"


def _normalize_sync_url(url: str) -> str:
    u = url.strip()
    if u.startswith("postgresql+psycopg2://"):
        return "postgresql+psycopg://" + u.removeprefix("postgresql+psycopg2://")
    if u.startswith("postgresql://"):
        return "postgresql+psycopg://" + u.removeprefix("postgresql://")
    if u.startswith("postgres://"):
        return "postgresql+psycopg://" + u.removeprefix("postgres://")
    return u


def _skip_without_docker() -> None:
    if os.environ.get("SKIP_TESTCONTAINERS", "").lower() in ("1", "true", "yes"):
        pytest.skip("SKIP_TESTCONTAINERS is set")
    try:
        import docker

        docker.from_env().ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Docker not available for testcontainers: {exc}")


@pytest.fixture(scope="module")
def migrated_postgres() -> str:
    _skip_without_docker()
    pytest.importorskip("testcontainers")
    from alembic import command
    from alembic.config import Config
    from testcontainers.postgres import PostgresContainer

    ini = _alembic_ini()
    assert ini.is_file()
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _normalize_sync_url(pg.get_connection_url())
        os.environ["DATABASE_URL"] = url
        command.upgrade(Config(str(ini)), "head")
        yield url


def _plant_tenant(url: str, tenant_id: uuid.UUID) -> None:
    eng = create_engine(url)
    with eng.begin() as cx:
        cx.execute(
            text("INSERT INTO tenants (id, name, is_active) VALUES (:id, :name, true)"),
            {"id": tenant_id, "name": f"aiuditor-bounce-{tenant_id.hex[:8]}"},
        )


def _signed_contract(tenant: uuid.UUID) -> Contract:
    now = datetime.now(UTC)
    return Contract(
        id=uuid.uuid4(),
        tenant_id=tenant,
        parties=[
            ContractParty(role="renter", subject="renter@example.com"),
            ContractParty(role="host", subject="agx-host"),
        ],
        meters=["wall_time", "capacity_block"],
        rail="stripe",
        until=now + timedelta(hours=2),
        dispute_tip="cert plus lease audit",
        status=ContractStatus.SIGNED,
        signed_by="checkout",
        created_at=now,
        signed_at=now,
    )


def test_sql_persist_survives_new_store_session(migrated_postgres: str) -> None:
    tenant = uuid.uuid4()
    _plant_tenant(migrated_postgres, tenant)
    row = _signed_contract(tenant)

    writer = new_stores(SqlPersistSession.from_url(migrated_postgres))
    writer.contract_store.put(row)

    bounced = new_stores(SqlPersistSession.from_url(migrated_postgres))
    restored = bounced.contract_store.get(tenant, row.id)
    assert restored is not None
    assert restored.id == row.id
    assert restored.rail == "stripe"
    assert restored.status is ContractStatus.SIGNED
    assert [party.role for party in restored.parties] == ["renter", "host"]
    assert restored.dispute_tip == "cert plus lease audit"
    dumped = bounced.persist.snapshot()["contracts"][str(tenant)][str(row.id)]
    assert "prompt" not in dumped
    assert "ticket" not in dumped


def test_resolve_persist_pings_migrated_postgres(migrated_postgres: str) -> None:
    persist = resolve_persist(True, migrated_postgres)
    assert isinstance(persist, SqlPersistSession)
    persist.ping()
