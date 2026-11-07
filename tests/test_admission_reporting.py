"""Assigned upload runs: authenticated evidence, immutable replay, human hold."""
import hashlib
import hmac
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from aiuditor_service.admission import submit_admission, resolve_admission_async
from aiuditor_service.admission_catalog import admission_catalog_id
from aiuditor_service.admission_reporting import Assignment, Report, PURPOSE, persist_report
from aiuditor_service.domain.models import AdmissionCreate, AdmissionResolve, AdmissionStatus
from aiuditor_service.hypermesh import FakePlaneHop
from aiuditor_service.store import AdmissionStore
from conftest import tenant_session_headers

KEY = 'sandbox-measurement-authority-' + 'x' * 40


def assigned(store, tenant):
    row = submit_admission(store, tenant, AdmissionCreate(file_hash='sha256:'+'a'*64,
        format='gguf', license='mit', intended_class='agx-large'), run_pipeline=False)
    assignment = Assignment(tenant_id=tenant, admission_id=row.id, job_id=uuid.uuid4(),
        device_id=uuid.uuid4(), class_id=row.intended_class, file_hash=row.file_hash,
        catalog_id=admission_catalog_id(intended_class=row.intended_class,file_hash=row.file_hash),
        binding_sha256='b'*64, created_at=datetime.now(UTC)-timedelta(seconds=1))
    row = row.model_copy(update={'lab_assignment':assignment.model_dump(mode='json'),
        'status':AdmissionStatus.SANDBOXING})
    store.put(row)
    now = datetime.now(UTC)
    return row, Report(**assignment.model_dump(), soak_passed=True, egress_ok=True,
        egress_reason='overlay_publish_only', measured_at=now, issued_at=now)


def signed(report, key=KEY, purpose=PURPOSE):
    payload = report.model_dump(mode='json') if isinstance(report, Report) else report
    raw=json.dumps(payload,sort_keys=True,separators=(',',':')).encode()
    return {'content':raw,'headers':{'Content-Type':'application/json',
        'X-Tenant-ID':payload['tenant_id'],
        'X-Lab-Signature':hmac.new(key.encode(),purpose+raw,hashlib.sha256).hexdigest()}}


@pytest.fixture(autouse=True)
def dedicated_key(monkeypatch):
    monkeypatch.setenv('AIUDITOR_LAB_REPORT_KEY',KEY)


@pytest.mark.parametrize('kind',['tenant','service','ir','internal','none'])
async def test_other_authority_refused(http_client,app,tenant_a,kind):
    row,report=assigned(app.state.admission_store,tenant_a)
    headers={'X-Tenant-ID':str(tenant_a)}
    if kind=='tenant': headers.update(tenant_session_headers(tenant_a))
    if kind in {'service','ir'}:
        from controlplane_identity.security.api_key_exchange_jwt import mint_api_key_session_jwt
        headers['Authorization']='Bearer '+mint_api_key_session_jwt(key_record_id=uuid.uuid4(),tenant_id=tenant_a,scopes=[],purpose=kind)
    if kind=='internal':headers['X-Internal-Token']='generic-token'
    response=await http_client.post(f'/api/v1/aiuditor/admissions/{row.id}/sandbox',
        json=report.model_dump(mode='json'),headers=headers)
    assert response.status_code==401
    assert app.state.admission_store.get(tenant_a,row.id)==row


@pytest.mark.parametrize('field',['job_id','device_id','admission_id','tenant_id','file_hash','binding_sha256','class_id','catalog_id'])
async def test_wrong_assignment_refused(http_client,app,tenant_a,field):
    row,report=assigned(app.state.admission_store,tenant_a)
    value=report.model_dump(mode='json')
    value[field]=str(uuid.uuid4()) if field.endswith('_id') and field not in {'class_id','catalog_id'} else ('sha256:'+'c'*64 if field=='file_hash' else 'c'*64)
    response=await http_client.post(f'/api/v1/aiuditor/admissions/{row.id}/sandbox',**signed(value))
    assert response.status_code in {404,409}
    assert app.state.admission_store.get(tenant_a,row.id)==row


async def test_hold_replay_conflict_and_named_promotion(http_client,app,tenant_a):
    store=app.state.admission_store;row,report=assigned(store,tenant_a)
    path=f'/api/v1/aiuditor/admissions/{row.id}/sandbox'
    response=await http_client.post(path,**signed(report))
    assert response.status_code==200,response.text
    assert response.json()['status']=='hold' and response.json()['verdict'] is None
    again=await http_client.post(path,**signed(report.model_copy(update={'issued_at':datetime.now(UTC)})))
    assert again.json()==response.json()
    changed=await http_client.post(path,**signed(report.model_copy(update={'egress_ok':False})))
    assert changed.status_code==409
    plane=FakePlaneHop()
    allowed=await resolve_admission_async(store,tenant_a,row.id,
        AdmissionResolve(verdict='allow'),resolved_by='named-human',plane=plane)
    assert allowed.verdict.value=='allow' and len(plane.promotions)==1
    assert plane.promotions[0]['certified'] is False


@pytest.mark.parametrize('change',[
    {'issued_at':datetime.now(UTC)-timedelta(minutes=6)},
    {'measured_at':datetime.now(UTC)-timedelta(days=2)},
    {'measured_at':datetime.now(UTC)+timedelta(minutes=1)}])
async def test_stale_or_future_report_refused(http_client,app,tenant_a,change):
    row,report=assigned(app.state.admission_store,tenant_a)
    response=await http_client.post(f'/api/v1/aiuditor/admissions/{row.id}/sandbox',**signed(report.model_copy(update=change)))
    assert response.status_code in {401,409}


@pytest.mark.parametrize('purpose',[b'aiuditor.lab-envelope.v1\n',b''])
async def test_cross_purpose_signature_refused(http_client,app,tenant_a,purpose):
    row,report=assigned(app.state.admission_store,tenant_a)
    response=await http_client.post(f'/api/v1/aiuditor/admissions/{row.id}/sandbox',**signed(report,purpose=purpose))
    assert response.status_code==401


@pytest.mark.parametrize('egress',[None,False])
def test_missing_egress_denies_and_receipt_survives_restart(egress):
    store=AdmissionStore();row,report=assigned(store,uuid.uuid4())
    report=report.model_copy(update={'egress_ok':egress})
    accepted=persist_report(store.persist,report,datetime.now(UTC))
    assert accepted.verdict.value=='deny'
    restarted=AdmissionStore(persist=store.persist)
    assert restarted.get(row.tenant_id,row.id)==accepted
    assert persist_report(restarted.persist,report,datetime.now(UTC)+timedelta(days=2))==accepted


def test_concurrent_conflicting_reports_choose_one_receipt():
    store=AdmissionStore();row,report=assigned(store,uuid.uuid4())
    def deliver(value):
        try:return persist_report(store.persist,report.model_copy(update={'egress_ok':value}),datetime.now(UTC))
        except HTTPException as error:return error.status_code
    with ThreadPoolExecutor(2) as pool: results=list(pool.map(deliver,[True,False]))
    assert sum(result==409 for result in results)==1
    assert store.get(row.tenant_id,row.id).sandbox_report_sha256 is not None
