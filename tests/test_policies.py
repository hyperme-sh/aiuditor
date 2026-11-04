from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from aiuditor_service.domain.models import ApprovalPolicyCreate, PolicyDisposition
from aiuditor_service.events import POLICY_CREATED_V1, POLICY_DELETED_V1
from aiuditor_service.policies import (
    POLICY_EXISTS,
    POLICY_PURPOSE,
    evaluate_policy,
    put_policy,
)
from aiuditor_service.store import PolicyStore
from conftest import tenant_session_headers


@pytest.fixture
def internal_token(monkeypatch):
    monkeypatch.setenv("AIUDITOR_INTERNAL_TOKEN", "test-aiuditor-internal-token")
    return "test-aiuditor-internal-token"


def test_evaluate_prefers_class_over_purpose():
    store = PolicyStore()
    tenant = uuid.uuid4()
    put_policy(
        store,
        tenant,
        ApprovalPolicyCreate(purpose="renter", disposition=PolicyDisposition.HOLD),
    )
    put_policy(
        store,
        tenant,
        ApprovalPolicyCreate(
            purpose="renter",
            prompt_class="incident_triage",
            disposition=PolicyDisposition.ALLOW,
        ),
    )
    assert (
        evaluate_policy(
            store, tenant, purpose="renter", prompt_class="incident_triage"
        )
        is PolicyDisposition.ALLOW
    )
    assert (
        evaluate_policy(store, tenant, purpose="renter", prompt_class="out_of_scope")
        is PolicyDisposition.HOLD
    )
    assert evaluate_policy(store, tenant, purpose="ir") is None


def test_evaluate_prefers_connection():
    store = PolicyStore()
    tenant = uuid.uuid4()
    connection = uuid.uuid4()
    put_policy(
        store,
        tenant,
        ApprovalPolicyCreate(purpose="renter", disposition=PolicyDisposition.HOLD),
    )
    put_policy(
        store,
        tenant,
        ApprovalPolicyCreate(
            purpose="renter",
            connection_id=connection,
            disposition=PolicyDisposition.DENY,
        ),
    )
    assert (
        evaluate_policy(store, tenant, purpose="renter", connection_id=connection)
        is PolicyDisposition.DENY
    )
    assert (
        evaluate_policy(store, tenant, purpose="renter", connection_id=uuid.uuid4())
        is PolicyDisposition.HOLD
    )


def test_duplicate_policy_is_409():
    store = PolicyStore()
    tenant = uuid.uuid4()
    body = ApprovalPolicyCreate(purpose="renter", disposition=PolicyDisposition.HOLD)
    put_policy(store, tenant, body)
    with pytest.raises(HTTPException) as exc:
        put_policy(store, tenant, body)
    assert exc.value.status_code == 409
    assert exc.value.detail == POLICY_EXISTS


def test_unknown_purpose_is_400():
    store = PolicyStore()
    with pytest.raises(HTTPException) as exc:
        put_policy(
            store,
            uuid.uuid4(),
            ApprovalPolicyCreate(purpose="marketplace", disposition=PolicyDisposition.HOLD),
        )
    assert exc.value.detail == POLICY_PURPOSE


@pytest.mark.asyncio
async def test_operator_can_put_and_delete_policy(
    http_client, tenant_headers, tenant_a, event_sink
):
    created = await http_client.post(
        "/api/v1/aiuditor/policies",
        headers=tenant_headers,
        json={"purpose": "renter", "disposition": "hold"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["purpose"] == "renter"
    assert body["disposition"] == "hold"
    assert body["prompt_class"] is None
    assert POLICY_CREATED_V1 in [e["event_type"] for e in event_sink]
    assert "messages" not in str(event_sink)

    listed = await http_client.get(
        "/api/v1/aiuditor/policies",
        headers=tenant_headers,
        params={"purpose": "renter"},
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    removed = await http_client.delete(
        f"/api/v1/aiuditor/policies/{body['id']}",
        headers=tenant_headers,
    )
    assert removed.status_code == 204
    assert POLICY_DELETED_V1 in [e["event_type"] for e in event_sink]
    empty = await http_client.get("/api/v1/aiuditor/policies", headers=tenant_headers)
    assert empty.json() == []


@pytest.mark.asyncio
async def test_internal_can_read_policy_not_write(
    http_client, tenant_headers, tenant_a, internal_token
):
    created = await http_client.post(
        "/api/v1/aiuditor/policies",
        headers=tenant_headers,
        json={"purpose": "ir", "prompt_class": "out_of_scope", "disposition": "allow"},
    )
    assert created.status_code == 201
    listed = await http_client.get(
        "/api/v1/aiuditor/policies",
        headers={
            "X-Tenant-ID": str(tenant_a),
            "X-Internal-Token": internal_token,
        },
        params={"purpose": "ir"},
    )
    assert listed.status_code == 200
    assert listed.json()[0]["disposition"] == "allow"
    refused = await http_client.post(
        "/api/v1/aiuditor/policies",
        headers={
            "X-Tenant-ID": str(tenant_a),
            "X-Internal-Token": internal_token,
        },
        json={"purpose": "renter", "disposition": "deny"},
    )
    assert refused.status_code == 401


@pytest.mark.asyncio
async def test_renter_exchange_cannot_write_policy(http_client, tenant_a):
    from api.infrastructure.security.api_key_exchange_jwt import mint_api_key_session_jwt

    token = mint_api_key_session_jwt(
        key_record_id=uuid.uuid4(),
        tenant_id=tenant_a,
        scopes=[],
        purpose="renter",
    )
    denied = await http_client.post(
        "/api/v1/aiuditor/policies",
        headers={
            "X-Tenant-ID": str(tenant_a),
            "Authorization": f"Bearer {token}",
        },
        json={"purpose": "renter", "disposition": "hold"},
    )
    assert denied.status_code == 401


@pytest.mark.asyncio
async def test_policy_is_tenant_scoped(http_client, tenant_headers, tenant_b):
    await http_client.post(
        "/api/v1/aiuditor/policies",
        headers=tenant_headers,
        json={"purpose": "renter", "disposition": "deny"},
    )
    other = await http_client.get(
        "/api/v1/aiuditor/policies",
        headers=tenant_session_headers(tenant_b),
    )
    assert other.status_code == 200
    assert other.json() == []


@pytest.mark.asyncio
async def test_missing_policy_delete_is_404(http_client, tenant_headers):
    missing = await http_client.delete(
        f"/api/v1/aiuditor/policies/{uuid.uuid4()}",
        headers=tenant_headers,
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_site_inbox_is_not_on_policy_paths(http_client):
    spec = await http_client.get("/openapi.json")
    paths = spec.json()["paths"]
    assert "/api/v1/aiuditor/policies" in paths
    assert "/api/v1/auditor/v0/tickets" not in paths
    assert "/api/v1/grants/v0/grants" not in paths
