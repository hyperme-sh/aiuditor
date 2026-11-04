from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from asgi_lifespan import LifespanManager

from aiuditor_service.app import create_app
from aiuditor_service.domain.models import HydrateSnapshot
from aiuditor_service.package import (
    WITNESS_BIND_ALL,
    WITNESS_TENANT_INVALID,
    WITNESS_TENANT_REQUIRED,
    PackageUnavailable,
    resolve_bind_host,
    resolve_hydrate_every,
    resolve_hydrate_tenant,
    witness_uses_memory_persist,
)
from aiuditor_service.parent import FakeParentHop
from aiuditor_service.persist import MemoryPersistSession, resolve_persist
from aiuditor_service.role import WRITER_MARKET, AiuditorRole


def test_witness_bind_defaults_to_localhost():
    assert resolve_bind_host(AiuditorRole.WITNESS, "") == "127.0.0.1"
    assert resolve_bind_host(AiuditorRole.WITNESS, "127.0.0.1") == "127.0.0.1"
    assert resolve_bind_host(AiuditorRole.GLOBAL, "") == "0.0.0.0"


def test_witness_refuses_all_interfaces():
    with pytest.raises(PackageUnavailable, match=WITNESS_BIND_ALL):
        resolve_bind_host(AiuditorRole.WITNESS, "0.0.0.0")
    with pytest.raises(PackageUnavailable, match=WITNESS_BIND_ALL):
        resolve_bind_host(AiuditorRole.WITNESS, "10.0.0.8")


def test_hydrate_tenant_required_only_when_parent_is_set():
    assert (
        resolve_hydrate_tenant(AiuditorRole.WITNESS, parent_url="", tenant_id="") is None
    )
    assert (
        resolve_hydrate_tenant(AiuditorRole.GLOBAL, parent_url="https://parent", tenant_id="")
        is None
    )
    tenant = uuid.uuid4()
    assert (
        resolve_hydrate_tenant(
            AiuditorRole.WITNESS,
            parent_url="https://parent",
            tenant_id=str(tenant),
        )
        == tenant
    )
    with pytest.raises(PackageUnavailable, match=WITNESS_TENANT_REQUIRED):
        resolve_hydrate_tenant(
            AiuditorRole.WITNESS, parent_url="https://parent", tenant_id=""
        )
    with pytest.raises(PackageUnavailable, match=WITNESS_TENANT_INVALID):
        resolve_hydrate_tenant(
            AiuditorRole.WITNESS, parent_url="https://parent", tenant_id="not-a-uuid"
        )


def test_hydrate_every_floors_and_defaults():
    assert resolve_hydrate_every("") == 60.0
    assert resolve_hydrate_every("nope") == 60.0
    assert resolve_hydrate_every("1") == 5.0
    assert resolve_hydrate_every("90") == 90.0


def test_witness_memory_persist_without_database_url():
    assert witness_uses_memory_persist(AiuditorRole.WITNESS, "") is True
    assert witness_uses_memory_persist(AiuditorRole.WITNESS, "postgresql+asyncpg://x:x@localhost:1/x") is True
    assert (
        witness_uses_memory_persist(
            AiuditorRole.WITNESS, "postgresql://controlplane:x@postgres:5432/controlplane"
        )
        is False
    )
    assert witness_uses_memory_persist(AiuditorRole.GLOBAL, "") is False
    persist = resolve_persist(True, "", role=AiuditorRole.WITNESS)
    assert isinstance(persist, MemoryPersistSession)


def test_create_app_witness_without_parent_does_not_need_tenant(monkeypatch):
    monkeypatch.delenv("AIUDITOR_PARENT_URL", raising=False)
    monkeypatch.delenv("AIUDITOR_HYDRATE_TENANT_ID", raising=False)
    app = create_app(enable_db_middleware=False, role=AiuditorRole.WITNESS)
    assert app.state.aiuditor_role is AiuditorRole.WITNESS


def test_create_app_witness_parent_requires_tenant(monkeypatch):
    monkeypatch.setenv("AIUDITOR_PARENT_URL", "https://api.test.hyperme.sh")
    monkeypatch.delenv("AIUDITOR_HYDRATE_TENANT_ID", raising=False)
    with pytest.raises(PackageUnavailable, match=WITNESS_TENANT_REQUIRED):
        create_app(enable_db_middleware=False, role=AiuditorRole.WITNESS)


@pytest.mark.asyncio
async def test_witness_timer_pulls_parent(monkeypatch):
    tenant = uuid.uuid4()
    monkeypatch.setenv("AIUDITOR_PARENT_URL", "https://parent.test")
    monkeypatch.setenv("AIUDITOR_HYDRATE_TENANT_ID", str(tenant))
    monkeypatch.setenv("AIUDITOR_HYDRATE_EVERY", "5")
    snapshot = HydrateSnapshot(
        tenant_id=tenant,
        role="global",
        writer=WRITER_MARKET,
        exported_at=datetime.now(UTC),
        connections=[],
        policies=[],
        contracts=[],
        violations=[],
        approvals=[],
        catalog=[],
    )
    parent = FakeParentHop(snapshot=snapshot)
    app = create_app(
        enable_db_middleware=False,
        role=AiuditorRole.WITNESS,
        parent_hop=parent,
    )
    async with LifespanManager(app):
        for _ in range(50):
            if parent.calls:
                break
            await asyncio.sleep(0.02)
    assert parent.calls == [tenant]
