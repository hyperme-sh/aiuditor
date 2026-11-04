from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from aiuditor_service.approvals import (
    APPROVAL_ALREADY_CONSUMED,
    APPROVAL_NOT_ALLOW_ONCE,
    APPROVAL_NOT_HELD,
    INTERNAL_REQUIRED,
    LLM_NEVER_RESOLVES,
    NAMED_HUMAN_REQUIRED,
    consume_allow_once,
    expire_due,
    hold_approval,
    latest_for,
    resolve_approval,
)
from aiuditor_service.domain.models import (
    ApprovalCreate,
    ApprovalDecision,
    ApprovalStatus,
    ApprovalWhy,
)
from aiuditor_service.events import APPROVAL_HELD_V1, APPROVAL_RESOLVED_V1
from aiuditor_service.store import ApprovalStore
from conftest import tenant_session_headers


@pytest.fixture
def internal_token(monkeypatch):
    monkeypatch.setenv("AIUDITOR_INTERNAL_TOKEN", "test-aiuditor-internal-token")
    return "test-aiuditor-internal-token"


def test_hold_strips_prompt_shaped_who():
    store = ApprovalStore()
    tenant = uuid.uuid4()
    with pytest.raises(HTTPException) as exc:
        hold_approval(
            store,
            tenant,
            ApprovalCreate(who="user prompt: do the thing", why=ApprovalWhy.COMPLETION),
        )
    assert exc.value.status_code == 400


def test_timeout_is_deny_without_a_person():
    store = ApprovalStore()
    tenant = uuid.uuid4()
    start = datetime(2026, 9, 20, 17, 0, tzinfo=UTC)
    row = hold_approval(
        store,
        tenant,
        ApprovalCreate(who="renter-key", why=ApprovalWhy.COMPLETION, ttl_seconds=30),
        now=start,
    )
    expired = expire_due(store, tenant, now=start + timedelta(seconds=31))
    assert len(expired) == 1
    assert expired[0].id == row.id
    assert expired[0].status is ApprovalStatus.TIMED_OUT
    assert expired[0].decision is ApprovalDecision.DENY
    assert expired[0].resolved_by is None
    with pytest.raises(HTTPException) as exc:
        resolve_approval(
            store,
            tenant,
            row.id,
            decision=ApprovalDecision.ALLOW_ONCE,
            resolved_by="11111111-1111-1111-1111-111111111111",
            now=start + timedelta(seconds=32),
        )
    assert exc.value.detail == APPROVAL_NOT_HELD


