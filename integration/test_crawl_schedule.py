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
from test_persist_sql import _plant_tenant
from machine_postgres import migrated_postgres


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
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert MARKETPLACE_TENANT_ID not in schedule.active


@pytest.mark.asyncio
@pytest.mark.parametrize("intake_error", [False, True])
async def test_cancellation_joins_completion_before_close(monkeypatch, intake_error):
    import threading
    from aiuditor_service.crawl_schedule import CrawlDecision
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    closed = []
    class Lease:
        def finish(self, _errors):
            started.set()
            assert release.wait(5)
            finished.set()
        def close(self):
            assert finished.is_set(), "connection closed during active completion"
            closed.append(True)
    lease = Lease()
    state = SimpleNamespace(aiuditor_role=AiuditorRole.GLOBAL,
                            crawl_schedule=SimpleNamespace(claim_due=lambda *_: CrawlDecision(lease)),
                            plane_hop=None, candidate_store=None, envelope_store=None)
    async def intake(**_):
        if intake_error:
            raise RuntimeError("intake failed")
        return IntakeReport(0, 0, 0, 0, 0)
    monkeypatch.setattr("aiuditor_service.intake_loop.run_intake", intake)
    monkeypatch.delenv("AIUDITOR_CRAWL_TENANT_ID", raising=False)
    task = asyncio.create_task(run_crawl_loop(SimpleNamespace(state=state), stop=asyncio.Event()))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()  # Repeated shutdown cancellation must also retain the worker.
        await asyncio.sleep(0)
        assert not task.done() and closed == []
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert closed == [True]


@pytest.mark.parametrize("failure", ["unlock", "acquisition"])
def test_uncertain_lock_connection_is_discarded(migrated_postgres, failure):
    from sqlalchemy import event
    tenant = uuid.uuid4()
    _plant_tenant(migrated_postgres, tenant)
    engine = create_engine(migrated_postgres, pool_size=1, max_overflow=0)
    schedule = SqlCrawlSchedule(engine)
    lease = schedule.claim_due(tenant, 0).lease if failure == "unlock" else None
    def fail_statement(_conn, _cursor, statement, _params, _context, _many):
        if ((failure == "unlock" and "pg_advisory_unlock" in statement)
                or (failure == "acquisition" and "INSERT INTO aiuditor_crawl_state" in statement)):
            raise RuntimeError("statement canceled without disconnect")
    event.listen(engine, "before_cursor_execute", fail_statement)
    try:
        with pytest.raises(RuntimeError, match="statement canceled"):
            if lease is not None:
                lease.close()
            else:
                schedule.claim_due(tenant, 0)
    finally:
        event.remove(engine, "before_cursor_execute", fail_statement)
    # If the old backend returned to the pool, the lock is still held/reentrant.
    # An independent engine proves no abandoned session retains the tenant lock.
    independent = create_engine(migrated_postgres)
    try:
        recovered = SqlCrawlSchedule(independent).claim_due(tenant, 0)
        assert recovered.lease is not None
        recovered.lease.close()
    finally:
        independent.dispose()
        engine.dispose()


@pytest.mark.asyncio
async def test_cleanup_failure_retries_instead_of_killing_crawl_loop(monkeypatch):
    from aiuditor_service.crawl_schedule import CrawlDecision
    stop = asyncio.Event()
    calls = []
    class Lease:
        def finish(self, _errors):
            pass
        def close(self):
            raise OSError("transient unlock failure")
    class Schedule:
        def claim_due(self, *_):
            calls.append(True)
            if len(calls) > 1:
                stop_loop.call_soon_threadsafe(stop.set)
            return CrawlDecision(Lease())
    stop_loop = asyncio.get_running_loop()
    state = SimpleNamespace(aiuditor_role=AiuditorRole.GLOBAL, crawl_schedule=Schedule(),
                            plane_hop=None, candidate_store=None, envelope_store=None)
    async def intake(**_):
        return IntakeReport(0, 0, 0, 0, 0)
    monkeypatch.delenv("AIUDITOR_CRAWL_TENANT_ID", raising=False)
    monkeypatch.setattr("aiuditor_service.intake_loop.run_intake", intake)
    monkeypatch.setattr("aiuditor_service.intake_loop.resolve_crawl_every_s", lambda: 0.001)
    await asyncio.wait_for(run_crawl_loop(SimpleNamespace(state=state), stop=stop), timeout=5)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_shutdown_cancellation_survives_cleanup_failure(monkeypatch):
    from aiuditor_service.crawl_schedule import CrawlDecision
    running = asyncio.Event()
    class Lease:
        def finish(self, _errors):
            pytest.fail("canceled intake must not be finished")
        def close(self):
            raise OSError("unlock failed")
    state = SimpleNamespace(aiuditor_role=AiuditorRole.GLOBAL,
                            crawl_schedule=SimpleNamespace(claim_due=lambda *_: CrawlDecision(Lease())),
                            plane_hop=None, candidate_store=None, envelope_store=None)
    async def intake(**_):
        running.set()
        await asyncio.Event().wait()
    monkeypatch.delenv("AIUDITOR_CRAWL_TENANT_ID", raising=False)
    monkeypatch.setattr("aiuditor_service.intake_loop.run_intake", intake)
    task = asyncio.create_task(run_crawl_loop(SimpleNamespace(state=state), stop=asyncio.Event()))
    await asyncio.wait_for(running.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
