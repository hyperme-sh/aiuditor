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
    CrawlCandidate,
    CrawlCandidateStatus,
)
from aiuditor_service.persist import SqlPersistSession, resolve_persist
from aiuditor_service.store import CandidateStore, new_stores

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


_OVERSIZE = 11_264_441_216


def _candidate(tenant: uuid.UUID, *, file_name: str, size_bytes: int = _OVERSIZE) -> CrawlCandidate:
    now = datetime.now(UTC)
    return CrawlCandidate(
        id=uuid.uuid4(),
        tenant_id=tenant,
        repo="bartowski/Llama-3.1-8B-Instruct-GGUF",
        revision="main",
        file_name=file_name,
        size_bytes=size_bytes,
        license="llama3",
        quant="Q4_K_M",
        file_hash="sha256:" + ("ab" * 32),
        publisher="bartowski",
        catalog_id="hf-llama-8b",
        class_id="agx-large",
        status=CrawlCandidateStatus.FIT,
        soak_job_id=None,
        created_at=now,
        updated_at=now,
    )


def test_sql_persist_stores_size_bytes_above_integer_max(migrated_postgres: str) -> None:
    tenant = uuid.uuid4()
    _plant_tenant(migrated_postgres, tenant)
    row = _candidate(tenant, file_name="Llama-3.1-8B-Instruct-Q4_K_M.gguf")
    writer = CandidateStore(persist=SqlPersistSession.from_url(migrated_postgres))
    writer.put(row)

    bounced = CandidateStore(persist=SqlPersistSession.from_url(migrated_postgres))
    restored = bounced.get(tenant, row.id)
    assert restored is not None
    assert restored.size_bytes == _OVERSIZE


def test_failed_candidate_savepoint_does_not_drop_the_next_write(
    migrated_postgres: str,
) -> None:
    tenant = uuid.uuid4()
    _plant_tenant(migrated_postgres, tenant)
    persist = SqlPersistSession.from_url(migrated_postgres)
    missing_tenant = uuid.uuid4()
    bad = _candidate(missing_tenant, file_name="bad.gguf")
    good = _candidate(tenant, file_name="ok.gguf")

    with persist.candidate_scope():
        with pytest.raises(Exception):
            persist.upsert("candidates", missing_tenant, bad.id, bad.model_dump())
        persist.upsert("candidates", tenant, good.id, good.model_dump())

    restored = CandidateStore(persist=SqlPersistSession.from_url(migrated_postgres)).get(
        tenant, good.id
    )
    assert restored is not None
    assert restored.size_bytes == _OVERSIZE
    assert restored.file_name == "ok.gguf"


def test_core_suite_bootstrap_serializes_replicas(migrated_postgres: str) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from aiuditor_service.program_bootstrap import bootstrap_core_suite
    from aiuditor_service.store import SuiteStore

    tenant = uuid.uuid4()
    _plant_tenant(migrated_postgres, tenant)
    barrier = Barrier(2)

    def initialize():
        store = SuiteStore(persist=SqlPersistSession.from_url(migrated_postgres))
        barrier.wait(timeout=10)
        return bootstrap_core_suite(store, tenant)

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: initialize(), range(2)))
    assert sorted(results) == [0, 2]
    reloaded = SuiteStore(persist=SqlPersistSession.from_url(migrated_postgres))
    assert len(reloaded.list_for(tenant)) == 2
    assert bootstrap_core_suite(reloaded, tenant) == 0


def test_core_suite_bootstrap_waits_for_real_tenant(migrated_postgres: str) -> None:
    from sqlalchemy.exc import IntegrityError
    from aiuditor_service.program_bootstrap import bootstrap_core_suite
    from aiuditor_service.store import SuiteStore

    tenant = uuid.uuid4()
    store = SuiteStore(persist=SqlPersistSession.from_url(migrated_postgres))
    with pytest.raises(IntegrityError):
        bootstrap_core_suite(store, tenant)
    assert store.list_for(tenant) == []
    _plant_tenant(migrated_postgres, tenant)
    assert bootstrap_core_suite(store, tenant) == 2
