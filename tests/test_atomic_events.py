"""Offline owner transaction, retry and call-site boundary contracts."""
import ast
import asyncio
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest
from starlette.requests import Request
from aiuditor_service import atomic_events as atomic
from aiuditor_service.persist import MemoryPersistSession, SqlPersistSession, _SCOPE
from aiuditor_service.store import new_stores
from aiuditor_service.interfaces.api import routes
from aiuditor_service.domain.models import ApprovalPolicyCreate

PRODUCERS = {
    'create_connection','_bind_lease','create_approval','resolve_approval_route',
    'create_policy','delete_policy','create_contract','sign_contract_route',
    'bind_contract_route','fulfill_contract_route','fulfill_accounting_contract',
    'create_catalog_service','allocate_catalog_service','record_suite_member',
    'replace_class_suite_member','seed_suite','create_admission','verdict_admission',
    'file_violation_route','evaluate_contract_route',
}


def owner():
    persist=MemoryPersistSession();persist.event_sink=[]
    stores=new_stores(persist)
    state=SimpleNamespace(**vars(stores),aiuditor_persist=persist)
    request=Request({'type':'http','headers':[], 'app':SimpleNamespace(state=state)})
    return persist,request


@pytest.mark.parametrize('producer',sorted(PRODUCERS))
def test_each_producer_uses_an_owner_operation(producer):
    source=Path(routes.__file__).read_text();tree=ast.parse(source)
    function=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name==producer)
    calls=[n for n in ast.walk(function) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)
           and n.func.id in ('publish_owned_event','publish_contract')]
    assert calls
    decorated=any(isinstance(n,ast.Name) and n.id=='atomic_events' for n in function.decorator_list)
    if not decorated:
        contexts=[n for n in ast.walk(function) if isinstance(n,ast.AsyncWith)
                  and any(isinstance(i.context_expr,ast.Call) and isinstance(i.context_expr.func,ast.Name)
                          and i.context_expr.func.id=='operation' for i in n.items)]
        assert all(any(call in list(ast.walk(context)) for context in contexts) for call in calls)


def test_production_has_no_detached_or_shared_publisher():
    root=Path(routes.__file__).parents[2]
    found=set()
    for path in root.rglob('*.py'):
        source=path.read_text()
        assert all(name not in source for name in ('get_tenant_event_publisher','PostgresEventPublisher','install_tenant_event_publisher'))
        for node in ast.walk(ast.parse(source)):
            if isinstance(node,ast.AsyncFunctionDef) and any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name)
                and n.func.id in ('publish_owned_event','publish_contract') for n in ast.walk(node)):
                found.add(node.name)
    assert found==PRODUCERS|{'publish_contract','publish_allocation'}


@pytest.mark.parametrize('outcome',['commit','rollback','insert_failure','retry'])
async def test_real_policy_write_and_event_have_one_outcome(monkeypatch,outcome):
    persist,request=owner();tenant=uuid.uuid4()
    monkeypatch.setattr(routes,'require_auditor_actor',lambda request:tenant)
    original=routes.publish_owned_event
    async def injected(*args,**kwargs):
        if outcome=='insert_failure':raise RuntimeError('outbox insertion failed')
        result=await original(*args,**kwargs)
        if outcome=='rollback':raise RuntimeError('forced outer rollback')
        if outcome=='retry':assert await original(*args,**kwargs)==result
        return result
    monkeypatch.setattr(routes,'publish_owned_event',injected)
    before=persist.snapshot()
    if outcome in ('rollback','insert_failure'):
        with pytest.raises(RuntimeError):
            await routes.create_policy(request,ApprovalPolicyCreate(purpose='renter',disposition='hold'))
        assert persist.snapshot()==before and persist.event_sink==[]
    else:
        row=await routes.create_policy(request,ApprovalPolicyCreate(purpose='renter',disposition='hold'))
        assert str(row.id) in persist.rows['policies'][str(tenant)]
        assert len(persist.rows['service_outbox'][str(tenant)])==1
        assert len(persist.event_sink)==1


