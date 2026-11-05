"""Real PostgreSQL qualification using the existing migrated owner harness."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
import uuid

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import Headers
from aiuditor_service.approvals import consume_allow_once, hold_approval, resolve_approval, resolver_subject
from aiuditor_service.domain.models import ApprovalCreate, ApprovalDecision
from aiuditor_service.persist import SqlPersistSession
from aiuditor_service.store import ApprovalStore
from aiuditor_service.private_models import routes
from controlplane_db.models import Tenant, User, Role
from controlplane_identity.security.tenant_session_jwt import mint_tenant_session_jwt

pytest_plugins = ["machine_postgres"]
pytestmark = pytest.mark.integration

@pytest.fixture
def scoped_engine(migrated_postgres):
    admin = create_engine(migrated_postgres)
    role, tenant = "approval_test_" + uuid.uuid4().hex, uuid.uuid4()
    with admin.begin() as conn:
        conn.execute(text("INSERT INTO tenants (id,name,is_active) VALUES (:id,:name,true)"), {"id": tenant, "name": str(tenant)})
        conn.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        conn.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        conn.exec_driver_sql(f'GRANT SELECT,UPDATE ON aiuditor_approvals TO "{role}"')
    engine = create_engine(migrated_postgres)
    @event.listens_for(engine, "connect")
    def use_role(connection, _):
        with connection.cursor() as cursor:
            cursor.execute(f'SET ROLE "{role}"')
        connection.commit()
    yield admin, engine, tenant, role
    engine.dispose()
    with admin.begin() as conn:
        conn.exec_driver_sql(f'DROP OWNED BY "{role}"')
        conn.exec_driver_sql(f'DROP ROLE "{role}"')
    admin.dispose()

def approved(admin, tenant):
    store = ApprovalStore(persist=SqlPersistSession(sessionmaker(admin)))
    row = hold_approval(store, tenant, ApprovalCreate(who="reviewer", why="completion"))
    return resolve_approval(store, tenant, row.id, decision=ApprovalDecision.ALLOW_ONCE, resolved_by="reviewer")

def test_competing_consumers_restart_and_rls(scoped_engine):
    admin, engine, tenant, role = scoped_engine
    row, barrier = approved(admin, tenant), Barrier(4, timeout=15)
    def consume(_):
        store = ApprovalStore(persist=SqlPersistSession(sessionmaker(engine)))
        barrier.wait()
        try:
            consume_allow_once(store, tenant, row.id)
            return True
        except HTTPException as exc:
            assert exc.status_code == 409
            return False
    with ThreadPoolExecutor(4) as pool:
        assert sorted(pool.map(consume, range(4))) == [False, False, False, True]
    engine.dispose()
    persist = SqlPersistSession(sessionmaker(engine))
    assert not persist.consume_approval(tenant, row.id, datetime.now(UTC))
    fresh, wrong = approved(admin, tenant), uuid.uuid4()
    assert not persist.consume_approval(wrong, fresh.id, datetime.now(UTC))
    with engine.begin() as conn:
        assert conn.scalar(text("SELECT current_user")) == role
        assert conn.scalar(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")) is False
        conn.execute(text("SELECT set_config('app.current_tenant_id',:tid,true)"), {"tid": str(wrong)})
        assert conn.scalar(text("SELECT count(*) FROM aiuditor_approvals")) == 0
    assert persist.consume_approval(tenant, fresh.id, datetime.now(UTC))

@pytest.mark.parametrize("changes", ["expires_at = now() - interval '1 second'", "status = 'denied'", "decision = 'allow_session'", "consumed_at = now()"])
def test_conditional_update_refuses_invalid_authority(scoped_engine, changes):
    admin, engine, tenant, _ = scoped_engine
    row = approved(admin, tenant)
    with admin.begin() as conn:
        conn.execute(text(f"UPDATE aiuditor_approvals SET {changes} WHERE id=:id"), {"id": row.id})
    assert not SqlPersistSession(sessionmaker(engine)).consume_approval(tenant, row.id, datetime.now(UTC))

@pytest.mark.parametrize("role_name", ["auditor", "admin"])
async def test_human_session_rechecks_live_membership(async_engine, monkeypatch, role_name):
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    monkeypatch.setattr(routes, "get_session_maker", lambda: maker)
    tenant, user, role_id, subject = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), str(uuid.uuid4())
    async with maker() as session:
        session.add(Tenant(id=tenant, name=str(tenant), is_active=True))
        await session.flush()
        session.add(Role(id=role_id, tenant_id=tenant, name=role_name, permissions=[]))
        await session.flush()
        session.add(User(id=user, tenant_id=tenant, keycloak_sub=subject, email=f"{user}@example.test", first_name="Test", last_name="Human", gender="unspecified", is_active=True, role_id=role_id))
        await session.commit()
    token = mint_tenant_session_jwt(tenant_id=tenant, extra_roles=["ir"], claims={"sub": subject, "azp": "controlplane-frontend", "account_type": "human", "amr": ["pwd"]})
    request = Request({"type": "http", "headers": Headers({"Authorization": f"Bearer {token}", "X-Tenant-ID": str(tenant)}).raw, "app": type("App", (), {"state": type("State", (), {})()})()})
    assert await resolver_subject(request) == subject
    for changes in [{"role_id": None}, {"role_id": role_id, "is_active": False}]:
        async with maker() as session:
            member = await session.get(User, user)
            for key, value in changes.items():
                setattr(member, key, value)
            await session.commit()
        with pytest.raises(HTTPException) as error:
            await resolver_subject(request)
        assert error.value.status_code == 403
