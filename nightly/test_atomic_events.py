"""Owner SQL transaction cases registered only in Infra's scheduled batch."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import create_engine,text
from sqlalchemy.orm import sessionmaker
from controlplane_events.envelope import ControlPlaneEventEnvelope
from controlplane_events.contracts import validate_event_envelope
from aiuditor_service import atomic_events as atomic
from aiuditor_service.persist import SqlPersistSession
from aiuditor_service.interfaces.api import routes

PRODUCERS = ('create_connection','_bind_lease','create_approval','resolve_approval_route',
    'create_policy','delete_policy','create_contract','sign_contract_route',
    'bind_contract_route','fulfill_contract_route','fulfill_accounting_contract',
    'create_catalog_service','allocate_catalog_service','record_suite_member',
    'replace_class_suite_member','seed_suite','create_admission','verdict_admission',
    'file_violation_route','evaluate_contract_route')


@pytest.fixture
async def owner(producer,monkeypatch):
    path=Path(os.environ['AIUDITOR_OUTBOX_FIXTURE_SCRIPT'])
    spec=importlib.util.spec_from_file_location('aiuditor_fixture',path)
    fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
    fixture.environment(os.environ)
    def command(action):subprocess.run([sys.executable,str(path),action],check=True,timeout=180)
    command('reset-case')
    url=(Path(os.environ['AIUDITOR_OUTBOX_FIXTURE_DIR'])/'owner-url').read_text()
    engine=create_engine(url)
    persist=SqlPersistSession(sessionmaker(engine,expire_on_commit=False))
    try:
        invoke,request=fixture.handlers(persist,producer,monkeypatch)
        yield engine,persist,fixture.configuration(),command,invoke,request
    finally:
        lock_engine=getattr(persist,'_enforcement_lock_engine',None)
        if lock_engine is not None:lock_engine.dispose()
        engine.dispose()


def outbox(engine,tenant):
    with engine.begin() as db:
        db.execute(text("SELECT set_config('app.current_tenant_id',:tenant,true)"),{'tenant':tenant})
        rows=db.execute(text('SELECT id,envelope FROM service_outbox ORDER BY id')).all()
        for _,value in rows:
            envelope=ControlPlaneEventEnvelope.model_validate(value)
            validate_event_envelope(envelope)
            assert str(envelope.tenant_id)==tenant
        return rows


@pytest.mark.parametrize('producer',PRODUCERS)
@pytest.mark.parametrize('outcome',['commit','rollback','insert_failure','retry'])
async def test_owner_transaction(producer,outcome,owner,monkeypatch):
    engine,persist,config,command,invoke,request=owner
    before=persist.snapshot();before_events=outbox(engine,config['tenant_id'])
    assert not before_events
    original=atomic.publish_owned_event;captured=[]
    async def publish(*args,**kwargs):
        event_id=await original(*args,**kwargs)
        captured.append(kwargs)
        if outcome=='rollback':raise RuntimeError('forced outer rollback')
        if outcome=='retry':assert await original(*args,**kwargs)==event_id
        return event_id
    monkeypatch.setattr(routes,'publish_owned_event',publish)
    monkeypatch.setattr(atomic,'publish_owned_event',publish)
    if outcome=='insert_failure':command('break-outbox')
    if outcome in ('rollback','insert_failure'):
        from sqlalchemy.exc import IntegrityError
        with pytest.raises(RuntimeError if outcome=='rollback' else IntegrityError):await invoke()
        assert persist.snapshot()==before
        assert outbox(engine,config['tenant_id'])==before_events
    else:
        await invoke()
        after=persist.snapshot();events=outbox(engine,config['tenant_id'])
        assert after!=before and events and captured
        if outcome=='retry':
            async with atomic.operation(persist):
                for payload in captured:await original(request,**payload)
            assert outbox(engine,config['tenant_id'])==events
            assert persist.snapshot()==after
    assert not outbox(engine,config['other_tenant_id'])
    for rows in persist.snapshot().values():assert not rows.get(config['other_tenant_id'])


@pytest.mark.parametrize('producer',['mint_connection_ticket'])
async def test_auditor_ticket_recovery(producer,owner,monkeypatch):
    engine,persist,config,command,invoke,request=owner
    original=routes.publish_owned_event
    async def fail_after_insert(*args,**kwargs):
        await original(*args,**kwargs)
        raise RuntimeError('forced publication rollback')
    monkeypatch.setattr(routes,'publish_owned_event',fail_after_insert)
    with pytest.raises(RuntimeError,match='forced publication rollback'):await invoke()
    import uuid
    tenant=uuid.UUID(config['tenant_id']);connection=uuid.UUID(config['connection_id'])
    store=request.app.state.connection_store
    pending=store.get(tenant,connection)
    assert pending.lease_id is None and pending.pending_auditor_grant is not None
    assert 'pending_auditor_grant' not in pending.model_dump()
    assert not outbox(engine,config['tenant_id'])
    monkeypatch.setattr(routes,'publish_owned_event',original)
    granted=await invoke()
    bound=store.get(tenant,connection)
    assert bound.lease_id==granted.lease_id and bound.pending_auditor_grant is None
    assert len(request.app.state.plane_hop.grants)==1
    assert len(outbox(engine,config['tenant_id']))==1
    assert not outbox(engine,config['other_tenant_id'])
