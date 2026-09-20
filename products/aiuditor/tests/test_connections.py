from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from aiuditor_service.credentials import credential_ref
from aiuditor_service.domain.models import ConnectionCreate, ConnectionKind, ConnectionPurpose
from conftest import tenant_session_headers

DEVICE = uuid.UUID("a6400000-0640-4000-8000-000000000001")


def test_host_create_requires_device_id():
    with pytest.raises(ValidationError):
        ConnectionCreate(kind=ConnectionKind.HOST, purpose=ConnectionPurpose.LEASE)


def test_host_create_rejects_endpoint():
    with pytest.raises(ValidationError):
        ConnectionCreate(
            kind=ConnectionKind.HOST,
            purpose=ConnectionPurpose.LEASE,
            device_id=DEVICE,
            endpoint="https://llm.example/v1",
        )


def test_host_create_rejects_credential():
    with pytest.raises(ValidationError):
        ConnectionCreate(
            kind=ConnectionKind.HOST,
            purpose=ConnectionPurpose.LEASE,
            device_id=DEVICE,
            credential="hm_dev_stays-on-hypermesh",
        )


def test_provider_create_requires_https_endpoint():
    with pytest.raises(ValidationError):
        ConnectionCreate(kind=ConnectionKind.PROVIDER, purpose=ConnectionPurpose.AUDITOR)
    with pytest.raises(ValidationError):
        ConnectionCreate(
            kind=ConnectionKind.PROVIDER,
            purpose=ConnectionPurpose.AUDITOR,
            endpoint="http://llm.example/v1",
        )
    with pytest.raises(ValidationError):
        ConnectionCreate(
            kind=ConnectionKind.PROVIDER,
            purpose=ConnectionPurpose.AUDITOR,
            endpoint="https://user:pass@llm.example/v1",
        )


def test_provider_create_rejects_device_id():
    with pytest.raises(ValidationError):
        ConnectionCreate(
            kind=ConnectionKind.PROVIDER,
            purpose=ConnectionPurpose.AUDITOR,
            device_id=DEVICE,
            endpoint="https://llm.example/v1",
        )


def test_provider_create_accepts_https():
    body = ConnectionCreate(
        kind=ConnectionKind.PROVIDER,
        purpose=ConnectionPurpose.AUDITOR,
        endpoint="https://llm.example/v1",
    )
    assert body.endpoint == "https://llm.example/v1"


@pytest.mark.asyncio
async def test_create_and_get_provider(http_client, tenant_headers):
    created = await http_client.post(
        "/api/v1/aiuditor/connections",
        headers=tenant_headers,
        json={
            "kind": "provider",
            "purpose": "auditor",
            "endpoint": "https://llm.example/v1",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "pending"
    assert body["credential_ref"] is None
    got = await http_client.get(
        f"/api/v1/aiuditor/connections/{body['id']}",
        headers=tenant_headers,
    )
    assert got.status_code == 200, got.text
    assert got.json()["endpoint"] == "https://llm.example/v1"


@pytest.mark.asyncio
async def test_list_connections_is_tenant_scoped(
    http_client, tenant_headers, tenant_a, tenant_b
):
    await http_client.post(
        "/api/v1/aiuditor/connections",
        headers=tenant_headers,
        json={
            "kind": "host",
            "purpose": "lease",
            "device_id": str(DEVICE),
        },
    )
    other = await http_client.post(
        "/api/v1/aiuditor/connections",
        headers=tenant_session_headers(tenant_b),
        json={
            "kind": "provider",
            "purpose": "auditor",
            "endpoint": "https://other.example/v1",
            "credential": "provider-secret",
        },
    )
    assert other.status_code == 201, other.text
    listed = await http_client.get("/api/v1/aiuditor/connections", headers=tenant_headers)
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == str(tenant_a)
    assert rows[0]["kind"] == "host"
    foreign = await http_client.get(
        f"/api/v1/aiuditor/connections/{other.json()['id']}",
        headers=tenant_headers,
    )
    assert foreign.status_code == 404


@pytest.mark.asyncio
async def test_http_refuses_header_only_and_machine(http_client, tenant_a):
    header_only = await http_client.post(
        "/api/v1/aiuditor/connections",
        headers={"X-Tenant-ID": str(tenant_a)},
        json={
            "kind": "host",
            "purpose": "lease",
            "device_id": str(DEVICE),
        },
    )
    assert header_only.status_code == 401, header_only.text

    machine = await http_client.post(
        "/api/v1/aiuditor/connections",
        headers={
            "X-Tenant-ID": str(tenant_a),
            "Authorization": "Bearer hm_dev_not-a-grant",
        },
        json={
            "kind": "host",
            "purpose": "lease",
            "device_id": str(DEVICE),
        },
    )
    assert machine.status_code == 401, machine.text


@pytest.mark.asyncio
async def test_http_refuses_foreign_tenant_session(http_client, tenant_a, tenant_b):
    headers = tenant_session_headers(tenant_a)
    headers["X-Tenant-ID"] = str(tenant_b)
    res = await http_client.post(
        "/api/v1/aiuditor/connections",
        headers=headers,
        json={
            "kind": "provider",
            "purpose": "auditor",
            "endpoint": "https://llm.example/v1",
        },
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_http_rejects_http_endpoint(http_client, tenant_headers):
    res = await http_client.post(
        "/api/v1/aiuditor/connections",
        headers=tenant_headers,
        json={
            "kind": "provider",
            "purpose": "auditor",
            "endpoint": "http://llm.example/v1",
        },
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_missing_connection_is_404(http_client, tenant_headers):
    res = await http_client.get(
        f"/api/v1/aiuditor/connections/{uuid.uuid4()}",
        headers=tenant_headers,
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_response_never_echoes_raw_credential(http_client, tenant_headers):
    secret = "sk-never-on-the-wire-again"
    created = await http_client.post(
        "/api/v1/aiuditor/connections",
        headers=tenant_headers,
        json={
            "kind": "provider",
            "purpose": "lease",
            "endpoint": "https://llm.example/v1",
            "credential": secret,
        },
    )
    assert created.status_code == 201, created.text
    text = created.text
    assert secret not in text
    assert created.json()["credential_ref"] == credential_ref(secret)
