"""Durable local decision and nonblocking worker retry against owner PostgreSQL."""
import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from sqlalchemy import create_engine, text
from aiuditor_service.domain.models import ContractViolationCreate, ViolationKind
from aiuditor_service.hypermesh import FakePlaneHop
from aiuditor_service.interfaces.api.routes import _apply_filed_violation
from aiuditor_service.persist import SqlPersistSession
from aiuditor_service.store import ContractStore, ViolationStore
from aiuditor_service.violations import put_violation
from test_violations import _signed

pytest_plugins = ['machine_postgres']
pytestmark = pytest.mark.integration


def test_concurrent_decision_restart_and_upstream_recovery(migrated_postgres):
    tenant, lease = uuid.uuid4(), uuid.uuid4()
    engine = create_engine(migrated_postgres)
    with engine.begin() as c:
        c.execute(text('INSERT INTO tenants(id,name,is_active) VALUES(:tid,:name,true)'), {'tid':tenant,'name':str(tenant)})
    persist = SqlPersistSession.from_url(migrated_postgres)
    contracts = ContractStore(persist=persist)
    contract = _signed(contracts, tenant, lease_id=lease)
    def file(_):
        p = SqlPersistSession.from_url(migrated_postgres)
        return put_violation(ViolationStore(persist=p), ContractStore(persist=p), tenant,
            contract.id, ContractViolationCreate(kind=ViolationKind.ABUSE))[0]
    with ThreadPoolExecutor(4) as pool:
        rows = list(pool.map(file, range(4)))
    assert len({r.id for r in rows}) == 1
    store = ViolationStore(persist=SqlPersistSession.from_url(migrated_postgres))
    assert len(store.list_for(tenant)) == 1
    plane = FakePlaneHop()
    plane.violate_error = 'upstream unavailable'
    app = SimpleNamespace(state=SimpleNamespace(violation_store=store, plane_hop=plane))
    req = Request({'type':'http','method':'POST','path':'/','headers':[], 'app':app})
    with pytest.raises(HTTPException) as outage:
        asyncio.run(_apply_filed_violation(req, tenant, rows[0]))
    assert outage.value.status_code == 503
    assert store.list_for(tenant)[0].enforcement_completed_at is None
    # A fresh session/store sees the same pending operation.
    app.state.violation_store = ViolationStore(persist=SqlPersistSession.from_url(migrated_postgres))
    plane.violate_error = None
    asyncio.run(_apply_filed_violation(req, tenant, rows[0]))
    asyncio.run(_apply_filed_violation(req, tenant, rows[0]))
    final = app.state.violation_store.list_for(tenant)[0]
    assert final.id == rows[0].id
    assert final.enforcement_completed_at is not None
    assert final.enforcement_attempts == 2
    assert len(plane.violations) == 1
    engine.dispose()