async def test_delete_rolls_back_when_outbox_fails(monkeypatch):
    persist,request=owner();tenant=uuid.uuid4()
    monkeypatch.setattr(routes,'require_auditor_actor',lambda request:tenant)
    row=await routes.create_policy(request,ApprovalPolicyCreate(purpose='renter',disposition='hold'))
    before=persist.snapshot()
    async def fail(*args,**kwargs):raise RuntimeError('outbox insertion failed')
    monkeypatch.setattr(routes,'publish_owned_event',fail)
    with pytest.raises(RuntimeError):await routes.delete_policy(request,row.id)
    assert persist.snapshot()==before and len(persist.event_sink)==1


async def test_unrelated_concurrent_write_survives_operation_rollback():
    persist,request=owner();tenant=uuid.uuid4();entered=asyncio.Event();release=asyncio.Event()
    async def failed():
        with pytest.raises(RuntimeError):
            async with atomic.operation(persist):
                persist.upsert('policies',tenant,'first',{'value':1})
                entered.set();await release.wait();raise RuntimeError('rollback')
    task=asyncio.create_task(failed());await entered.wait()
    async with atomic.operation(persist):persist.upsert('policies',tenant,'second',{'value':2})
    release.set();await task
    assert persist.load_rows('policies',tenant)=={'second':{'value':2}}


async def test_outbox_cannot_be_written_without_an_owner_operation():
    persist,request=owner()
    with pytest.raises(ValueError,match='owner transaction'):
        await atomic.publish_owned_event(request,tenant_id=uuid.uuid4(),event_type='unused',payload={})
    assert 'service_outbox' not in persist.rows


def test_scoped_sql_writes_do_not_open_or_commit_a_second_session():
    session=Mock();session.begin_nested.return_value=nullcontext()
    factory=Mock(side_effect=AssertionError('second connection'))
    persist=SqlPersistSession(factory);token=_SCOPE.set(session)
    try:
        with persist.write_session() as bound:assert bound is session
        with persist.candidate_scope():assert _SCOPE.get() is session
    finally:_SCOPE.reset(token)
    factory.assert_not_called();session.commit.assert_not_called()


@pytest.mark.parametrize('cancel',[False,True])
async def test_sql_pool_wait_does_not_block_the_loop_or_leak_on_cancellation(monkeypatch,cancel):
    from contextlib import contextmanager
    from threading import Event
    entered,release=Event(),Event()
    connection=Mock()
    def connect():
        entered.set()
        assert release.wait(3)
        return connection
    factory=Mock();factory.kw={'bind':SimpleNamespace(connect=connect)}
    persist=SqlPersistSession(factory)
    @contextmanager
    def scope(*,connection):
        yield
    monkeypatch.setattr(persist,'candidate_scope',scope)
    async def run():
        async with atomic.operation(persist):pass
    task=asyncio.create_task(run())
    assert await asyncio.to_thread(entered.wait,2)
    if cancel:task.cancel()
    await asyncio.sleep(0)
    release.set()
    if cancel:
        with pytest.raises(asyncio.CancelledError):await task
    else:await task
    connection.close.assert_called_once()


async def test_admission_and_submitted_event_are_visible_before_lab_dispatch(monkeypatch):
    from aiuditor_service.domain.models import AdmissionCreate
    from aiuditor_service.hypermesh import FakePlaneHop
    persist,request=owner();tenant=uuid.uuid4();plane=FakePlaneHop()
    request.app.state.plane_hop=plane
    monkeypatch.setattr(routes,'require_auditor_actor',lambda request:tenant)
    original=plane.queue_lab_soak
    async def queued(*args,**kwargs):
        assert atomic._CURRENT.get() is None
        assert str(kwargs['admission_id']) in persist.rows['admissions'][str(tenant)]
        assert len(persist.rows['service_outbox'][str(tenant)])==1
        return await original(*args,**kwargs)
    monkeypatch.setattr(plane,'queue_lab_soak',queued)
    row=await routes.create_admission(request,AdmissionCreate(file_hash='sha256:'+'ab'*32,
        format='gguf',license='mit',intended_class='fixture-class',hold_for_human=False))
    assert row.lab_assignment is not None


