"""Signed owner reports, immutable receipts and CAS measurement replacement."""
import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from aiuditor_service.lab_reporting import LabEnvelopeReport, PURPOSE
from aiuditor_service.persist import MemoryPersistSession
from aiuditor_service.store import EnvelopeStore
from aiuditor_service.intake import needs_resoaking
from conftest import tenant_session_headers

KEY = "assigned-lab-report-key-" + "x" * 40


def report(tenant, **changes):
    now = datetime.now(UTC)
    value = {"protocol_version": 1, "tenant_id": str(tenant), "origin": "path_b",
        "job_id": str(uuid.uuid4()), "device_id": str(uuid.uuid4()), "binding_sha256": "b"*64,
        "measured_at": now.isoformat(), "issued_at": now.isoformat(),
        "mode": "create", "expected_revision": None,
        "envelope": {"class_id":"agx-large", "catalog_id":"test-model", "file_hash":"sha256:"+"a"*64,
            "runtime":"nvidia", "quant":"Q4", "context":None, "fit":True,
            "decode_tok_s":10, "thermal_c":50}}
    value.update(changes)
    return value


def signed(value, key=KEY):
    content = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return {"content": content, "headers": {"Content-Type":"application/json",
        "X-Tenant-ID":value["tenant_id"], "X-Lab-Signature":hmac.new(key.encode(), PURPOSE+content, hashlib.sha256).hexdigest()}}


@pytest.fixture(autouse=True)
def lab_key(monkeypatch):
    monkeypatch.setenv("AIUDITOR_LAB_REPORT_KEY",KEY)


@pytest.mark.parametrize("path",["/envelopes", "/envelopes/initial"])
@pytest.mark.parametrize("kind",["tenant","service","ir","internal","none"])
async def test_ordinary_authority_cannot_report(http_client, app, tenant_a, monkeypatch, path, kind):
    value=report(tenant_a);headers={"X-Tenant-ID":str(tenant_a)}
    if kind=="tenant":headers.update(tenant_session_headers(tenant_a))
    if kind in {"service","ir"}:
        from controlplane_identity.security.api_key_exchange_jwt import mint_api_key_session_jwt
        headers["Authorization"]="Bearer "+mint_api_key_session_jwt(key_record_id=uuid.uuid4(),tenant_id=tenant_a,scopes=[],purpose=kind)
    if kind=="internal":
        monkeypatch.setenv("AIUDITOR_INTERNAL_TOKEN","generic-internal")
        headers["X-Internal-Token"]="generic-internal"
    result=await http_client.post('/api/v1/aiuditor'+path,json=value,headers=headers)
    assert result.status_code==401
    assert app.state.envelope_store.list_for(tenant_a)==[]


@pytest.mark.parametrize("field",["tenant_id","job_id","device_id","binding_sha256","envelope"])
async def test_signed_binding_cannot_be_changed(http_client,tenant_a,field):
    value=report(tenant_a);original=signed(value)
    value[field] = {**value['envelope'],'file_hash':'sha256:'+'c'*64} if field=='envelope' else ('c'*64 if field=='binding_sha256' else str(uuid.uuid4()))
    result=await http_client.post('/api/v1/aiuditor/envelopes',json=value,headers=original['headers'])
    assert result.status_code==401


async def test_fresh_assigned_report_replay_and_cas(http_client,tenant_a,app):
    first=report(tenant_a)
    response=await http_client.post('/api/v1/aiuditor/envelopes/initial',**signed(first))
    assert response.status_code==201,response.text
    original=response.json(); assert original['measurement_verified'] is True
    assert original['updated_at']==original['measured_at']
    replay={**first,'issued_at':datetime.now(UTC).isoformat()}
    again=await http_client.post('/api/v1/aiuditor/envelopes/initial',**signed(replay))
    assert again.status_code==201 and again.json()==original
    assert again.headers['X-Lab-Report-Replay']=='true'
    changed={**replay,'envelope':{**first['envelope'],'decode_tok_s':99}}
    assert (await http_client.post('/api/v1/aiuditor/envelopes',**signed(changed))).status_code==409
    later=report(tenant_a,measured_at=(datetime.now(UTC)+timedelta(seconds=1)).isoformat(),mode='replace',expected_revision=original['revision'])
    request=signed(later);request['headers']['If-Match']='"'+original['revision']+'"'
    replaced=await http_client.post('/api/v1/aiuditor/envelopes',**request)
    assert replaced.status_code==201,replaced.text
    assert replaced.json()['id']==original['id']
    old_replay=await http_client.post('/api/v1/aiuditor/envelopes/initial',**signed(replay))
    assert old_replay.status_code==201 and old_replay.json()==original
    rows=(await http_client.get('/api/v1/aiuditor/envelopes',headers=tenant_session_headers(tenant_a))).json()
    assert rows[0]['report_sha256']==replaced.json()['report_sha256']
    stale=report(tenant_a,mode='replace',expected_revision=original['revision'])
    request=signed(stale);request['headers']['If-Match']='"'+original['revision']+'"'
    assert (await http_client.post('/api/v1/aiuditor/envelopes',**request)).status_code==412
    assert len(app.state.envelope_store.persist.rows["service_outbox"][str(tenant_a)])==2


