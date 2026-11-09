"""Owner-migrated PostgreSQL receipts under a non-bypass RLS role."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from aiuditor_service.lab_reporting import LabEnvelopeReport
from aiuditor_service.persist import SqlPersistSession
from aiuditor_service.store import EnvelopeStore
from test_lab_reporting import KEY, report, signed

pytest_plugins = ['machine_postgres']
pytestmark = pytest.mark.integration


@pytest.fixture
def reporter_db(migrated_postgres):
    admin=create_engine(migrated_postgres); role='lab_report_'+uuid.uuid4().hex
    tid,other=uuid.uuid4(),uuid.uuid4()
    with admin.begin() as c:
        for id in [tid,other]:
            c.execute(text('INSERT INTO tenants(id,name,is_active) VALUES(:id,:name,true)'),{'id':id,'name':str(id)})
        c.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        c.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        c.exec_driver_sql(f'GRANT SELECT,INSERT,UPDATE ON aiuditor_envelopes,aiuditor_envelope_reports,service_outbox,service_tenants TO "{role}"')
    engine=create_engine(migrated_postgres)
    @event.listens_for(engine,'connect')
    def use_role(conn,_):
        with conn.cursor() as cur:cur.execute(f'SET ROLE "{role}"')
        conn.commit()
    yield admin,engine,tid,other
    engine.dispose()
    with admin.begin() as c:
        c.exec_driver_sql(f'DROP OWNED BY "{role}"');c.exec_driver_sql(f'DROP ROLE "{role}"')
    admin.dispose()


def test_concurrent_receipt_restart_and_rls(reporter_db):
    admin,engine,tid,other=reporter_db; body=LabEnvelopeReport.model_validate(report(tid))
    barrier=Barrier(4)
    def deliver(_):
        p=SqlPersistSession(sessionmaker(engine));barrier.wait()
        return p.accept_lab_report(body,datetime.now(UTC))
    with ThreadPoolExecutor(4) as pool:rows=list(pool.map(deliver,range(4)))
    assert sum(fresh for _,fresh in rows)==1
    assert all(row==rows[0][0] for row,_ in rows)
    engine.dispose()
    p=SqlPersistSession(sessionmaker(engine))
    old,fresh=p.accept_lab_report(body,datetime.now(UTC)+timedelta(days=3))
    assert not fresh and old==rows[0][0]
    with engine.begin() as c:
        c.execute(text("SELECT set_config('app.current_tenant_id',:tid,true)"),{'tid':str(other)})
        assert c.scalar(text('SELECT count(*) FROM aiuditor_envelope_reports'))==0
        assert c.scalar(text('SELECT count(*) FROM aiuditor_envelopes'))==0
    assert EnvelopeStore(persist=p).get(other,old.id) is None
    changed=body.model_copy(update={'device_id':uuid.uuid4()})
    with pytest.raises(HTTPException) as error:p.accept_lab_report(changed,datetime.now(UTC))
    assert error.value.status_code==409


def test_competing_replacements_only_one_current_revision(reporter_db):
    _,engine,tid,_=reporter_db;p=SqlPersistSession(sessionmaker(engine))
    first,_=p.accept_lab_report(LabEnvelopeReport.model_validate(report(tid)),datetime.now(UTC))
    reports=[LabEnvelopeReport.model_validate(report(tid,mode='replace',expected_revision=first.revision,
        measured_at=(datetime.now(UTC)+timedelta(seconds=i+1)).isoformat())) for i in range(2)]
    barrier=Barrier(2)
    def replace(body):
        barrier.wait()
        try:
            return SqlPersistSession(sessionmaker(engine)).accept_lab_report(body,datetime.now(UTC))[1]
        except HTTPException as e:assert e.status_code==412;return False
    with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(replace,reports))==[False,True]


async def test_purpose_route_owns_real_tenant_transaction(reporter_db,monkeypatch):
    from aiuditor_service.interfaces.api import routes
    from aiuditor_service import middleware
    from aiuditor_service.role import AiuditorRole
    _,engine,tid,_=reporter_db
    monkeypatch.setenv('AIUDITOR_LAB_REPORT_KEY',KEY)
    def forbidden():raise AssertionError('Lab authority fell back to generic credential middleware')
    monkeypatch.setattr(middleware,'get_session_maker',forbidden)
    events=[]
    class Publisher:
        async def publish(self,**kwargs):events.append(kwargs)
    monkeypatch.setattr(routes,'get_tenant_event_publisher',lambda:Publisher())
    app=FastAPI();app.state.aiuditor_role=AiuditorRole.GLOBAL
    app.state.envelope_store=EnvelopeStore(persist=SqlPersistSession(sessionmaker(engine)))
    app.add_middleware(middleware.AiuditorServiceDbMiddleware)
    app.include_router(routes.router,prefix='/api/v1')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://report.test') as client:
        value=report(tid)
        first=await client.post('/api/v1/aiuditor/envelopes',**signed(value))
        assert first.status_code==201,first.text
        replay=await client.post('/api/v1/aiuditor/envelopes',**signed(value))
        assert replay.status_code==201 and replay.json()==first.json()
    assert events==[]  # No separately committed publication after the HTTP write.
    with engine.begin() as db:
        db.execute(text("SELECT set_config('app.current_tenant_id',:tid,true)"),{'tid':str(tid)})
        durable=db.execute(text('SELECT id,envelope FROM service_outbox WHERE tenant_id=:tid'),{'tid':tid}).all()
        assert len(durable)==1
        assert durable[0].envelope['payload']=={key:first.json()[key] for key in durable[0].envelope['payload']}
        assert durable[0].envelope['correlation_id']==str(durable[0].id)


def test_admission_report_atomic_restart_and_rls(reporter_db):
    from aiuditor_service.admission_reporting import persist_report
    from aiuditor_service.store import AdmissionStore
    from test_admission_reporting import assigned
    from fastapi import HTTPException
    admin, engine, tid, other = reporter_db
    with engine.connect() as connection:
        role = connection.scalar(text('SELECT current_user'))
    with admin.begin() as c:
        c.exec_driver_sql(f'GRANT SELECT,INSERT,UPDATE ON aiuditor_admissions TO "{role}"')
    persist = SqlPersistSession(sessionmaker(engine))
    row, body = assigned(AdmissionStore(persist=persist), tid)
    barrier = Barrier(3)
    def deliver(_):
        barrier.wait()
        return persist_report(SqlPersistSession(sessionmaker(engine)), body, datetime.now(UTC))
    with ThreadPoolExecutor(3) as pool:
        results = list(pool.map(deliver, range(3)))
    assert all(result == results[0] for result in results)
    assert results[0].status.value == 'hold' and results[0].verdict is None
    engine.dispose()
    assert persist_report(SqlPersistSession(sessionmaker(engine)), body,
        datetime.now(UTC)+timedelta(days=2)) == results[0]
    with pytest.raises(HTTPException) as error:
        persist_report(persist, body.model_copy(update={'egress_ok':False}), datetime.now(UTC))
    assert error.value.status_code == 409
    with engine.begin() as c:
        c.execute(text("SELECT set_config('app.current_tenant_id',:tid,true)"), {'tid':str(other)})
        assert c.scalar(text('SELECT count(*) FROM aiuditor_admissions')) == 0


def test_human_denial_overlapping_report_preserves_receipt(reporter_db, monkeypatch):
    from threading import Event
    import aiuditor_service.admission_reporting as reporting
    from aiuditor_service.admission import resolve_admission
    from aiuditor_service.domain.models import AdmissionResolve
    from aiuditor_service.store import AdmissionStore
    from test_admission_reporting import assigned
    admin, engine, tid, _ = reporter_db
    with engine.connect() as connection:
        role = connection.scalar(text('SELECT current_user'))
    with admin.begin() as c:
        c.exec_driver_sql(f'GRANT SELECT,INSERT,UPDATE ON aiuditor_admissions TO "{role}"')
    persist = SqlPersistSession(sessionmaker(engine))
    row, body = assigned(AdmissionStore(persist=persist), tid)
    locked, release = Event(), Event()
    apply = reporting._apply
    def paused(old, report, now):
        locked.set()
        assert release.wait(10)
        return apply(old, report, now)
    monkeypatch.setattr(reporting, '_apply', paused)
    with ThreadPoolExecutor(2) as pool:
        evidence = pool.submit(reporting.persist_report, persist, body, datetime.now(UTC))
        assert locked.wait(10)
        verdict = pool.submit(resolve_admission, AdmissionStore(persist=persist), tid, row.id,
            AdmissionResolve(verdict='deny'), resolved_by='named-person')
        release.set()
        accepted, denied = evidence.result(timeout=15), verdict.result(timeout=15)
    assert denied.verdict.value == 'deny'
    assert denied.sandbox_report_sha256 == accepted.sandbox_report_sha256
    assert denied.sandbox_soak_ok is True and denied.sandbox_egress_ok is True
    assert reporting.persist_report(persist, body, datetime.now(UTC)) == denied


def test_lab_outbox_failure_rolls_back_receipt_and_envelope(reporter_db, monkeypatch):
    import aiuditor_service.lab_reporting as reporting
    admin,engine,tid,_=reporter_db
    body=LabEnvelopeReport.model_validate(report(tid));persist=SqlPersistSession(sessionmaker(engine))
    original=reporting.enqueue_event
    def fail(*args,**kwargs):raise RuntimeError('isolated outbox failure')
    monkeypatch.setattr(reporting,'enqueue_event',fail)
    with pytest.raises(RuntimeError,match='isolated outbox failure'):
        persist.accept_lab_report(body,datetime.now(UTC))
    with admin.connect() as db:
        for table in ('aiuditor_envelopes','aiuditor_envelope_reports','service_outbox'):
            assert db.scalar(text(f'SELECT count(*) FROM {table} WHERE tenant_id=:tid'),{'tid':tid})==0
    monkeypatch.setattr(reporting,'enqueue_event',original)
    row,fresh=persist.accept_lab_report(body,datetime.now(UTC));assert fresh
    with admin.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM service_outbox WHERE tenant_id=:tid'),{'tid':tid})==1


def test_missing_event_recovered_once_after_restart(reporter_db, monkeypatch):
    import aiuditor_service.lab_reporting as reporting
    admin,engine,tid,_=reporter_db
    body=LabEnvelopeReport.model_validate(report(tid));persist=SqlPersistSession(sessionmaker(engine))
    original=reporting.enqueue_event
    with monkeypatch.context() as patch:
        # Isolated interrupted TEST write: receipt/envelope committed before publish.
        patch.setattr(reporting,'enqueue_event',lambda *args,**kwargs:None)
        row,_=persist.accept_lab_report(body,datetime.now(UTC))
    engine.dispose()
    for _ in range(2):
        replay,fresh=SqlPersistSession(sessionmaker(engine)).accept_lab_report(body,datetime.now(UTC)+timedelta(days=2))
        assert not fresh and replay==row
    with admin.connect() as db:
        records=db.execute(text('SELECT id,envelope FROM service_outbox WHERE tenant_id=:tid'),{'tid':tid}).all()
        assert records==[(reporting.event_for(row)[0],reporting.event_for(row)[1].model_dump(mode='json'))]
        assert db.scalar(text('SELECT count(*) FROM aiuditor_envelope_reports WHERE tenant_id=:tid'),{'tid':tid})==1


@pytest.mark.parametrize('copies',[1,2])
def test_stored_uncorrelated_event_is_preserved_or_ambiguity_refused(reporter_db,monkeypatch,copies):
    import aiuditor_service.lab_reporting as reporting
    from controlplane_events.outbox import enqueue
    admin,engine,tid,_=reporter_db
    body=LabEnvelopeReport.model_validate(report(tid));persist=SqlPersistSession(sessionmaker(engine))
    with monkeypatch.context() as patch:
        patch.setattr(reporting,'enqueue_event',lambda *args,**kwargs:None)
        row,_=persist.accept_lab_report(body,datetime.now(UTC))
    envelope=reporting.event_for(row)[1].model_copy(update={'correlation_id':None})
    with engine.begin() as db:
        db.execute(text("SELECT set_config('app.current_tenant_id',:tid,true)"),{'tid':str(tid)})
        for _ in range(copies):enqueue(db,envelope,event_id=uuid.uuid4())
    if copies==1:
        replay,fresh=persist.accept_lab_report(body,datetime.now(UTC))
        assert not fresh and replay==row
    else:
        with pytest.raises(HTTPException) as error:persist.accept_lab_report(body,datetime.now(UTC))
        assert error.value.status_code==409
    with admin.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM service_outbox WHERE tenant_id=:tid'),{'tid':tid})==copies
