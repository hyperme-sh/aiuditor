from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from aiuditor_service.approvals import (
    APPROVAL_NOT_HELD,
    LLM_NEVER_RESOLVES,
    NAMED_HUMAN_REQUIRED,
    expire_due,
    hold_approval,
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
    assert "/api/v1/aiuditor/approvals/{approval_id}/resolve" in paths
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


def test_llm_subject_is_refused_on_resolve():
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
        resolver_subject(Request(scope))
    assert exc.value.detail == LLM_NEVER_RESOLVES
