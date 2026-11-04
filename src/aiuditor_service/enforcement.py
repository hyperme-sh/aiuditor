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
    from sqlalchemy import text
    with persist._session_factory() as session:
        try:
            acquired = session.scalar(text('SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))'), {'key': key})
            if not acquired:
                raise HTTPException(503, 'Enforcement in progress; retry')
            yield
        finally:
            session.rollback()
