import copy
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import uuid
import pytest
from fastapi import Request, Response, HTTPException
from aiuditor_service import access_policy_routes as routes
from aiuditor_service.access_policy_state import PolicyMutation
from aiuditor_service.access_policy_store import consume
from aiuditor_service.access_preview import Policy
from test_access_preview import AccessPreviewTests as PreviewFixture


def setup():
    fixture=PreviewFixture();fixture.setUp()
    tenant=uuid.UUID(fixture.ids['tenant_id']);org=uuid.UUID(fixture.ids['organization_id'])
    row=SimpleNamespace(tenant_id=tenant,organization_id=org,state={'tenant_id':str(tenant),
        'organization_id':str(org),'revision':str(uuid.uuid4()),'sequence':0,'restrictions':{},
        'base':fixture.policy_data})
    return fixture,row


@pytest.mark.asyncio
async def test_owner_routes_scope_personal_edits_and_refuse_stale_writes():
    f,row=setup();session=Mock();request=Request({'type':'http','headers':[]})
    @asynccontextmanager
    async def transaction(tenant):
        assert tenant==row.tenant_id
        yield session
    authority=AsyncMock()
    with patch.object(routes,'human',return_value=(row.tenant_id,'owner')),patch.object(routes,'require_authority',authority),patch.object(routes,'transaction',transaction),patch.object(routes.store,'locked',AsyncMock(return_value=row)):
        body=PolicyMutation(organization_id=row.organization_id,expected_revision=row.state['revision'],scope='personal',restriction={'operation':'read','target':'/project/file','mode':'deny','reason':'Personal restriction'})
        result=await routes.change_policy(body,request,Response())
        assert result['sequence']==1 and result['restrictions'][0]['subject']=='owner'
        authority.assert_awaited_once_with(request,row.tenant_id,'owner','organization.read',row.organization_id)
        assert session.add.call_count==1
        with pytest.raises(HTTPException) as error:await routes.change_policy(body,request,Response())
        assert error.value.status_code==409 and session.add.call_count==1


@pytest.mark.asyncio
async def test_cross_org_denial_precedes_policy_access():
    f,row=setup();request=Request({'type':'http','headers':[]})
    with patch.object(routes,'human',return_value=(row.tenant_id,'owner')),patch.object(routes,'require_authority',AsyncMock(side_effect=HTTPException(403,'Wrong selected org'))),patch.object(routes.store,'locked',AsyncMock()) as locked:
        with pytest.raises(HTTPException):await routes.get_policy(uuid.uuid4(),request,Response())
        locked.assert_not_awaited()


@pytest.mark.asyncio
async def test_admission_consumes_once_and_denials_do_not_consume():
    f,row=setup();session=SimpleNamespace(scalar=AsyncMock(side_effect=[f.body.request_id,None]))
    from aiuditor_service.access_preview import evaluate
    decision=evaluate(f.body,f.fact,Policy.model_validate(f.policy_data),now=100)
    accepted=await consume(session,row,f.body,decision)
    assert accepted['dispatch_admitted'] is True
    with pytest.raises(HTTPException) as error:await consume(session,row,f.body,decision)
    assert error.value.status_code==409
    denied={**decision,'permitted':False}
    assert await consume(session,row,f.body,denied)==denied
    assert session.scalar.await_count==2


@pytest.mark.asyncio
async def test_history_refuses_partial_rollback_and_file_cannot_replace_current_state():
    from aiuditor_service.access_policy_store import locked
    f,row=setup()
    row.state['sequence']=1
    latest=SimpleNamespace(sequence=1,change={'revision':row.state['revision']})
    session=SimpleNamespace(scalar=AsyncMock(side_effect=[row,latest]))
    seed=Mock(side_effect=AssertionError('Existing owner state must not read old file'))
    assert await locked(session,row.tenant_id,row.organization_id,seed) is row
    seed.assert_not_called()
    stale=SimpleNamespace(**vars(row));stale.state={**row.state,'sequence':0,'revision':str(uuid.uuid4())}
    for old in (stale,None):
        session=SimpleNamespace(scalar=AsyncMock(side_effect=[old,latest]))
        with pytest.raises(HTTPException) as error:await locked(session,row.tenant_id,row.organization_id,seed)
        assert error.value.status_code==503
