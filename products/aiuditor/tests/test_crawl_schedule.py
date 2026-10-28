"""Durable crawl cadence survives restarts and coordinates writer replicas."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import asyncio
import uuid

import pytest
from sqlalchemy import create_engine, select, text
from aiuditor_service.crawl_schedule import MemoryCrawlSchedule, SqlCrawlSchedule
from aiuditor_service.models import AiuditorCrawlState
from aiuditor_service.intake import IntakeReport
from aiuditor_service.intake_loop import run_crawl_loop
from aiuditor_service.role import AiuditorRole
from test_persist_sql import _plant_tenant, _skip_without_docker, _normalize_sync_url


@pytest.fixture(scope="module")
def migrated_postgres():
    from testcontainers.postgres import PostgresContainer
    from controlplane_db.migrations import upgrade
    _skip_without_docker()
    with PostgresContainer("postgres:16-alpine") as postgres:
        url = _normalize_sync_url(postgres.get_connection_url())
        upgrade(url)
        yield url


def test_remaining_interval_and_unfinished_run():
    now = datetime(2026, 10, 28, tzinfo=UTC)
    clock = [now]
    store = MemoryCrawlSchedule(clock=lambda: clock[0])
    tenant = uuid.uuid4()
    first = store.claim_due(tenant, 100)
    assert first.lease is not None
    assert store.claim_due(tenant, 100).lease is None
    first.lease.close()  # Crash before finish preserves started_at.
    clock[0] += timedelta(seconds=40)
    assert store.claim_due(tenant, 100).wait_s == 60
    clock[0] += timedelta(seconds=60)
    second = store.claim_due(tenant, 100)
    second.lease.finish(3)
    second.lease.close()
    assert store.rows[tenant]["error_count"] == 3
    assert store.rows[tenant]["finished_at"] == clock[0]
    assert store.claim_due(tenant, 100).wait_s == 100


def test_sql_restart_and_multiple_writers(migrated_postgres):
    tenant = uuid.uuid4()
    _plant_tenant(migrated_postgres, tenant)
    engine = create_engine(migrated_postgres)
    first_store, restarted = SqlCrawlSchedule(engine), SqlCrawlSchedule(engine)
    first = first_store.claim_due(tenant, 3600)
    assert first.lease is not None
    assert restarted.claim_due(tenant, 0).lease is None  # Active run even after cadence is due.
    first.lease.close()  # Unfinished run, as after process death.
    decision = restarted.claim_due(tenant, 3600)
    assert decision.lease is None and 3500 < decision.wait_s <= 3600
    second = restarted.claim_due(tenant, 0)
    assert second.lease is not None
    second.lease.finish(2)
    second.lease.close()
    with engine.begin() as cx:
        row = cx.execute(select(AiuditorCrawlState).where(AiuditorCrawlState.tenant_id == tenant)).mappings().one()
        assert row["started_at"] and row["finished_at"] >= row["started_at"]
        assert row["error_count"] == 2
        flags = cx.execute(text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname='aiuditor_crawl_state'")).one()
        assert tuple(flags) == (True, True)
    assert restarted.claim_due(tenant, 3600).lease is None
    engine.dispose()


@pytest.mark.asyncio
async def test_loop_waits_remaining_interval_without_crawling(monkeypatch):
    store = MemoryCrawlSchedule()
    tenant = uuid.uuid4()
    first = store.claim_due(tenant, 3600)
    first.lease.finish(0)
    first.lease.close()
    state = SimpleNamespace(aiuditor_role=AiuditorRole.GLOBAL, crawl_schedule=store)
    stop = asyncio.Event()
    monkeypatch.setattr("aiuditor_service.intake_loop.resolve_crawl_tenant", lambda: tenant)
    monkeypatch.setattr("aiuditor_service.intake_loop.resolve_crawl_every_s", lambda: 3600)
    async def forbidden(**_):
        pytest.fail("restart crawled before the remaining interval elapsed")
    monkeypatch.setattr("aiuditor_service.intake_loop.run_intake", forbidden)
    task = asyncio.create_task(run_crawl_loop(SimpleNamespace(state=state), stop=stop))
    await asyncio.sleep(0.02)
    stop.set()
    await asyncio.wait_for(task, timeout=2)


@pytest.mark.asyncio
async def test_loop_records_report_errors_and_exceptions(monkeypatch):
    tenant = uuid.uuid4()
    monkeypatch.setattr("aiuditor_service.intake_loop.resolve_crawl_tenant", lambda: tenant)
    for failure in (False, True):
        stop = asyncio.Event()
        store = MemoryCrawlSchedule()
        state = SimpleNamespace(aiuditor_role=AiuditorRole.OPERATOR, crawl_schedule=store,
                                plane_hop=None, candidate_store=None, envelope_store=None)
        async def intake(**_):
            stop.set()
            if failure:
                raise RuntimeError("failed")
            return IntakeReport(0, 0, 0, 0, 0, errors=("one", "two"))
        monkeypatch.setattr("aiuditor_service.intake_loop.run_intake", intake)
        await run_crawl_loop(SimpleNamespace(state=state), stop=stop)
        assert store.rows[tenant]["finished_at"] is not None
        assert store.rows[tenant]["error_count"] == (1 if failure else 2)
        assert tenant not in store.active


@pytest.mark.asyncio
async def test_cancellation_during_claim_acquisition_releases_writer_lock(monkeypatch):
    import threading
    from aiuditor_service.intake import MARKETPLACE_TENANT_ID
    started, release = threading.Event(), threading.Event()
    class SlowSchedule(MemoryCrawlSchedule):
        def claim_due(self, tenant_id, every):
            started.set()
            assert release.wait(5)
            return super().claim_due(tenant_id, every)
    schedule = SlowSchedule()
    state = SimpleNamespace(aiuditor_role=AiuditorRole.GLOBAL, crawl_schedule=schedule)
    monkeypatch.delenv("AIUDITOR_CRAWL_TENANT_ID", raising=False)
    task = asyncio.create_task(run_crawl_loop(SimpleNamespace(state=state), stop=asyncio.Event()))
    assert await asyncio.to_thread(started.wait, 2)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert MARKETPLACE_TENANT_ID not in schedule.active
