"""AIuditor-owned cadence and single-writer crawl claims.

A SQL claim pins one connection and a session advisory lock until release.
Transactions are short: started_at commits before network intake, and finish
commits afterward. A process crash releases the lock but preserves started_at,
so a restart waits the remaining interval. Memory is for SQL-disabled tests.
"""
from __future__ import annotations

import hashlib
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from aiuditor_service.models import AiuditorCrawlState


def remaining_s(started, finished, now, every):
    return max(0.0, every - (now - (finished or started)).total_seconds())


@dataclass
class CrawlDecision:
    lease: object | None
    wait_s: float = 0


class MemoryCrawlSchedule:
    def __init__(self, clock=lambda: datetime.now(UTC)):
        self.clock = clock
        self.rows = {}
        self.active = set()
        self.lock = threading.Lock()

    def claim_due(self, tenant_id, every):
        with self.lock:
            now = self.clock()
            if tenant_id in self.active:
                return CrawlDecision(None, 1)
            row = self.rows.get(tenant_id)
            wait = remaining_s(row["started_at"], row["finished_at"], now, every) if row else 0
            if wait > 0:
                return CrawlDecision(None, wait)
            self.rows[tenant_id] = dict(started_at=now, finished_at=None, error_count=0)
            self.active.add(tenant_id)
            return CrawlDecision(_MemoryLease(self, tenant_id))


class _MemoryLease:
    def __init__(self, store, tenant_id):
        self.store, self.tenant_id = store, tenant_id

    def finish(self, error_count):
        with self.store.lock:
            self.store.rows[self.tenant_id].update(finished_at=self.store.clock(), error_count=error_count)

    def close(self):
        with self.store.lock:
            self.store.active.discard(self.tenant_id)


class SqlCrawlSchedule:
    def __init__(self, engine):
        self.engine = engine

    def claim_due(self, tenant_id, every):
        key = int.from_bytes(hashlib.sha256(b"aiuditor-crawl:" + tenant_id.bytes).digest()[:8], signed=True)
        connection = self.engine.connect()
        locked = False
        try:
            with connection.begin():
                locked = bool(connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}))
            if not locked:
                connection.close()
                return CrawlDecision(None, 1)
            lease = _SqlLease(connection, tenant_id, key, uuid.uuid4())
            with connection.begin():
                lease.set_tenant()
                now = connection.scalar(select(func.clock_timestamp()))
                row = connection.execute(select(AiuditorCrawlState).where(
                    AiuditorCrawlState.tenant_id == tenant_id)).mappings().first()
                wait = remaining_s(row["started_at"], row["finished_at"], now, every) if row else 0
                if wait <= 0:
                    values = dict(tenant_id=tenant_id, run_id=lease.run_id,
                                  started_at=now, finished_at=None, error_count=0)
                    connection.execute(insert(AiuditorCrawlState).values(**values)
                        .on_conflict_do_update(index_elements=["tenant_id"], set_=values))
            if wait > 0:
                lease.close()
                return CrawlDecision(None, wait)
            return CrawlDecision(lease)
        except BaseException:
            # A failed acquisition/commit can leave lock ownership uncertain.
            # Discard the physical session; rollback alone cannot unlock it.
            try:
                if not connection.closed:
                    connection.invalidate()
            finally:
                connection.close()
            raise


class _SqlLease:
    def __init__(self, connection, tenant_id, key, run_id):
        self.connection, self.tenant_id, self.key, self.run_id = connection, tenant_id, key, run_id

    def set_tenant(self):
        self.connection.execute(text("SELECT set_config('app.current_tenant_id', :tenant, true)"),
                                {"tenant": str(self.tenant_id)})

    def finish(self, error_count):
        with self.connection.begin():
            self.set_tenant()
            self.connection.execute(update(AiuditorCrawlState).where(
                AiuditorCrawlState.tenant_id == self.tenant_id,
                AiuditorCrawlState.run_id == self.run_id,
            ).values(finished_at=func.clock_timestamp(), error_count=error_count))

    def close(self):
        try:
            with self.connection.begin():
                self.connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": self.key})
        except BaseException:
            # A canceled statement can leave a healthy backend holding the
            # session lock. Never return that backend to the shared pool.
            self.connection.invalidate()
            raise
        finally:
            self.connection.close()


def resolve_crawl_schedule(persist):
    factory = getattr(persist, "_session_factory", None)
    if factory is None:
        return MemoryCrawlSchedule()
    with factory() as session:
        return SqlCrawlSchedule(session.get_bind())
