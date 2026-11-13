import hashlib
import hmac
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi import HTTPException
from aiuditor_service.accounting import Committed, apply_commit, settlement_overdue, HttpAccounting
from aiuditor_service.accounting_contracts import Capabilities, SettlementStatus
from aiuditor_service.contracts import put_contract, sign_contract
from aiuditor_service.domain.models import ContractCreate, ContractStatus
from aiuditor_service.store import ContractStore

KEY = 'callback-signing-key-' + 'a'*32


def event(tenant, **changes):
    body = dict(type='accounting.committed.v1', id=str(uuid.uuid4()), tenant_id=str(tenant),
        accounting_backend='internal', accounting_ref='opaque:' + uuid.uuid4().hex,
        contract=dict(lease_id=str(uuid.uuid4()), renter_subject='renter',host_subject='host',reserved_hours=2))
    body.update(changes)
    return Committed.model_validate(body)


def signed(event, *, stamp=None, key=KEY):
    raw = event.model_dump_json().encode()
    stamp = str(int(time.time()) if stamp is None else stamp)
    digest = hmac.new(key.encode(), b'aiuditor/accounting.committed.v1\n' + stamp.encode() + b'\n' + raw, hashlib.sha256).hexdigest()
    return raw, {'Content-Type':'application/json', 'X-Accounting-Timestamp':stamp,
                 'X-Accounting-Signature':'v1='+digest, 'X-Tenant-ID':str(event.tenant_id)}


def setup_callback(monkeypatch, tenant):
    monkeypatch.setenv('AIUDITOR_ACCOUNTING_CALLBACK_KEY', KEY)
    monkeypatch.setenv('AIUDITOR_ACCOUNTING_TENANT_ID', str(tenant))
    monkeypatch.setenv('ACCOUNTING_BACKEND', 'internal')


async def test_signed_callback_binds_neutral_contract_and_replays(http_client, tenant_a, monkeypatch):
    setup_callback(monkeypatch, tenant_a)
    body = event(tenant_a)
    raw, headers = signed(body)
    path = '/api/v1/aiuditor/contracts/accounting/committed'
    first = await http_client.post(path, content=raw, headers=headers)
    assert first.status_code == 201, first.text
    result = first.json()
    assert result['accounting_backend']=='internal' and result['accounting_ref']==body.accounting_ref
    assert result['lease_id']==str(body.contract.lease_id) and 'rail' not in result
    assert (await http_client.post(path, content=raw, headers=headers)).json()==result
    # Existing path is a signed-callback alias; it no longer accepts a static token.
    alias = await http_client.post('/api/v1/aiuditor/contracts/paid/fulfill', content=raw, headers=headers)
    assert alias.status_code == 201 and alias.json()['id'] == result['id']


@pytest.mark.parametrize('failure', ['signature','timestamp','tenant','backend','changed_body','unicode'])
async def test_callback_rejects_wrong_authority(http_client, tenant_a, monkeypatch, failure):
    setup_callback(monkeypatch, tenant_a)
    body=event(tenant_a)
    if failure=='tenant': body=event(uuid.uuid4())
    if failure=='backend': body=event(tenant_a, accounting_backend='http')
    raw, headers=signed(body, stamp=int(time.time())-600 if failure=='timestamp' else None)
    if failure=='signature': headers['X-Accounting-Signature']='v1='+'0'*64
    if failure=='unicode': headers['X-Accounting-Signature']='malformed'
    if failure=='changed_body': raw=raw.replace(b'renter',b'changed')
    response=await http_client.post('/api/v1/aiuditor/contracts/accounting/committed',content=raw,headers=headers)
    assert response.status_code in {401,403}


@pytest.mark.parametrize('changed', ['lease','terms','ref','event'])
def test_committed_reference_cannot_be_reused_or_rewritten(changed):
    store, tenant=ContractStore(),uuid.uuid4()
    original=event(tenant)
    row=apply_commit(store,original)
    payload=original.model_dump(mode='json')
    if changed=='lease': payload['contract']['lease_id']=str(uuid.uuid4())
    if changed=='terms': payload['contract']['reserved_hours']=3
    if changed=='ref': payload['accounting_ref']='other'
    if changed=='event': payload['id']=str(uuid.uuid4())
    with pytest.raises(HTTPException) as error: apply_commit(store,Committed.model_validate(payload))
    assert error.value.status_code==409
    assert len(store.list_for(tenant))==1 and store.get(tenant,row.id)==row


def test_replay_after_restart_does_not_reactivate_suspended_contract():
    store, tenant=ContractStore(),uuid.uuid4()
    committed=event(tenant)
    row=apply_commit(store,committed)
    store.put(row.model_copy(update={'status':ContractStatus.SUSPENDED}))
    restarted=ContractStore(persist=store.persist)
    assert apply_commit(restarted,committed).status==ContractStatus.SUSPENDED


def test_existing_contract_backend_is_not_changed_by_environment(monkeypatch):
    store, tenant=ContractStore(),uuid.uuid4()
    row=apply_commit(store,event(tenant))
    monkeypatch.setenv('ACCOUNTING_BACKEND','http')
    assert store.get(tenant,row.id).accounting_backend=='internal'


async def test_internal_backend_skips_settlement_and_paused_host_is_not_unpaid():
    from aiuditor_service.violations import evaluate_kind
    from aiuditor_service.hypermesh import HostView, LeaseView
    row=apply_commit(ContractStore(),event(uuid.uuid4()))
    assert await settlement_overdue(row) is False
    host=HostView(id=uuid.uuid4(),enrolled=True,role='host',path_b_state='green',sell_state='paused',schedule_hold=True)
    lease=LeaseView(id=row.lease_id,device_id=host.id,purpose='renter',status='active')
    assert evaluate_kind(row,host=host,lease=lease) is None


