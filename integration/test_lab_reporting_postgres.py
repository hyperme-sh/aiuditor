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
        c.exec_driver_sql(f'GRANT SELECT,INSERT,UPDATE ON aiuditor_envelopes,aiuditor_envelope_reports TO "{role}"')
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
    assert len(events)==1
