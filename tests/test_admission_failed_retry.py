"""A new measured attempt never overwrites an accepted failed report or human decision."""
import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from aiuditor_service.admission_reporting import persist_report, install_assignment
from aiuditor_service.admission_retry import begin_failed_retry, canonical
from aiuditor_service.domain.models import AdmissionFailedRetry, AdmissionStatus, AdmissionVerdict
from aiuditor_service.store import AdmissionStore
from test_admission_reporting import assigned, signed, KEY
from conftest import tenant_session_headers

ACTOR='00000000-0000-4000-8000-000000000001'


def denied(store=None, tenant=None):
    store=store or AdmissionStore();tenant=tenant or uuid.uuid4()
    original,report=assigned(store,tenant)
    report=report.model_copy(update={'soak_passed':False})
    row=persist_report(store.persist,report,datetime.now(UTC))
    body=AdmissionFailedRetry(request_id=uuid.uuid4(),expected_job_id=report.job_id,
        expected_report_sha256=row.sandbox_report_sha256,source_ref='owner/model@'+'a'*40+'/model.gguf')
    return store,row,report,body


def test_complete_failed_attempt_is_archived_before_an_idempotent_retry():
    store,row,report,body=denied();original=row.model_dump(mode='json',exclude={'lab_attempt_history'})
    retried=begin_failed_retry(store.persist,row.tenant_id,row.id,body,ACTOR)
    assert retried.id==row.id and retried.file_hash==row.file_hash
    assert retried.lab_assignment is None and retried.sandbox_report_sha256 is None
    assert retried.status is AdmissionStatus.QUARANTINED and retried.verdict is None
    assert retried.resolved_by is None and retried.resolved_at is None
    assert retried.sandbox_soak_ok is None and retried.sandbox_egress_ok is None
    assert len(retried.lab_attempt_history)==1
    entry=retried.lab_attempt_history[0]
    assert entry['snapshot']==original
    assert entry['snapshot_sha256']==hashlib.sha256(canonical(original)).hexdigest()
    assert entry['requested_by']==ACTOR
    assert begin_failed_retry(store.persist,row.tenant_id,row.id,body,ACTOR)==retried
    assert AdmissionStore(persist=store.persist).get(row.tenant_id,row.id)==retried


def test_old_report_replay_cannot_fill_or_replace_the_new_attempt():
    store,row,report,body=denied();retried=begin_failed_retry(store.persist,row.tenant_id,row.id,body,ACTOR)
    assert persist_report(store.persist,report,datetime.now(UTC))==retried
    with pytest.raises(HTTPException) as failure:
        persist_report(store.persist,report.model_copy(update={'soak_passed':True}),datetime.now(UTC))
    assert failure.value.status_code==409
    new_assignment=dict(row.lab_assignment,job_id=str(uuid.uuid4()),binding_sha256='c'*64)
    installed=install_assignment(store.persist,row.tenant_id,row.id,new_assignment)
    assert installed.lab_assignment['job_id']!=row.lab_assignment['job_id']
    assert persist_report(store.persist,report,datetime.now(UTC))==installed
    now=datetime.now(UTC)
    fresh=report.model_copy(update={'job_id':uuid.UUID(new_assignment['job_id']),
        'binding_sha256':'c'*64,'soak_passed':True,'measured_at':now,'issued_at':now})
    accepted=persist_report(store.persist,fresh,now)
    assert accepted.status is AdmissionStatus.HOLD and accepted.verdict is None
    assert accepted.lab_attempt_history==retried.lab_attempt_history
    assert persist_report(store.persist,report,now)==accepted
    assert store.get(row.tenant_id,row.id)==accepted


