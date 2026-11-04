from __future__ import annotations

import uuid

import pytest

from aiuditor_service.app import create_app
from aiuditor_service.events import CONNECTION_ASSIGNED_V1
from aiuditor_service.hypermesh import ClosedPlaneHop
from aiuditor_service.tenant import AUDITOR_ACTOR_REQUIRED
from aiuditor_service.domain.models import (
    Connection,
    ConnectionKind,
    ConnectionPurpose,
    ConnectionStatus,
)
from aiuditor_service.interfaces.api.routes import (
    CONNECTION_ALREADY_BOUND,
    CONNECTION_DISABLED,
    HOST_TICKET_ONLY,
    LEASE_ALREADY_ASSIGNED,
    LEASE_ASSIGN_ONLY,
    LEASE_DEVICE_MISMATCH,
    LEASE_IS_AUDITOR,
    LEASE_NOT_FOUND,
    PROVIDER_CLASSIFY_ONLY,
)
from aiuditor_service.provider import FakeProviderHop
from conftest import FYBER_AGX_DEVICE_ID


async def _host(http_client, tenant_headers, *, purpose: str) -> dict:
    created = await http_client.post(
        "/api/v1/aiuditor/connections",
        headers=tenant_headers,
        json={
            "kind": "host",
            "purpose": purpose,
            "device_id": str(FYBER_AGX_DEVICE_ID),
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


async def _provider(http_client, tenant_headers) -> dict:
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
    return created.json()


@pytest.mark.asyncio
async def test_auditor_host_mints_ir_ticket_not_renter(
    http_client, tenant_headers, plane_hop, event_sink
):
    row = await _host(http_client, tenant_headers, purpose="auditor")
    minted = await http_client.post(
        f"/api/v1/aiuditor/connections/{row['id']}/ticket",
        headers=tenant_headers,
        json={"reserved_hours": 6},
    )
    assert minted.status_code == 201, minted.text
    body = minted.json()
    assert body["purpose"] == "ir"
    assert body["device_id"] == str(FYBER_AGX_DEVICE_ID)
    assert body["lease_id"]
    assert "ticket" not in body
    got = await http_client.get(
        f"/api/v1/aiuditor/connections/{row['id']}",
        headers=tenant_headers,
    )
    assert got.json()["lease_id"] == body["lease_id"]
    assert CONNECTION_ASSIGNED_V1 in [e["event_type"] for e in event_sink]
    assigned = [e for e in event_sink if e["event_type"] == CONNECTION_ASSIGNED_V1][0]
    assert assigned["payload"]["lease_id"] == body["lease_id"]
    assert "ticket" not in assigned["payload"]
    assert plane_hop.grants[FYBER_AGX_DEVICE_ID].purpose == "ir"

    again = await http_client.post(
        f"/api/v1/aiuditor/connections/{row['id']}/ticket",
        headers=tenant_headers,
        json={},
    )
    assert again.status_code == 409
    assert again.json()["detail"] == CONNECTION_ALREADY_BOUND


@pytest.mark.asyncio
async def test_lease_host_binds_renter_lease_not_ir(
    http_client, tenant_headers, plane_hop, tenant_a
):
    row = await _host(http_client, tenant_headers, purpose="lease")
    lease_id = uuid.uuid4()
    plane_hop.add_lease(
        lease_id,
        device_id=FYBER_AGX_DEVICE_ID,
        purpose="renter",
        tenant_id=tenant_a,
    )
    bound = await http_client.post(
        f"/api/v1/aiuditor/connections/{row['id']}/assign",
        headers=tenant_headers,
        json={"lease_id": str(lease_id)},
    )
    assert bound.status_code == 200, bound.text
    assert bound.json()["lease_id"] == str(lease_id)

    ir_lease = uuid.uuid4()
    plane_hop.add_lease(
        ir_lease,
        device_id=FYBER_AGX_DEVICE_ID,
        purpose="ir",
        tenant_id=tenant_a,
    )
    auditor = await _host(http_client, tenant_headers, purpose="auditor")
    refused = await http_client.post(
        f"/api/v1/aiuditor/connections/{auditor['id']}/assign",
        headers=tenant_headers,
        json={"lease_id": str(ir_lease)},
    )
    assert refused.status_code == 409
    assert refused.json()["detail"] == LEASE_ASSIGN_ONLY


@pytest.mark.asyncio
async def test_lease_assign_refuses_ir_ticket_and_other_device(
    http_client, tenant_headers, plane_hop, tenant_a
):
    row = await _host(http_client, tenant_headers, purpose="lease")
    ir_lease = uuid.uuid4()
    plane_hop.add_lease(
        ir_lease,
        device_id=FYBER_AGX_DEVICE_ID,
        purpose="ir",
        tenant_id=tenant_a,
    )
    refused = await http_client.post(
        f"/api/v1/aiuditor/connections/{row['id']}/assign",
        headers=tenant_headers,
        json={"lease_id": str(ir_lease)},
    )
    assert refused.status_code == 409
    assert refused.json()["detail"] == LEASE_IS_AUDITOR

    other_device = uuid.uuid4()
    other_lease = uuid.uuid4()
    plane_hop.add_lease(
        other_lease,
        device_id=other_device,
        purpose="renter",
        tenant_id=tenant_a,
    )
    mismatch = await http_client.post(
        f"/api/v1/aiuditor/connections/{row['id']}/assign",
        headers=tenant_headers,
        json={"lease_id": str(other_lease)},
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"] == LEASE_DEVICE_MISMATCH

    missing = await http_client.post(
        f"/api/v1/aiuditor/connections/{row['id']}/assign",
        headers=tenant_headers,
        json={"lease_id": str(uuid.uuid4())},
    )
    assert missing.status_code == 404
    assert missing.json()["detail"] == LEASE_NOT_FOUND


@pytest.mark.asyncio
async def test_lease_host_cannot_mint_ticket(http_client, tenant_headers):
    row = await _host(http_client, tenant_headers, purpose="lease")
    refused = await http_client.post(
        f"/api/v1/aiuditor/connections/{row['id']}/ticket",
        headers=tenant_headers,
        json={},
    )
    assert refused.status_code == 409
    assert refused.json()["detail"] == HOST_TICKET_ONLY


@pytest.mark.asyncio
async def test_mint_fail_closes_without_plane(event_sink, tenant_headers, host_lookup):
    import httpx
    from asgi_lifespan import LifespanManager

    app = create_app(
        test_event_sink=event_sink,
        enable_db_middleware=False,
        host_lookup=host_lookup,
        plane_hop=ClosedPlaneHop(),
        provider_hop=FakeProviderHop(),
    )
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            row = await _host(client, tenant_headers, purpose="auditor")
            refused = await client.post(
                f"/api/v1/aiuditor/connections/{row['id']}/ticket",
                headers=tenant_headers,
                json={},
            )
    assert refused.status_code == 503


@pytest.mark.asyncio
async def test_provider_classify_does_not_reuse_renter_or_store_prompt(
    http_client, tenant_headers, provider_hop, event_sink
):
    row = await _provider(http_client, tenant_headers)
    prompt = "secret classify prompt must not be evented"
    classified = await http_client.post(
        f"/api/v1/aiuditor/connections/{row['id']}/classify",
        headers={**tenant_headers, "X-Provider-Authorization": "Bearer one-shot"},
        json={"messages": [{"role": "user", "content": prompt}]},
    )
    assert classified.status_code == 200, classified.text
    assert classified.json() == {"upstream_status": 200}
    assert prompt not in classified.text
    assert prompt not in str(event_sink)
    assert provider_hop.calls[0]["authorization"] == "Bearer one-shot"
    assert provider_hop.calls[0]["message_count"] == 1
    ready = await http_client.get(
        f"/api/v1/aiuditor/connections/{row['id']}",
        headers=tenant_headers,
    )
    assert ready.json()["status"] == "ready"

    host = await _host(http_client, tenant_headers, purpose="auditor")
    refused = await http_client.post(
        f"/api/v1/aiuditor/connections/{host['id']}/classify",
        headers=tenant_headers,
        json={"messages": [{"role": "user", "content": prompt}]},
    )
    assert refused.status_code == 409
    assert refused.json()["detail"] == PROVIDER_CLASSIFY_ONLY


@pytest.mark.asyncio
async def test_renter_exchange_cannot_use_connection(
    http_client, tenant_headers, tenant_a
):
    from api.infrastructure.security.api_key_exchange_jwt import mint_api_key_session_jwt

    row = await _host(http_client, tenant_headers, purpose="auditor")
    token = mint_api_key_session_jwt(
        key_record_id=uuid.uuid4(),
        tenant_id=tenant_a,
        scopes=[],
        purpose="renter",
    )
    denied = await http_client.post(
        f"/api/v1/aiuditor/connections/{row['id']}/ticket",
        headers={
            "X-Tenant-ID": str(tenant_a),
            "Authorization": f"Bearer {token}",
        },
        json={},
    )
    assert denied.status_code == 401
    assert denied.json()["detail"] == AUDITOR_ACTOR_REQUIRED


@pytest.mark.asyncio
async def test_mint_maps_renter_lease_conflict(http_client, tenant_headers, plane_hop):
    plane_hop.mint_error = "device has a renter lease"
    row = await _host(http_client, tenant_headers, purpose="auditor")
    refused = await http_client.post(
        f"/api/v1/aiuditor/connections/{row['id']}/ticket",
        headers=tenant_headers,
        json={},
    )
    assert refused.status_code == 409
    assert refused.json()["detail"] == "device has a renter lease"


@pytest.mark.asyncio
async def test_disabled_connection_cannot_be_used(http_client, tenant_headers, app, tenant_a):
    row = await _host(http_client, tenant_headers, purpose="auditor")
    stored = app.state.connection_store.get(tenant_a, uuid.UUID(row["id"]))
    assert stored is not None
    app.state.connection_store.put(
        stored.model_copy(update={"status": ConnectionStatus.DISABLED})
    )
    refused = await http_client.post(
        f"/api/v1/aiuditor/connections/{row['id']}/ticket",
        headers=tenant_headers,
        json={},
    )
    assert refused.status_code == 409
    assert refused.json()["detail"] == CONNECTION_DISABLED


@pytest.mark.asyncio
async def test_lease_already_assigned_to_another_row(
    http_client, tenant_headers, plane_hop, app, tenant_a
):
    from datetime import UTC, datetime

    lease_id = uuid.uuid4()
    plane_hop.add_lease(
        lease_id,
        device_id=FYBER_AGX_DEVICE_ID,
        purpose="renter",
        tenant_id=tenant_a,
    )
    other_id = uuid.uuid4()
    app.state.connection_store.put(
        Connection(
            id=other_id,
            tenant_id=tenant_a,
            kind=ConnectionKind.HOST,
            purpose=ConnectionPurpose.LEASE,
            device_id=uuid.uuid4(),
            lease_id=lease_id,
            status=ConnectionStatus.READY,
            created_at=datetime.now(UTC),
        )
    )
    row = await _host(http_client, tenant_headers, purpose="lease")
    refused = await http_client.post(
        f"/api/v1/aiuditor/connections/{row['id']}/assign",
        headers=tenant_headers,
        json={"lease_id": str(lease_id)},
    )
    assert refused.status_code == 409
    assert refused.json()["detail"] == LEASE_ALREADY_ASSIGNED


@pytest.mark.asyncio
async def test_classify_fail_closes_on_provider_error(
    http_client, tenant_headers, provider_hop
):
    provider_hop.error = "provider hop failed"
    row = await _provider(http_client, tenant_headers)
    refused = await http_client.post(
        f"/api/v1/aiuditor/connections/{row['id']}/classify",
        headers=tenant_headers,
        json={"messages": []},
    )
    assert refused.status_code == 503
