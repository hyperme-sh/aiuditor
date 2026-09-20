from __future__ import annotations

import uuid

import pytest

from aiuditor_service.app import create_app
from aiuditor_service.hypermesh import HOST_LOOKUP_REQUIRED, ClosedHostLookup
from aiuditor_service.interfaces.api.routes import (
    DEVICE_NOT_ENROLLED,
    DEVICE_NOT_FOUND,
    DEVICE_ROLE_NOT_HOST,
    HOST_ALREADY_ASSIGNED,
)
from conftest import FYBER_AGX_DEVICE_ID, tenant_session_headers


@pytest.mark.asyncio
async def test_assign_enrolled_host_is_ready(http_client, tenant_headers, tenant_a):
    created = await http_client.post(
        "/api/v1/aiuditor/connections",
        headers=tenant_headers,
        json={
            "kind": "host",
            "purpose": "auditor",
            "device_id": str(FYBER_AGX_DEVICE_ID),
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["kind"] == "host"
    assert body["purpose"] == "auditor"
    assert body["device_id"] == str(FYBER_AGX_DEVICE_ID)
    assert body["tenant_id"] == str(tenant_a)
    assert body["endpoint"] is None
    assert body["credential_ref"] is None
    assert body["status"] == "ready"


@pytest.mark.asyncio
async def test_unknown_device_is_404(http_client, tenant_headers):
    res = await http_client.post(
        "/api/v1/aiuditor/connections",
        headers=tenant_headers,
        json={
            "kind": "host",
            "purpose": "lease",
            "device_id": str(uuid.uuid4()),
        },
    )
    assert res.status_code == 404, res.text
    assert res.json()["detail"] == DEVICE_NOT_FOUND


@pytest.mark.asyncio
async def test_unenrolled_device_is_409(http_client, tenant_headers, host_lookup):
    device_id = uuid.UUID("bbbbbbbb-1111-4111-8111-bbbbbbbbbbbb")
    host_lookup.enroll(device_id, enrolled=False)
    res = await http_client.post(
        "/api/v1/aiuditor/connections",
        headers=tenant_headers,
        json={
            "kind": "host",
            "purpose": "lease",
            "device_id": str(device_id),
        },
    )
    assert res.status_code == 409, res.text
    assert res.json()["detail"] == DEVICE_NOT_ENROLLED


@pytest.mark.asyncio
async def test_gateway_role_cannot_be_assigned(http_client, tenant_headers, host_lookup):
    device_id = uuid.UUID("cccccccc-1111-4111-8111-cccccccccccc")
    host_lookup.enroll(device_id, role="gateway")
    res = await http_client.post(
        "/api/v1/aiuditor/connections",
        headers=tenant_headers,
        json={
            "kind": "host",
            "purpose": "lease",
            "device_id": str(device_id),
        },
    )
    assert res.status_code == 409, res.text
    assert res.json()["detail"] == DEVICE_ROLE_NOT_HOST


@pytest.mark.asyncio
async def test_foreign_tenant_cannot_assign_other_tenant_device(
    http_client, tenant_b, host_lookup
):
    res = await http_client.post(
        "/api/v1/aiuditor/connections",
        headers=tenant_session_headers(tenant_b),
        json={
            "kind": "host",
            "purpose": "lease",
            "device_id": str(FYBER_AGX_DEVICE_ID),
        },
    )
    assert res.status_code == 404, res.text
    assert res.json()["detail"] == DEVICE_NOT_FOUND


@pytest.mark.asyncio
async def test_same_device_purpose_cannot_be_assigned_twice(http_client, tenant_headers):
    first = await http_client.post(
        "/api/v1/aiuditor/connections",
        headers=tenant_headers,
        json={
            "kind": "host",
            "purpose": "lease",
            "device_id": str(FYBER_AGX_DEVICE_ID),
        },
    )
    assert first.status_code == 201, first.text
    again = await http_client.post(
        "/api/v1/aiuditor/connections",
        headers=tenant_headers,
        json={
            "kind": "host",
            "purpose": "lease",
            "device_id": str(FYBER_AGX_DEVICE_ID),
        },
    )
    assert again.status_code == 409, again.text
    assert again.json()["detail"] == HOST_ALREADY_ASSIGNED


@pytest.mark.asyncio
async def test_same_device_can_be_lease_and_auditor(http_client, tenant_headers):
    lease = await http_client.post(
        "/api/v1/aiuditor/connections",
        headers=tenant_headers,
        json={
            "kind": "host",
            "purpose": "lease",
            "device_id": str(FYBER_AGX_DEVICE_ID),
        },
    )
    auditor = await http_client.post(
        "/api/v1/aiuditor/connections",
        headers=tenant_headers,
        json={
            "kind": "host",
            "purpose": "auditor",
            "device_id": str(FYBER_AGX_DEVICE_ID),
        },
    )
    assert lease.status_code == 201, lease.text
    assert auditor.status_code == 201, auditor.text
    assert lease.json()["id"] != auditor.json()["id"]


@pytest.mark.asyncio
async def test_provider_create_does_not_need_hypermesh(event_sink, tenant_headers):
    import httpx
    from asgi_lifespan import LifespanManager

    app = create_app(
        test_event_sink=event_sink,
        enable_db_middleware=False,
        host_lookup=ClosedHostLookup(),
    )
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/v1/aiuditor/connections",
                headers=tenant_headers,
                json={
                    "kind": "provider",
                    "purpose": "auditor",
                    "endpoint": "https://llm.example/v1",
                },
            )
            assert created.status_code == 201, created.text
            refused = await client.post(
                "/api/v1/aiuditor/connections",
                headers=tenant_headers,
                json={
                    "kind": "host",
                    "purpose": "lease",
                    "device_id": str(FYBER_AGX_DEVICE_ID),
                },
            )
    assert refused.status_code == 503, refused.text
    assert refused.json()["detail"] == HOST_LOOKUP_REQUIRED