@pytest.mark.parametrize('fault',['human-deny','human-allow','passing-report','unassigned','wrong-job','wrong-report','static-deny'])
def test_retry_cannot_clear_an_unrelated_or_human_decision(fault):
    store,row,report,body=denied()
    if fault=='human-deny':row=row.model_copy(update={'resolved_by':ACTOR})
    elif fault=='human-allow':row=row.model_copy(update={'resolved_by':ACTOR,'verdict':AdmissionVerdict.ALLOW,'status':AdmissionStatus.ALLOWED})
    elif fault=='passing-report':row=row.model_copy(update={'verdict':None,'resolved_by':None,'status':AdmissionStatus.HOLD,'sandbox_soak_ok':True})
    elif fault=='unassigned':row=row.model_copy(update={'lab_assignment':None})
    elif fault=='wrong-job':body=body.model_copy(update={'expected_job_id':uuid.uuid4()})
    elif fault=='wrong-report':body=body.model_copy(update={'expected_report_sha256':'f'*64})
    elif fault=='static-deny':row=row.model_copy(update={'resolved_by':'static','sandbox_report_sha256':None})
    store.put(row)
    with pytest.raises((HTTPException,ValueError)):
        begin_failed_retry(store.persist,row.tenant_id,row.id,body,ACTOR)
    assert store.get(row.tenant_id,row.id)==row


@pytest.mark.parametrize('same_request',[True,False])
def test_competing_retry_requests_archive_exactly_one_complete_attempt(same_request):
    store,row,report,body=denied()
    def retry(index):
        request=body if same_request else body.model_copy(update={'request_id':uuid.uuid4()})
        try:return begin_failed_retry(store.persist,row.tenant_id,row.id,request,ACTOR)
        except HTTPException as e:return e.status_code
    with ThreadPoolExecutor(8) as pool:results=list(pool.map(retry,range(16)))
    if same_request:assert all(result==results[0] for result in results)
    else:assert results.count(409)==15
    assert len(store.get(row.tenant_id,row.id).lab_attempt_history)==1


@pytest.mark.parametrize('field',['source_ref','expected_report_sha256','expected_job_id'])
def test_retry_request_id_cannot_be_rebound(field):
    store,row,report,body=denied();retried=begin_failed_retry(store.persist,row.tenant_id,row.id,body,ACTOR)
    changed=body.model_copy(update={field:uuid.uuid4() if field.endswith('_id') else ('f'*64 if field.endswith('sha256') else 'other/ref')})
    with pytest.raises(HTTPException) as failure:begin_failed_retry(store.persist,row.tenant_id,row.id,changed,ACTOR)
    assert failure.value.status_code==409 and store.get(row.tenant_id,row.id)==retried


async def test_normal_person_retry_queues_new_work_once_and_preserves_original_receipt(http_client,app,tenant_a,plane_hop,monkeypatch):
    monkeypatch.setenv("AIUDITOR_LAB_REPORT_KEY",KEY)
    store,row,report,body=denied(app.state.admission_store,tenant_a)
    path=f'/api/v1/aiuditor/admissions/{row.id}/retry'
    first=await http_client.post(path,headers=tenant_session_headers(tenant_a),json=body.model_dump(mode='json'))
    assert first.status_code==200,first.text
    assert first.json()['lab_assignment']['job_id']!=str(report.job_id)
    assert first.json()['lab_attempt_history'][0]['snapshot']['sandbox_report_sha256']==report.evidence_hash()
    repeat=await http_client.post(path,headers=tenant_session_headers(tenant_a),json=body.model_dump(mode='json'))
    assert repeat.status_code==200 and repeat.json()==first.json() and len(plane_hop.soaks)==1
    late=await http_client.post(f'/api/v1/aiuditor/admissions/{row.id}/sandbox',**signed(report))
    assert late.status_code==200 and late.json()==first.json()


@pytest.mark.parametrize('kind',['none','internal','service','ir'])
async def test_generic_or_machine_authority_cannot_retry_a_failed_lab(http_client,app,tenant_a,kind):
    store,row,report,body=denied(app.state.admission_store,tenant_a)
    headers={'X-Tenant-ID':str(tenant_a)}
    if kind=='internal':headers['X-Internal-Token']='generic-token'
    if kind in {'service','ir'}:
        from controlplane_identity.security.api_key_exchange_jwt import mint_api_key_session_jwt
        headers['Authorization']='Bearer '+mint_api_key_session_jwt(key_record_id=uuid.uuid4(),tenant_id=tenant_a,scopes=[],purpose=kind)
    response=await http_client.post(f'/api/v1/aiuditor/admissions/{row.id}/retry',headers=headers,json=body.model_dump(mode='json'))
    assert response.status_code in {401,403}
    assert store.get(tenant_a,row.id)==row
