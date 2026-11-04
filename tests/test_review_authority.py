"""Regression boundaries for human authority, lab reports and one-use grants."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from fastapi import HTTPException, Request
from starlette.datastructures import Headers

from aiuditor_service.approvals import resolver_subject, hold_approval, resolve_approval, consume_allow_once
from aiuditor_service.domain.models import ApprovalCreate, ApprovalDecision
from aiuditor_service.persist import MemoryPersistSession, SqlPersistSession
from aiuditor_service.store import ApprovalStore
from conftest import tenant_session_headers


def request(headers):
    return Request({"type": "http", "headers": Headers(headers).raw,
                    "app": type("App", (), {"state": type("State", (), {})()})()})


@pytest.mark.parametrize("purpose", ["ir", "service"])
async def test_exchange_never_resolves_even_with_named_subject(purpose, tenant_a):
    from controlplane_identity.security.api_key_exchange_jwt import mint_api_key_session_jwt
    token = mint_api_key_session_jwt(key_record_id=uuid.uuid4(), tenant_id=tenant_a,
                                    scopes=[], purpose=purpose)
    with pytest.raises(HTTPException) as error:
        await resolver_subject(request({"Authorization": f"Bearer {token}", "X-Tenant-ID": str(tenant_a)}))
    assert error.value.status_code == 401


@pytest.mark.parametrize("changes", [{"account_type": "machine"}, {"account_type": None},
                                      {"amr": ["client_credentials"]}, {"amr": None}])
async def test_missing_human_proof_denied(changes, tenant_a):
    from controlplane_identity.security.tenant_session_jwt import mint_tenant_session_jwt
    claims = {"sub": "named-person", "account_type": "human", "amr": ["pwd"], **changes}
    token = mint_tenant_session_jwt(claims=claims, tenant_id=tenant_a, extra_roles=["ir"])
    with pytest.raises(HTTPException) as error:
        await resolver_subject(request({"Authorization": f"Bearer {token}", "X-Tenant-ID": str(tenant_a)}))
    assert error.value.status_code == 403


async def test_current_membership_required(app, monkeypatch, tenant_a):
    from aiuditor_service.private_models import routes
    calls = []
    async def no_member(session, tenant, *, keycloak_sub, role):
        calls.append((tenant, keycloak_sub, role))
        return None
    monkeypatch.setattr(routes, "get_active_tenant_role_member", no_member)
    with pytest.raises(HTTPException) as error:
        await resolver_subject(request(tenant_session_headers(tenant_a)))
    assert error.value.status_code == 403
    assert [x[2] for x in calls] == ["auditor", "admin"]
    assert all(x[0] == tenant_a for x in calls)


@pytest.mark.parametrize("credential", ["tenant", "service", "internal", "none"])
async def test_unbound_lab_report_cannot_change_state(http_client, app, tenant_a, credential, monkeypatch):
    monkeypatch.setenv("AIUDITOR_INTERNAL_TOKEN", "test-internal")
    headers = tenant_session_headers(tenant_a) if credential == "tenant" else {"X-Tenant-ID": str(tenant_a)}
    if credential == "service":
        from controlplane_identity.security.api_key_exchange_jwt import mint_api_key_session_jwt
        headers["Authorization"] = "Bearer " + mint_api_key_session_jwt(
            key_record_id=uuid.uuid4(), tenant_id=tenant_a, scopes=[], purpose="service")
    if credential == "internal":
        headers["X-Internal-Token"] = "test-internal"
    def must_not_read(*args):
        raise AssertionError("unbound evidence reached admission store")
    monkeypatch.setattr(app.state.admission_store, "get", must_not_read)
    response = await http_client.post(f"/api/v1/aiuditor/admissions/{uuid.uuid4()}/sandbox",
                                     headers=headers, json={"soak_passed": True, "egress_ok": True})
    assert response.status_code == 503


def test_two_memory_consumers_only_one_wins():
    persist = MemoryPersistSession()
    tenant = uuid.uuid4()
    store = ApprovalStore(persist=persist)
    held = hold_approval(store, tenant, ApprovalCreate(who="person", why="completion"))
    resolve_approval(store, tenant, held.id, decision=ApprovalDecision.ALLOW_ONCE, resolved_by="human")
    barrier = Barrier(2)
    def consume():
        barrier.wait()
        try:
            consume_allow_once(ApprovalStore(persist=persist), tenant, held.id)
            return True
        except HTTPException as exc:
            assert exc.status_code == 409
            return False
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(lambda _: consume(), range(2))) == [False, True]


def test_sql_conditional_consume_competing_sessions(tmp_path):
    # SQLite exercises real conditional SQL and independent transactions, not
    # PostgreSQL RLS/owner-migration acceptance (tracked separately).
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.orm import sessionmaker
    engine = create_engine(f"sqlite:///{tmp_path / 'consume.db'}")
    @event.listens_for(engine, "connect")
    def configure(connection, _):
        connection.create_function("set_config", 3, lambda *args: "")
    tenant, approval = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE aiuditor_approvals (id CHAR(32) PRIMARY KEY, tenant_id CHAR(32), status TEXT, decision TEXT, consumed_at DATETIME, expires_at DATETIME)"))
        conn.execute(text("INSERT INTO aiuditor_approvals VALUES (:id,:tenant,'allowed','allow_once',NULL,:expiry)"),
                     {"id": approval.hex, "tenant": tenant.hex, "expiry": now + timedelta(hours=1)})
    barrier = Barrier(2)
    def consume():
        persist = SqlPersistSession(sessionmaker(engine))
        barrier.wait()
        return persist.consume_approval(tenant, approval, now)
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(lambda _: consume(), range(2))) == [False, True]
    persist = SqlPersistSession(sessionmaker(engine))
    assert not persist.consume_approval(uuid.uuid4(), approval, now)
    with engine.begin() as conn:
        conn.execute(text("UPDATE aiuditor_approvals SET consumed_at=NULL, expires_at=:expiry"), {"expiry": now - timedelta(seconds=1)})
    assert not persist.consume_approval(tenant, approval, now)
    engine.dispose()
