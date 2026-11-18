import uuid
import pytest
from aiuditor_service.access_dream import Dream, review
from aiuditor_service.access_preview import Policy
from test_access_preview import AccessPreviewTests

def fixture():
 f=AccessPreviewTests();f.setUp()
 b=Dream(version=1,**{k:v for k,v in f.ids.items() if k not in ('organization_id','owner_id')},project='/project',records=[dict(rule_hash='a'*64,policy_revision=f.policy_data['revision'],action=f.body.action,last_use=90,expires_at=120,review_pending=False)])
 return f,b

@pytest.mark.parametrize('change,kind,status',[
 ({},'retain','applied'),({'review_pending':True},'narrow','applied'),
 ({'expires_at':100},'expire','applied'),({'policy_revision':uuid.uuid4()},'retain','superseded')])
def test_bounded_review(change,kind,status):
 f,b=fixture()
 for k,v in change.items():setattr(b.records[0],k,v)
 out=review(b,f.fact,Policy.model_validate(f.policy_data),100)[0]
 assert (out['kind'],out['status'])==(kind,status)

def test_protection_never_overrides_explicit_denial():
 f,b=fixture();b.records[0].review_pending=True
 rule=f.policy_data['organizations'][0]['rules'][0];rule['dream_protected']=True
 assert review(b,f.fact,Policy.model_validate(f.policy_data),100)[0]['kind']=='retain'
 rule['effect']='deny'
 assert review(b,f.fact,Policy.model_validate(f.policy_data),100)[0]['kind']=='revoke'

def test_advisory_does_not_block_original_and_later_narrows():
 f,b=fixture();f.policy_data['organizations'][0]['rules'][0]['effect']='advisory'
 assert f.result()['permitted']
 b.records[0].review_pending=True
 assert review(b,f.fact,Policy.model_validate(f.policy_data),100)[0]['kind']=='narrow'

def test_duplicate_and_cross_identity_refused():
 f,b=fixture();b.records.append(b.records[0])
 with pytest.raises(ValueError):review(b,f.fact,Policy.model_validate(f.policy_data),100)
 b.records.pop();f.fact['session_id']=str(uuid.uuid4())
 with pytest.raises(ValueError):review(b,f.fact,Policy.model_validate(f.policy_data),100)

@pytest.mark.asyncio
async def test_owner_transaction_retry_and_manual_change_never_reapply(monkeypatch):
 from contextlib import asynccontextmanager
 from types import SimpleNamespace
 from unittest.mock import AsyncMock,Mock
 from fastapi import Request,Response,HTTPException
 from aiuditor_service import access_dream as route
 from aiuditor_service.role import AiuditorRole
 f,b=fixture();b.records[0].review_pending=True
 row=SimpleNamespace(tenant_id=b.tenant_id,organization_id=uuid.UUID(f.ids['organization_id']),state={'tenant_id':str(b.tenant_id),'organization_id':f.ids['organization_id'],'revision':f.policy_data['revision'],'sequence':0,'restrictions':{},'base':f.policy_data})
 prior=[]
 session=SimpleNamespace(scalar=AsyncMock(side_effect=lambda *_:prior[0] if prior else None),add=lambda value:prior.append(value) if isinstance(value,route.AiuditorAccessDream) else None)
 @asynccontextmanager
 async def transaction(tenant):
  assert tenant==b.tenant_id
  yield session
 monkeypatch.setattr(route,'transaction',transaction)
 monkeypatch.setattr(route.store,'locked',AsyncMock(return_value=row))
 monkeypatch.setattr(route,'machine_context',AsyncMock(return_value=f.fact))
 monkeypatch.setattr(route,'role_from_request',lambda _:AiuditorRole.GLOBAL)
 monkeypatch.setattr(route.time,'time',lambda:100)
 request=Request({'type':'http','headers':[(b'authorization',b'Bearer hm_msi_private'),(b'x-tenant-id',str(b.tenant_id).encode())]})
 first=await route.dreaming(b,request,Response());assert first['outcomes'][0]['kind']=='narrow';assert row.state['sequence']==1
 row.state={**row.state,'revision':str(uuid.uuid4()),'sequence':2}
 second=await route.dreaming(b,request,Response());assert second==first;assert row.state['sequence']==2
 b.records[0].last_use=80
 with pytest.raises(HTTPException) as error:await route.dreaming(b,request,Response())
 assert error.value.status_code==409

@pytest.mark.asyncio
async def test_witness_cannot_start_dreaming(monkeypatch):
 from fastapi import Request,Response,HTTPException
 from aiuditor_service import access_dream as route
 from aiuditor_service.role import AiuditorRole
 f,b=fixture();monkeypatch.setattr(route,'role_from_request',lambda _:AiuditorRole.WITNESS)
 with pytest.raises(HTTPException) as error:await route.dreaming(b,Request({'type':'http','headers':[]}),Response())
 assert error.value.status_code==403

def test_read_cannot_smuggle_raw_arguments_into_history():
 f,b=fixture();raw=b.model_dump(mode='json');raw['records'][0]['action']['arguments_json']='{"secret":"private"}'
 b=Dream.model_validate(raw)
 with pytest.raises(ValueError):review(b,f.fact,Policy.model_validate(f.policy_data),100)
