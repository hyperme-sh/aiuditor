"""Isolated owner memory transactions: durable lab event obligations, never live evidence."""
from datetime import UTC, datetime, timedelta
import uuid
import pytest
from fastapi import HTTPException
from aiuditor_service.lab_reporting import LabEnvelopeReport, event_for, persist_report
from aiuditor_service.persist import MemoryPersistSession
from test_lab_reporting import report


def test_replay_recovers_missing_event_without_rewriting_measurement(monkeypatch):
    tenant=uuid.uuid4();body=LabEnvelopeReport.model_validate(report(tenant));persist=MemoryPersistSession()
    first,fresh=persist_report(persist,body,datetime.now(UTC))
    assert fresh
    expected=persist.rows.pop('service_outbox')
    restarted=MemoryPersistSession(persist.rows)
    for _ in range(2):
        again,fresh=persist_report(restarted,body,datetime.now(UTC)+timedelta(days=3))
        assert not fresh and again==first
        assert restarted.rows['service_outbox']==expected
    changed=body.model_copy(update={'device_id':uuid.uuid4()})
    with pytest.raises(HTTPException) as error:persist_report(restarted,changed,datetime.now(UTC))
    assert error.value.status_code==409
    assert restarted.rows['service_outbox']==expected


def test_event_validation_failure_does_not_commit_envelope_or_receipt(monkeypatch):
    import aiuditor_service.lab_reporting as reporting
    tenant=uuid.uuid4();body=LabEnvelopeReport.model_validate(report(tenant));persist=MemoryPersistSession()
    def fail(row):raise ValueError('refused event')
    monkeypatch.setattr(reporting,'event_for',fail)
    with pytest.raises(ValueError,match='refused event'):persist_report(persist,body,datetime.now(UTC))
    assert not persist.rows.get('envelopes',{}).get(str(tenant))
    assert not persist.rows.get('envelope_reports',{}).get(str(tenant))
    assert not persist.rows.get('service_outbox')


def test_event_identity_is_receipt_bound_and_transport_time_independent():
    tenant=uuid.uuid4();body=LabEnvelopeReport.model_validate(report(tenant));persist=MemoryPersistSession()
    first,_=persist_report(persist,body,datetime.now(UTC))
    event_id,envelope=event_for(first)
    replay,_=persist_report(persist,body.model_copy(update={'issued_at':datetime.now(UTC)}),datetime.now(UTC))
    assert event_for(replay)==(event_id,envelope)
    assert envelope.tenant_id==tenant and envelope.occurred_at==body.measured_at
    assert envelope.payload==first.to_event_payload()
    assert event_for(first.model_copy(update={'lab_job_id':uuid.uuid4()}))[0]!=event_id


def test_conflicting_durable_event_is_not_silently_accepted():
    tenant=uuid.uuid4();body=LabEnvelopeReport.model_validate(report(tenant));persist=MemoryPersistSession()
    row,_=persist_report(persist,body,datetime.now(UTC));event_id,_=event_for(row)
    persist.rows['service_outbox'][str(tenant)][str(event_id)]['payload']['decode_tok_s']=999
    with pytest.raises(HTTPException) as error:persist_report(persist,body,datetime.now(UTC))
    assert error.value.status_code==409
    assert persist.rows['envelopes'][str(tenant)][str(row.id)]['decode_tok_s']==body.envelope.decode_tok_s