@pytest.mark.asyncio
async def test_inbox_hold_and_named_human_resolve(
    http_client, tenant_headers, tenant_a, event_sink
):
    created = await http_client.post(
        "/api/v1/aiuditor/approvals",
        headers=tenant_headers,
        json={
            "who": "renter@example.com",
            "why": "completion",
            "purpose": "renter",
            "prompt_class": "incident_triage",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "held"
    assert body["who"] == "renter@example.com"
    assert "prompt" not in body["who"]
    assert APPROVAL_HELD_V1 in [e["event_type"] for e in event_sink]
    assert "messages" not in str(event_sink)

    listed = await http_client.get(
        "/api/v1/aiuditor/approvals",
        headers=tenant_headers,
        params={"status": "held"},
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    resolved = await http_client.post(
        f"/api/v1/aiuditor/approvals/{body['id']}/resolve",
        headers=tenant_headers,
        json={"decision": "allow_once"},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "allowed"
    assert resolved.json()["decision"] == "allow_once"
    assert resolved.json()["resolved_by"] == "11111111-1111-1111-1111-111111111111"
    assert APPROVAL_RESOLVED_V1 in [e["event_type"] for e in event_sink]


@pytest.mark.asyncio
async def test_internal_can_hold_but_cannot_resolve(
    http_client, tenant_a, internal_token
):
    created = await http_client.post(
        "/api/v1/aiuditor/approvals",
        headers={
            "X-Tenant-ID": str(tenant_a),
            "X-Internal-Token": internal_token,
        },
        json={"who": "door", "why": "completion"},
    )
    assert created.status_code == 201, created.text
    refused = await http_client.post(
        f"/api/v1/aiuditor/approvals/{created.json()['id']}/resolve",
        headers={
            "X-Tenant-ID": str(tenant_a),
            "X-Internal-Token": internal_token,
        },
        json={"decision": "deny"},
    )
    assert refused.status_code == 401
    assert refused.json()["detail"] == NAMED_HUMAN_REQUIRED


@pytest.mark.asyncio
async def test_renter_exchange_cannot_use_inbox(http_client, tenant_a):
    from api.infrastructure.security.api_key_exchange_jwt import mint_api_key_session_jwt

    token = mint_api_key_session_jwt(
        key_record_id=uuid.uuid4(),
        tenant_id=tenant_a,
        scopes=[],
        purpose="renter",
    )
    denied = await http_client.post(
        "/api/v1/aiuditor/approvals",
        headers={
            "X-Tenant-ID": str(tenant_a),
            "Authorization": f"Bearer {token}",
        },
        json={"who": "renter", "why": "completion"},
    )
    assert denied.status_code == 401


@pytest.mark.asyncio
async def test_site_inbox_paths_are_not_on_aiuditor(http_client):
    spec = await http_client.get("/openapi.json")
    paths = spec.json()["paths"]
    assert "/api/v1/aiuditor/approvals" in paths
    assert "/api/v1/aiuditor/approvals/latest" in paths
    assert "/api/v1/aiuditor/approvals/{approval_id}/resolve" in paths
    assert "/api/v1/aiuditor/approvals/{approval_id}/consume" in paths
    assert "/api/v1/auditor/v0/tickets" not in paths
    assert "/api/v1/grants/v0/grants" not in paths


@pytest.mark.asyncio
async def test_inbox_is_tenant_scoped(http_client, tenant_headers, tenant_b):
    await http_client.post(
        "/api/v1/aiuditor/approvals",
        headers=tenant_headers,
        json={"who": "alice", "why": "tool"},
    )
    other = await http_client.get(
        "/api/v1/aiuditor/approvals",
        headers=tenant_session_headers(tenant_b),
    )
    assert other.status_code == 200
    assert other.json() == []


@pytest.mark.asyncio
async def test_missing_approval_is_404(http_client, tenant_headers):
    missing = await http_client.get(
        f"/api/v1/aiuditor/approvals/{uuid.uuid4()}",
        headers=tenant_headers,
    )
    assert missing.status_code == 404


async def test_llm_subject_is_refused_on_resolve():
    from fastapi import Request
    from starlette.datastructures import Headers

    from aiuditor_service.approvals import resolver_subject
    from api.infrastructure.security.tenant_session_jwt import mint_tenant_session_jwt

    tenant = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    token = mint_tenant_session_jwt(
        claims={
            "sub": "llm",
            "email": "model@example.com",
            "realm_access": {"roles": ["ir"]},
            "groups": ["ir"],
            "scope": "openid",
            "account_type": "human",
            "amr": ["pwd"],
        },
        tenant_id=tenant,
        extra_roles=["ir"],
    )
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/aiuditor/approvals/x/resolve",
        "headers": Headers(
            {
                "authorization": f"Bearer {token}",
                "x-tenant-id": str(tenant),
            }
        ).raw,
        "query_string": b"",
        "app": type("A", (), {"state": type("S", (), {})()})(),
    }
    with pytest.raises(HTTPException) as exc:
        await resolver_subject(Request(scope))
    assert exc.value.detail == LLM_NEVER_RESOLVES


def test_second_hold_reuses_the_open_row():
    store = ApprovalStore()
    tenant = uuid.uuid4()
    lease = uuid.uuid4()
    first = hold_approval(
        store,
        tenant,
        ApprovalCreate(
            who="pk_test",
            why=ApprovalWhy.COMPLETION,
            lease_id=lease,
            prompt_class="out_of_scope",
        ),
    )
    second = hold_approval(
        store,
        tenant,
        ApprovalCreate(
            who="pk_test",
            why=ApprovalWhy.COMPLETION,
            lease_id=lease,
            prompt_class="out_of_scope",
        ),
    )
    assert second.id == first.id
    assert len(store.list_for(tenant)) == 1


def test_consume_allow_once_is_one_shot():
    store = ApprovalStore()
    tenant = uuid.uuid4()
    lease = uuid.uuid4()
    row = hold_approval(
        store,
        tenant,
        ApprovalCreate(who="pk_test", why=ApprovalWhy.COMPLETION, lease_id=lease),
    )
    allowed = resolve_approval(
        store,
        tenant,
        row.id,
        decision=ApprovalDecision.ALLOW_ONCE,
        resolved_by="11111111-1111-1111-1111-111111111111",
    )
    consumed = consume_allow_once(store, tenant, allowed.id)
    assert consumed.consumed_at is not None
    with pytest.raises(HTTPException) as exc:
        consume_allow_once(store, tenant, allowed.id)
    assert exc.value.detail == APPROVAL_ALREADY_CONSUMED
    with pytest.raises(HTTPException) as again:
        consume_allow_once(store, tenant, uuid.uuid4())
    assert again.value.status_code == 404


def test_consume_refuses_allow_session():
    store = ApprovalStore()
    tenant = uuid.uuid4()
    row = hold_approval(
        store,
        tenant,
        ApprovalCreate(who="pk_test", why=ApprovalWhy.COMPLETION, lease_id=uuid.uuid4()),
    )
    resolve_approval(
        store,
        tenant,
        row.id,
        decision=ApprovalDecision.ALLOW_SESSION,
        resolved_by="11111111-1111-1111-1111-111111111111",
    )
    with pytest.raises(HTTPException) as exc:
        consume_allow_once(store, tenant, row.id)
    assert exc.value.detail == APPROVAL_NOT_ALLOW_ONCE


def test_latest_prefers_session_after_consumed_once():
    store = ApprovalStore()
    tenant = uuid.uuid4()
    lease = uuid.uuid4()
    once = hold_approval(
        store,
        tenant,
        ApprovalCreate(
            who="pk_test",
            why=ApprovalWhy.COMPLETION,
            lease_id=lease,
            prompt_class="out_of_scope",
        ),
    )
    resolve_approval(
        store,
        tenant,
        once.id,
        decision=ApprovalDecision.ALLOW_ONCE,
        resolved_by="11111111-1111-1111-1111-111111111111",
    )
    consume_allow_once(store, tenant, once.id)
    session = hold_approval(
        store,
        tenant,
        ApprovalCreate(
            who="pk_test",
            why=ApprovalWhy.COMPLETION,
            lease_id=lease,
            prompt_class="incident_triage",
        ),
    )
    resolve_approval(
        store,
        tenant,
        session.id,
        decision=ApprovalDecision.ALLOW_SESSION,
        resolved_by="11111111-1111-1111-1111-111111111111",
    )
    found = latest_for(
        store,
        tenant,
        lease_id=lease,
        who="pk_test",
        prompt_class="out_of_scope",
    )
    assert found is not None
    assert found.id == session.id


@pytest.mark.asyncio
async def test_internal_can_lookup_and_consume(
    http_client, tenant_headers, tenant_a, internal_token
):
    lease = uuid.uuid4()
    created = await http_client.post(
        "/api/v1/aiuditor/approvals",
        headers={
            "X-Tenant-ID": str(tenant_a),
            "X-Internal-Token": internal_token,
        },
        json={
            "who": "pk_test",
            "why": "completion",
            "lease_id": str(lease),
            "prompt_class": "out_of_scope",
            "purpose": "ir",
        },
    )
    assert created.status_code == 201, created.text
    approval_id = created.json()["id"]
    listed = await http_client.get(
        "/api/v1/aiuditor/approvals",
        headers={
            "X-Tenant-ID": str(tenant_a),
            "X-Internal-Token": internal_token,
        },
        params={"lease_id": str(lease), "who": "pk_test"},
    )
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [approval_id]
    latest = await http_client.get(
        "/api/v1/aiuditor/approvals/latest",
        headers={
            "X-Tenant-ID": str(tenant_a),
            "X-Internal-Token": internal_token,
        },
        params={
            "lease_id": str(lease),
            "who": "pk_test",
            "prompt_class": "out_of_scope",
        },
    )
    assert latest.status_code == 200
    assert latest.json()["id"] == approval_id

    resolved = await http_client.post(
        f"/api/v1/aiuditor/approvals/{approval_id}/resolve",
        headers=tenant_headers,
        json={"decision": "allow_once"},
    )
    assert resolved.status_code == 200
    consumed = await http_client.post(
        f"/api/v1/aiuditor/approvals/{approval_id}/consume",
        headers={
            "X-Tenant-ID": str(tenant_a),
            "X-Internal-Token": internal_token,
        },
    )
    assert consumed.status_code == 200, consumed.text
    assert consumed.json()["consumed_at"] is not None
    named = await http_client.post(
        f"/api/v1/aiuditor/approvals/{approval_id}/consume",
        headers=tenant_headers,
    )
    assert named.status_code == 401
    assert named.json()["detail"] == INTERNAL_REQUIRED


@pytest.mark.asyncio
async def test_latest_is_404_when_empty(http_client, tenant_headers):
    missing = await http_client.get(
        "/api/v1/aiuditor/approvals/latest",
        headers=tenant_headers,
        params={"lease_id": str(uuid.uuid4()), "who": "nobody"},
    )
    assert missing.status_code == 404
