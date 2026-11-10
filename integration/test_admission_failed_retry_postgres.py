"""Migrated admission retry history survives real owner locks, restart and tenant RLS."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from aiuditor_service.admission_reporting import persist_report
from aiuditor_service.admission_retry import begin_failed_retry
from aiuditor_service.persist import SqlPersistSession
from aiuditor_service.store import AdmissionStore
from test_admission_failed_retry import denied, ACTOR
from test_lab_reporting_postgres import reporter_db

pytest_plugins=['machine_postgres']
pytestmark=pytest.mark.integration


@pytest.mark.parametrize('same_request',[True,False])
def test_locked_retry_history_restart_old_report_and_rls(reporter_db,same_request):
    admin,engine,tenant,foreign=reporter_db
    with engine.connect() as c:role=c.scalar(text('SELECT current_user'))
    with admin.begin() as c:c.exec_driver_sql(f'GRANT SELECT,INSERT,UPDATE ON aiuditor_admissions TO "{role}"')
    writer=SqlPersistSession(sessionmaker(engine));store=AdmissionStore(persist=writer)
    _,original,report,body=denied(store,tenant)
    barrier=Barrier(4)
    def retry(index):
        request=body if same_request else body.model_copy(update={'request_id':uuid.uuid4()})
        barrier.wait()
        try:return begin_failed_retry(SqlPersistSession(sessionmaker(engine)),tenant,original.id,request,ACTOR)
        except HTTPException as e:return e.status_code
    with ThreadPoolExecutor(4) as pool:results=list(pool.map(retry,range(4)))
    if same_request:assert all(row==results[0] for row in results)
    else:assert results.count(409)==3
    expected=store.get(tenant,original.id)
    assert len(expected.lab_attempt_history)==1
    entry=expected.lab_attempt_history[0]
    assert entry['snapshot']==original.model_dump(mode='json',exclude={'lab_attempt_history'})
    engine.dispose()
    restarted=SqlPersistSession(sessionmaker(engine));recovered=AdmissionStore(persist=restarted)
    assert recovered.get(tenant,original.id)==expected
    assert persist_report(restarted,report,datetime.now(UTC))==expected
    with pytest.raises(HTTPException) as failure:
        persist_report(restarted,report.model_copy(update={'soak_passed':True}),datetime.now(UTC))
    assert failure.value.status_code==409
    assert recovered.get(tenant,original.id)==expected
    with pytest.raises(HTTPException) as failure:
        begin_failed_retry(restarted,foreign,original.id,body,ACTOR)
    assert failure.value.status_code==404
    with engine.begin() as c:
        c.execute(text("SELECT set_config('app.current_tenant_id',:tid,true)"),{'tid':str(foreign)})
        assert c.scalar(text('SELECT count(*) FROM aiuditor_admissions'))==0