async def test_promotion_failure_retains_verdict_and_one_event_for_retry(monkeypatch):
    from unittest.mock import AsyncMock
    from fastapi import HTTPException
    from aiuditor_service.domain.models import AdmissionCreate,AdmissionResolve,AdmissionStatus,AdmissionVerdict
    from aiuditor_service.hypermesh import FakePlaneHop
    persist,request=owner();tenant=uuid.uuid4();plane=FakePlaneHop(promote_error='offline')
    request.app.state.plane_hop=plane
    monkeypatch.setattr(routes,'require_auditor_actor',lambda request:tenant)
    monkeypatch.setattr(routes,'resolver_subject',AsyncMock(return_value='original-human'))
    store=request.app.state.admission_store
    row=routes.submit_admission(store,tenant,AdmissionCreate(file_hash='sha256:'+'ab'*32,
        format='gguf',license='mit',intended_class='fixture-class'),run_pipeline=False)
    store.put(row.model_copy(update={'status':AdmissionStatus.HOLD,'sandbox_soak_ok':True,'sandbox_egress_ok':True}))
    body=AdmissionResolve(verdict='allow',reason='reviewed')
    with pytest.raises(HTTPException) as error:await routes.verdict_admission(request,row.id,body)
    assert error.value.status_code==502
    accepted=store.get(tenant,row.id)
    assert accepted.verdict is AdmissionVerdict.ALLOW
    before=persist.snapshot()['service_outbox']
    plane.promote_error=None
    monkeypatch.setattr(routes,'resolver_subject',AsyncMock(return_value='retry-human'))
    retried=await routes.verdict_admission(request,row.id,body)
    assert retried.resolved_by=='original-human' and retried.resolved_at==accepted.resolved_at
    assert persist.snapshot()['service_outbox']==before
    assert len(before[str(tenant)])==1


async def test_suite_restoration_emits_a_new_transition_but_retry_does_not(monkeypatch):
    from aiuditor_service.domain.models import SuiteMemberCreate, SuiteMemberReplace
    persist,request=owner();tenant=uuid.uuid4()
    monkeypatch.setattr(routes,'require_auditor_actor',lambda request:tenant)
    monkeypatch.setattr(routes,'_require_operator_program',lambda *a,**k:None)
    first=await routes.record_suite_member(request,SuiteMemberCreate(class_id='agx-large',catalog_id='model-a',pack_id='pack-v1'))
    for old,new in [('model-a','model-b'),('model-b','model-a')]:
        body=SuiteMemberReplace(class_id=first.class_id,catalog_id=new,pack_id='pack-v1',expected_catalog_id=old,expected_pack_id='pack-v1')
        row=await routes.replace_class_suite_member(request,first.id,body)
    assert row.revision==2
    assert len(persist.rows['service_outbox'][str(tenant)])==3
    replay=await routes.replace_class_suite_member(request,first.id,body)
    assert replay.revision==2 and len(persist.rows['service_outbox'][str(tenant)])==3


