"""Nonblocking worker exclusion; durable progress lives on the violation row."""
from contextlib import contextmanager
from threading import Lock
from fastapi import HTTPException

@contextmanager
def enforcement_lock(persist, tenant, operation):
    key = f'aiuditor-enforcement:{tenant}:{operation}'
    if hasattr(persist, 'rows'):
        locks = persist.__dict__.setdefault('_enforcement_locks', {})
        lock = locks.setdefault(key, Lock())
        if not lock.acquire(blocking=False):
            raise HTTPException(503, 'Enforcement in progress; retry')
        try:
            yield
        finally:
            lock.release()
        return
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import TimeoutError as PoolTimeout
    # Awaited network I/O must never retain the progress writer's pool slot.
    engine = persist.__dict__.get('_enforcement_lock_engine')
    if engine is None:
        original = persist._session_factory.kw['bind']
        engine = create_engine(original.url, pool_size=4, max_overflow=0, pool_timeout=0)
        persist._enforcement_lock_engine = engine
    try:
        connection = engine.connect()
    except PoolTimeout:
        raise HTTPException(503, 'Enforcement worker capacity busy; retry') from None
    with connection:
        try:
            acquired = connection.scalar(text('SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))'), {'key': key})
            if not acquired:
                raise HTTPException(503, 'Enforcement in progress; retry')
            yield
        finally:
            connection.rollback()