async def test_settlement_requires_capability_and_exact_scope():
    row=apply_commit(ContractStore(),event(uuid.uuid4()))
    backend=AsyncMock()
    backend.capabilities.return_value=Capabilities(supports_settlement=False)
    assert await settlement_overdue(row,backend) is False
    backend.settlement_status.assert_not_awaited()
    backend.capabilities.return_value=Capabilities(supports_settlement=True)
    backend.settlement_status.return_value=SettlementStatus(tenant_id=row.tenant_id,accounting_ref=row.accounting_ref,overdue=True)
    assert await settlement_overdue(row,backend) is True
    backend.settlement_status.return_value=SettlementStatus(tenant_id=uuid.uuid4(),accounting_ref=row.accounting_ref,overdue=True)
    with pytest.raises(HTTPException): await settlement_overdue(row,backend)


@pytest.mark.parametrize('response', [httpx.Response(503),httpx.Response(302,headers={'location':'https://other.invalid'}),httpx.Response(200,json={}),httpx.Response(200,json={'supports_settlement':'false'})])
async def test_http_backend_failure_never_means_no_settlement(response):
    backend=HttpAccounting('https://accounting.example','t'*32,transport=httpx.MockTransport(lambda request:response))
    with pytest.raises(HTTPException) as error: await backend.capabilities()
    assert error.value.status_code==503


async def test_http_backend_reads_scoped_settlement_without_payment_fields():
    tenant, reference=uuid.uuid4(),'opaque-reference'
    def handler(request):
        assert request.headers['authorization']=='Bearer '+'t'*32
        if request.url.path=='/capabilities': return httpx.Response(200,json={'supports_settlement':True})
        assert request.url.params['tenant_id']==str(tenant) and request.url.params['accounting_ref']==reference
        return httpx.Response(200,json={'tenant_id':str(tenant),'accounting_ref':reference,'overdue':False})
    backend=HttpAccounting('https://accounting.example','t'*32,transport=httpx.MockTransport(handler))
    assert (await backend.capabilities()).supports_settlement
    assert (await backend.settlement_status(tenant,reference)).overdue is False


async def test_neutral_allocation_view_keeps_payment_details_out(http_client, tenant_headers, tenant_a, host_lookup):
    from test_allocate import _lease_host, _catalog_p1
    from conftest import FYBER_AGX_DEVICE_ID
    host_lookup.enroll(FYBER_AGX_DEVICE_ID,tenant_id=tenant_a,class_id='agx-large')
    await _lease_host(http_client,tenant_headers,FYBER_AGX_DEVICE_ID)
    service=await _catalog_p1(http_client,tenant_headers)
    response=await http_client.post(f"/api/v1/aiuditor/catalog/{service['id']}/allocate/accounting",
        headers=tenant_headers,json={'renter':'renter'})
    assert response.status_code==201, response.text
    body=response.json()
    assert body['funding_state']=='pending' and body['accounting_ref']
    assert {'billing_record_id','payment_amount_atoms','payment_test_credit_available'}.isdisjoint(body)
    assert 'rail' not in body['contract']


def test_callback_can_commit_a_named_signed_contract_with_matching_reference():
    store, tenant=ContractStore(),uuid.uuid4()
    committed=event(tenant)
    row=put_contract(store,tenant,ContractCreate(accounting_ref=committed.accounting_ref,
        parties=[{'role':'renter','subject':'renter'},{'role':'host','subject':'host'}],
        meters=committed.contract.meters,until=datetime.now(UTC)+timedelta(hours=3)))
    row=sign_contract(store,tenant,row.id,signed_by='human')
    committed=committed.model_copy(update={'contract':committed.contract.model_copy(update={'contract_id':row.id})})
    applied=apply_commit(store,committed)
    assert applied.id==row.id and applied.signed_by=='human'
    assert applied.accounting_ref==committed.accounting_ref and applied.lease_id==committed.contract.lease_id


async def test_unknown_or_broken_config_never_falls_back_to_internal(monkeypatch):
    from aiuditor_service.accounting import selected_backend
    row=apply_commit(ContractStore(),event(uuid.uuid4())).model_copy(update={'accounting_backend':'http'})
    monkeypatch.setenv('ACCOUNTING_BACKEND','typo')
    with pytest.raises(HTTPException): selected_backend()
    monkeypatch.delenv('ACCOUNTING_URL',raising=False)
    with pytest.raises(HTTPException) as error: await settlement_overdue(row)
    assert error.value.status_code==503


async def test_callback_replay_after_suspension_keeps_one_fulfillment_event(http_client,tenant_a,app,monkeypatch):
    setup_callback(monkeypatch,tenant_a)
    committed=event(tenant_a)
    raw,headers=signed(committed)
    path='/api/v1/aiuditor/contracts/accounting/committed'
    first=await http_client.post(path,content=raw,headers=headers)
    assert first.status_code==201
    store=app.state.contract_store
    row=store.get(tenant_a,uuid.UUID(first.json()['id']))
    store.put(row.model_copy(update={'status':ContractStatus.SUSPENDED}))
    before=store.persist.snapshot()['service_outbox']
    replay=await http_client.post(path,content=raw,headers=headers)
    assert replay.status_code==201 and replay.json()['status']=='suspended'
    assert store.persist.snapshot()['service_outbox']==before
    events=list(before[str(tenant_a)].values())
    assert len(events)==1 and events[0]['payload']['status']=='signed'
