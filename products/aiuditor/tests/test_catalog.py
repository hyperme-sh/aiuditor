from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from aiuditor_service.catalog import (
    PROVIDER_NOT_LEASE,
    SERVICE_METER,
    SERVICE_SIBLING,
    put_service,
)
from aiuditor_service.domain.models import (
    Connection,
    ConnectionKind,
    ConnectionPurpose,
    ConnectionStatus,
    ServiceBackend,
    ServiceCreate,
)
from aiuditor_service.events import CATALOG_SERVICE_CREATED_V1
from aiuditor_service.store import CatalogStore, ConnectionStore
from conftest import tenant_session_headers


def test_p1_and_p2_rows_are_distinct():
    store = CatalogStore()
    connections = ConnectionStore()
    tenant = uuid.uuid4()
    p1 = put_service(
        store,
        connections,
        tenant,
        ServiceCreate(
            title="AGX reserved box",
            backend=ServiceBackend.HYPERMESH_CLASS,
            class_id="agx-large",
        ),
    )
    p2 = put_service(
        store,
        connections,
        tenant,
        ServiceCreate(
            title="Llama 8B",
            backend=ServiceBackend.HYPERMESH_CATALOG,
            catalog_id="llama-3.1-8b-q4",
        ),
    )
    assert p1.backend is ServiceBackend.HYPERMESH_CLASS
    assert p1.class_id == "agx-large"
    assert p1.catalog_id is None
    assert p2.backend is ServiceBackend.HYPERMESH_CATALOG
    assert p2.catalog_id == "llama-3.1-8b-q4"
    assert p2.class_id is None
    assert p1.meters == ["wall_time", "capacity_block"]


def test_siblings_and_unknown_meters_are_refused():
    store = CatalogStore()
    connections = ConnectionStore()
    tenant = uuid.uuid4()
    with pytest.raises(HTTPException) as sibling:
        put_service(
            store,
            connections,
            tenant,
            ServiceCreate(
                title="turn",
                backend=ServiceBackend.HYPERMESH_CLASS,
                class_id="agx-large",
            ),
        )
    assert sibling.value.detail == SERVICE_SIBLING
    with pytest.raises(HTTPException) as meter:
        put_service(
            store,
            connections,
            tenant,
            ServiceCreate(
                title="AGX",
                backend=ServiceBackend.HYPERMESH_CLASS,
                class_id="agx-large",
                meters=["tokens"],
            ),
        )
    assert meter.value.detail == SERVICE_METER


def test_provider_must_be_a_lease_connection():
    store = CatalogStore()
    connections = ConnectionStore()
    tenant = uuid.uuid4()
    auditor = Connection(
        id=uuid.uuid4(),
        tenant_id=tenant,
        kind=ConnectionKind.PROVIDER,
        purpose=ConnectionPurpose.AUDITOR,
        endpoint="https://llm.example/v1",
        status=ConnectionStatus.PENDING,
        created_at=datetime.now(UTC),
    )
    connections.put(auditor)
    with pytest.raises(HTTPException) as exc:
        put_service(
            store,
            connections,
            tenant,
            ServiceCreate(
                title="Hosted chat",
                backend=ServiceBackend.PROVIDER,
                connection_id=auditor.id,
            ),
        )
    assert exc.value.detail == PROVIDER_NOT_LEASE


@pytest.mark.asyncio
async def test_operator_can_list_p1_p2_and_provider(
    http_client, tenant_headers, event_sink
):
    provider = await http_client.post(
        "/api/v1/aiuditor/connections",
        headers=tenant_headers,
        json={
            "kind": "provider",
            "purpose": "lease",
            "endpoint": "https://llm.example/v1",
        },
    )
    assert provider.status_code == 201, provider.text
    p1 = await http_client.post(
        "/api/v1/aiuditor/catalog",
        headers=tenant_headers,
        json={
            "title": "AGX reserved box",
            "backend": "hypermesh_class",
            "class_id": "agx-large",
        },
    )
    assert p1.status_code == 201, p1.text
    p2 = await http_client.post(
        "/api/v1/aiuditor/catalog",
        headers=tenant_headers,
        json={
            "title": "Llama 8B",
            "backend": "hypermesh_catalog",
            "catalog_id": "llama-3.1-8b-q4",
        },
    )
    assert p2.status_code == 201, p2.text
    hosted = await http_client.post(
        "/api/v1/aiuditor/catalog",
        headers=tenant_headers,
        json={
            "title": "Hosted chat",
            "backend": "provider",
            "connection_id": provider.json()["id"],
        },
    )
    assert hosted.status_code == 201, hosted.text
    assert hosted.json()["connection_id"] == provider.json()["id"]
    listed = await http_client.get("/api/v1/aiuditor/catalog", headers=tenant_headers)
    assert listed.status_code == 200
    backends = {row["backend"] for row in listed.json()}
    assert backends == {"hypermesh_class", "hypermesh_catalog", "provider"}
    assert CATALOG_SERVICE_CREATED_V1 in [e["event_type"] for e in event_sink]
    assert "messages" not in str(event_sink)


@pytest.mark.asyncio
async def test_renter_exchange_cannot_write_catalog(http_client, tenant_a):
    from api.infrastructure.security.api_key_exchange_jwt import mint_api_key_session_jwt

    token = mint_api_key_session_jwt(
        key_record_id=uuid.uuid4(),
        tenant_id=tenant_a,
        scopes=[],
        purpose="renter",
    )
    denied = await http_client.post(
        "/api/v1/aiuditor/catalog",
        headers={
            "X-Tenant-ID": str(tenant_a),
            "Authorization": f"Bearer {token}",
        },
        json={
            "title": "AGX",
            "backend": "hypermesh_class",
            "class_id": "agx-large",
        },
    )
    assert denied.status_code == 401


@pytest.mark.asyncio
async def test_catalog_is_tenant_scoped(http_client, tenant_headers, tenant_b):
    await http_client.post(
        "/api/v1/aiuditor/catalog",
        headers=tenant_headers,
        json={
            "title": "AGX reserved box",
            "backend": "hypermesh_class",
            "class_id": "agx-large",
        },
    )
    other = await http_client.get(
        "/api/v1/aiuditor/catalog",
        headers=tenant_session_headers(tenant_b),
    )
    assert other.status_code == 200
    assert other.json() == []


@pytest.mark.asyncio
async def test_missing_service_is_404(http_client, tenant_headers):
    missing = await http_client.get(
        f"/api/v1/aiuditor/catalog/{uuid.uuid4()}",
        headers=tenant_headers,
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_site_inbox_is_not_on_catalog_paths(http_client):
    spec = await http_client.get("/openapi.json")
    paths = spec.json()["paths"]
    assert "/api/v1/aiuditor/catalog" in paths
    assert "/api/v1/aiuditor/catalog/{service_id}" in paths
    assert "/api/v1/auditor/v0/tickets" not in paths
    assert "/api/v1/grants/v0/grants" not in paths
    assert "/api/v1/turn/allocate" not in paths
