"""CI-only durable accounting commit concurrency and restart contracts."""
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from aiuditor_service.accounting import apply_commit
from aiuditor_service.domain.models import ContractStatus
from aiuditor_service.persist import SqlPersistSession
from aiuditor_service.store import ContractStore
from test_accounting import event

pytest_plugins = ['machine_postgres']
pytestmark = pytest.mark.integration


def test_commit_concurrency_restart_and_reference_isolation(migrated_postgres):
    tenant = uuid.uuid4()
    engine = create_engine(migrated_postgres)
    try:
        with engine.begin() as db:
            db.execute(text('INSERT INTO tenants(id,name,is_active) VALUES(:id,:name,true)'),
                       dict(id=tenant, name=str(tenant)))
        committed = event(tenant)
        def store():
            return ContractStore(persist=SqlPersistSession.from_url(migrated_postgres))
        def apply(_):
            return apply_commit(store(), committed)
        with ThreadPoolExecutor(4) as pool:
            rows = list(pool.map(apply, range(4)))
        assert len({r.id for r in rows}) == 1
        restarted = store()
        assert len(restarted.list_for(tenant)) == 1
        row = rows[0].model_copy(update={'status':ContractStatus.SUSPENDED})
        restarted.put(row)
        replay = apply_commit(store(), committed)
        assert replay.id == row.id and replay.status == ContractStatus.SUSPENDED
        assert replay.until == row.until
        collision = event(tenant, accounting_ref=committed.accounting_ref)
        with pytest.raises(HTTPException) as refused:
            apply_commit(store(), collision)
        assert refused.value.status_code == 409
        assert len(store().list_for(tenant)) == 1
    finally:
        engine.dispose()


def test_failed_reference_write_rolls_back_grant(migrated_postgres, monkeypatch):
    tenant = uuid.uuid4()
    engine = create_engine(migrated_postgres)
    try:
        with engine.begin() as db:
            db.execute(text('INSERT INTO tenants(id,name,is_active) VALUES(:id,:name,true)'),
                       dict(id=tenant, name=str(tenant)))
        store = ContractStore(persist=SqlPersistSession.from_url(migrated_postgres))
        original = store.put
        def fail_binding(row):
            if row.accounting_commit_id is not None:
                raise RuntimeError('simulated accounting binding write failure')
            return original(row)
        monkeypatch.setattr(store, 'put', fail_binding)
        with pytest.raises(RuntimeError, match='binding write'):
            apply_commit(store, event(tenant))
        restarted = ContractStore(persist=SqlPersistSession.from_url(migrated_postgres))
        assert restarted.list_for(tenant) == []
    finally:
        engine.dispose()