async def test_pool_waiters_cannot_starve_route_workers(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Semaphore
    slot=Semaphore(1)
    def connect():
        assert slot.acquire(timeout=2)
        return SimpleNamespace(close=slot.release)
    factory=Mock();factory.kw={'bind':SimpleNamespace(connect=connect)}
    persist=SqlPersistSession(factory)
    @contextmanager
    def scope(*,connection):yield
    monkeypatch.setattr(persist,'candidate_scope',scope)
    loop=asyncio.get_running_loop();original=loop._default_executor
    executor=ThreadPoolExecutor(max_workers=2)
    loop.set_default_executor(executor)
    async def request():
        async with atomic.operation(persist):
            await asyncio.sleep(.02)
            assert await asyncio.to_thread(lambda:True)
    try:await asyncio.wait_for(asyncio.gather(*(request() for _ in range(3))),1)
    finally:
        loop._default_executor=original
        executor.shutdown(wait=True)


async def test_issued_lease_keeps_signed_authority_when_allocation_event_fails(monkeypatch):
    from datetime import UTC,datetime
    from unittest.mock import AsyncMock
    from aiuditor_service import catalog
    from aiuditor_service.domain.models import Connection,ServiceCreate,ServiceAllocate
    from aiuditor_service.hypermesh import FakePlaneHop,FakeHostLookup
    persist,request=owner();stores=new_stores(persist);tenant=uuid.uuid4()
    connection=Connection(id=uuid.uuid4(),tenant_id=tenant,kind='host',purpose='lease',
        device_id=uuid.uuid4(),status='ready',created_at=datetime.now(UTC))
    stores.connection_store.put(connection)
    service=catalog.put_service(stores.catalog_store,stores.connection_store,tenant,
        ServiceCreate(title='Class allocation',backend='hypermesh_class',class_id='agx-large'))
    monkeypatch.setattr(catalog,'pick_live_connection',AsyncMock(return_value=connection))
    plane=FakePlaneHop();original=plane.offer_lease
    async def offer(*args,**kwargs):
        assert atomic._CURRENT.get() is None
        signed=stores.contract_store.get(tenant,kwargs['contract_id'])
        assert signed is not None and signed.connection_id==connection.id
        return await original(*args,**kwargs)
    monkeypatch.setattr(plane,'offer_lease',offer)
    async def fail(allocation):raise RuntimeError('outbox unavailable')
    with pytest.raises(RuntimeError,match='outbox unavailable'):
        await catalog.allocate_service(stores.catalog_store,stores.connection_store,stores.contract_store,
            tenant,service.id,ServiceAllocate(renter="fixture-renter"),lookup=FakeHostLookup(),plane=plane,
            signed_by=str(uuid.uuid4()),on_allocated=fail)
    assert len(plane.leases)==1
    issued=next(iter(plane.leases.values()))
    authority=stores.contract_store.get(tenant,issued.contract_id)
    assert authority is not None and authority.connection_id==connection.id
    assert not persist.rows.get('service_outbox')


async def test_post_suite_pack_restoration_preserves_each_transition(monkeypatch):
    from aiuditor_service.domain.models import SuiteMemberCreate
    persist,request=owner();tenant=uuid.uuid4()
    monkeypatch.setattr(routes,'require_auditor_actor',lambda request:tenant)
    monkeypatch.setattr(routes,'_require_operator_program',lambda *a,**k:None)
    for pack in ['pack-a','pack-b','pack-a','pack-a']:
        row=await routes.record_suite_member(request,SuiteMemberCreate(class_id='agx-large',catalog_id='model-a',pack_id=pack))
    assert row.revision==2 and len(persist.rows['service_outbox'][str(tenant)])==3


async def test_get_connection_waits_without_blocking_owner_holder(monkeypatch):
    from contextlib import contextmanager
    from threading import Semaphore
    from fastapi import HTTPException
    slot=Semaphore(1)
    def connect():
        assert slot.acquire(timeout=2)
        return SimpleNamespace(close=slot.release)
    factory=Mock(side_effect=AssertionError('unscoped read checkout'))
    factory.kw={'bind':SimpleNamespace(connect=connect)}
    persist=SqlPersistSession(factory)
    @contextmanager
    def scope(*,connection):
        token=_SCOPE.set(Mock())
        try:yield
        finally:_SCOPE.reset(token)
    monkeypatch.setattr(persist,'candidate_scope',scope)
    def load_rows(*args):
        assert _SCOPE.get() is not None
        return {}
    monkeypatch.setattr(persist,'load_rows',load_rows)
    monkeypatch.setattr(persist,'hydrate',lambda stores:None)
    stores=new_stores(persist);tenant=uuid.uuid4()
    request=Request({'type':'http','headers':[], 'app':SimpleNamespace(state=SimpleNamespace(**vars(stores),aiuditor_persist=persist))})
    monkeypatch.setattr(routes,'require_tenant_id',lambda request:tenant)
    async with atomic.operation(persist):
        task=asyncio.create_task(routes.get_connection(request,uuid.uuid4()),context=__import__('contextvars').Context())
        await asyncio.sleep(.03)
        assert not task.done()
    with pytest.raises(HTTPException) as error:await asyncio.wait_for(task,1)
    assert error.value.status_code==404
