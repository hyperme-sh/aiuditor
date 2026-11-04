"""Deployment provisioning fills program configuration, never host approvals."""
import asyncio
from types import SimpleNamespace
import uuid

import pytest

from aiuditor_service.domain.models import SuiteMemberCreate
from aiuditor_service.persist import MemoryPersistSession
from aiuditor_service.program import put_suite_member
from aiuditor_service.program_bootstrap import bootstrap_core_suite, run_program_bootstrap
from aiuditor_service.role import AiuditorRole
from aiuditor_service.store import SuiteStore
from controlplane_tenancy.marketplace import MARKETPLACE_TENANT_ID


def test_defaults_survive_reload_without_overwriting_operator_program():
    persist = MemoryPersistSession()
    store = SuiteStore(persist=persist)
    tenant = uuid.uuid4()
    custom = put_suite_member(store, tenant, SuiteMemberCreate(
        class_id="agx-large", catalog_id="operator-model", pack_id="thin-v1",
    ))
    assert bootstrap_core_suite(store, tenant) == 1
    assert store.list_for(tenant, class_id="agx-large") == [custom]
    ids = {row.id for row in store.list_for(tenant)}
    reloaded = SuiteStore(persist=persist)
    assert bootstrap_core_suite(reloaded, tenant) == 0
    assert {row.id for row in reloaded.list_for(tenant)} == ids
    assert len(reloaded.list_for(tenant, class_id="nx-volume")) == 1
    assert reloaded.list_for(uuid.uuid4()) == []
    assert all(not rows for table, rows in persist.snapshot().items()
               if table != "suite_members")


@pytest.mark.parametrize("role,enabled,expected", [
    (AiuditorRole.GLOBAL, "true", 2),
    (AiuditorRole.GLOBAL, "false", 0),
    (AiuditorRole.OPERATOR, "true", 0),
    (AiuditorRole.WITNESS, "true", 0),
])
async def test_bootstrap_is_explicit_and_marketplace_writer_only(monkeypatch, role, enabled, expected):
    monkeypatch.setenv("AIUDITOR_BOOTSTRAP_CORE_SUITE", enabled)
    store = SuiteStore()
    app = SimpleNamespace(state=SimpleNamespace(aiuditor_role=role, suite_store=store))
    await run_program_bootstrap(app, stop=asyncio.Event())
    assert len(store.list_for(MARKETPLACE_TENANT_ID)) == expected


async def test_bootstrap_retries_tenant_creation_race(monkeypatch):
    monkeypatch.setenv("AIUDITOR_BOOTSTRAP_CORE_SUITE", "true")
    store = SuiteStore()
    app = SimpleNamespace(state=SimpleNamespace(aiuditor_role=AiuditorRole.GLOBAL, suite_store=store))
    attempts = []
    real = bootstrap_core_suite

    def initially_unavailable(store, tenant):
        attempts.append(tenant)
        if len(attempts) == 1:
            raise RuntimeError("tenant not created yet")
        return real(store, tenant)

    monkeypatch.setattr("aiuditor_service.program_bootstrap.bootstrap_core_suite", initially_unavailable)
    await asyncio.wait_for(run_program_bootstrap(app, stop=asyncio.Event(), retry_s=.001), timeout=5)
    assert len(attempts) == 2
    assert len(store.list_for(MARKETPLACE_TENANT_ID)) == 2


async def test_failed_bootstrap_can_shut_down(monkeypatch):
    monkeypatch.setenv("AIUDITOR_BOOTSTRAP_CORE_SUITE", "true")
    stop = asyncio.Event()
    app = SimpleNamespace(state=SimpleNamespace(aiuditor_role=AiuditorRole.GLOBAL, suite_store=SuiteStore()))

    def unavailable(*args):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr("aiuditor_service.program_bootstrap.bootstrap_core_suite", unavailable)
    task = asyncio.create_task(run_program_bootstrap(app, stop=stop, retry_s=60))
    await asyncio.sleep(.01)
    stop.set()
    await asyncio.wait_for(task, timeout=2)


async def test_service_lifespan_initializes_persisted_suite(app, monkeypatch):
    from asgi_lifespan import LifespanManager

    monkeypatch.setenv("AIUDITOR_BOOTSTRAP_CORE_SUITE", "true")
    monkeypatch.setenv("AIUDITOR_CRAWL_SOURCE", "static")
    async with LifespanManager(app):
        await asyncio.wait_for(app.state.program_bootstrap_task, timeout=5)
        assert len(app.state.suite_store.list_for(MARKETPLACE_TENANT_ID)) == 2
        assert app.state.envelope_store.list_for(MARKETPLACE_TENANT_ID) == []