@pytest.mark.parametrize("change",['stale','future','unauthorized_replace','tenant','missing_key','reused_key'])
async def test_fail_closed_inputs(http_client,tenant_a,monkeypatch,change):
    value=report(tenant_a)
    if change=='stale':value['measured_at']=(datetime.now(UTC)-timedelta(days=2)).isoformat()
    if change=='future':value['measured_at']=(datetime.now(UTC)+timedelta(hours=1)).isoformat()
    if change=='unauthorized_replace':value.update(mode='replace',expected_revision='0'*64)
    req=signed(value)
    if change=='tenant':req['headers']['X-Tenant-ID']=str(uuid.uuid4())
    if change=='missing_key':monkeypatch.delenv('AIUDITOR_LAB_REPORT_KEY')
    if change=='reused_key':monkeypatch.setenv('AIUDITOR_INTERNAL_TOKEN',KEY)
    expected={'stale':409,'future':409,'unauthorized_replace':412,'tenant':403,'missing_key':503,'reused_key':503}[change]
    assert (await http_client.post('/api/v1/aiuditor/envelopes',**req)).status_code==expected


def test_receipt_survives_store_restart_and_does_not_refresh_old_measurement():
    tid=uuid.uuid4();persist=MemoryPersistSession();r=LabEnvelopeReport.model_validate(report(tid))
    first,fresh=persist.accept_lab_report(r,datetime.now(UTC));assert fresh
    restarted=MemoryPersistSession(persist.snapshot())
    replay,fresh=restarted.accept_lab_report(r,datetime.now(UTC)+timedelta(days=4))
    assert not fresh and first==replay
    assert not needs_resoaking(first,file_hash=first.file_hash,quant=first.quant,runtime=first.runtime,now=datetime.now(UTC))
    legacy=first.model_copy(update={'lab_job_id':None})
    assert needs_resoaking(legacy,file_hash=legacy.file_hash,quant=legacy.quant,runtime=legacy.runtime,now=datetime.now(UTC))

@pytest.mark.parametrize('fault', ['expired-auth', 'naive-time', 'malformed-signature', 'zero-rate'])
async def test_invalid_authentication_and_measurements(http_client, tenant_a, fault):
    value = report(tenant_a)
    if fault == 'expired-auth': value['issued_at'] = (datetime.now(UTC)-timedelta(minutes=10)).isoformat()
    if fault == 'naive-time': value['measured_at'] = datetime.now(UTC).replace(tzinfo=None).isoformat()
    if fault == 'zero-rate': value['envelope']['decode_tok_s'] = 0
    req = signed(value)
    if fault == 'malformed-signature': req['headers']['X-Lab-Signature'] = 'invalid-signature'
    result = await http_client.post('/api/v1/aiuditor/envelopes', **req)
    assert result.status_code == (422 if fault in ('naive-time', 'zero-rate') else 401)

async def test_path_b_cannot_erase_qualified_context(http_client, tenant_a):
    first = report(tenant_a, origin='qualification')
    first['envelope']['context'] = 4096
    accepted = await http_client.post('/api/v1/aiuditor/envelopes', **signed(first))
    assert accepted.status_code == 201
    value = report(tenant_a, mode='replace', expected_revision=accepted.json()['revision'],
        measured_at=(datetime.now(UTC)+timedelta(seconds=1)).isoformat())
    req = signed(value); req['headers']['If-Match'] = '"'+accepted.json()['revision']+'"'
    refused = await http_client.post('/api/v1/aiuditor/envelopes', **req)
    assert refused.status_code == 409
