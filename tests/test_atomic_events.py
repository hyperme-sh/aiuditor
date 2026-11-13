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
    assert found==PRODUCERS|{'publish_contract'}


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
