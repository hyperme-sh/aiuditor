from __future__ import annotations

import uuid

import pytest

from aiuditor_service.catalog import (
    NO_LIVE_CONNECTION,
    PROVIDER_NOT_READY,
)
from aiuditor_service.events import CATALOG_SERVICE_ALLOCATED_V1
from conftest import FYBER_AGX_DEVICE_ID, tenant_session_headers


async def _catalog_p1(http_client, headers):
    created = await http_client.post(
        "/api/v1/aiuditor/catalog",
        headers=headers,
        json={
            "title": "AGX reserved box",
            "backend": "hypermesh_class",
            "class_id": "agx-large",
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


async def _lease_host(http_client, headers, device_id):
    created = await http_client.post(
        "/api/v1/aiuditor/connections",
        headers=headers,
        json={"kind": "host", "purpose": "lease", "device_id": str(device_id)},
    )
    assert created.status_code == 201, created.text
    return created.json()


async def _ready_provider(http_client, headers):
    created = await http_client.post(
        "/api/v1/aiuditor/connections",
        headers=headers,
        json={
            "kind": "provider",
            "purpose": "lease",
            "endpoint": "https://llm.example/v1",
            "credential": "provider-secret",
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


@pytest.mark.asyncio
async def test_p1_allocate_picks_matching_host_and_returns_usdc_payment(
    http_client, tenant_headers, host_lookup, plane_hop, tenant_a, event_sink
):
    host_lookup.enroll(
        FYBER_AGX_DEVICE_ID, tenant_id=tenant_a, class_id="agx-large"
    )
    other = uuid.UUID("bbbbbbbb-1111-4111-8111-bbbbbbbbbbbb")
    host_lookup.enroll(other, tenant_id=tenant_a, class_id="agx-small")
    await _lease_host(http_client, tenant_headers, other)
    host = await _lease_host(http_client, tenant_headers, FYBER_AGX_DEVICE_ID)
    service = await _catalog_p1(http_client, tenant_headers)
    allocated = await http_client.post(
        f"/api/v1/aiuditor/catalog/{service['id']}/allocate",
        headers=tenant_headers,
        json={"renter": "acme-renter"},
    )
    assert allocated.status_code == 201, allocated.text
    body = allocated.json()
    assert body["connection_id"] == host["id"]
    assert body["backend"] == "hypermesh_class"
    assert body["purpose"] == "renter"
    assert "checkout_url" not in body
    assert body["lease_id"] is not None
    assert uuid.UUID(body["billing_record_id"])
    assert body["payment_amount_atoms"] == plane_hop.offer_amount_atoms
    assert body["payment_test_credit_available"] is False
    assert {p["subject"] for p in body["contract"]["parties"] if p["role"] == "renter"} == {"11111111-1111-1111-1111-111111111111"}
    assert body["contract"]["status"] == "signed"
    assert body["contract"]["lease_id"] == body["lease_id"]
    assert body["contract"]["connection_id"] == host["id"]
    assert plane_hop.offers[0]["class_id"] == "agx-large"
    assert plane_hop.offers[0]["device_id"] == FYBER_AGX_DEVICE_ID
    assert CATALOG_SERVICE_ALLOCATED_V1 in [e["event_type"] for e in event_sink]
    payload = [e["payload"] for e in event_sink if e["event_type"] == CATALOG_SERVICE_ALLOCATED_V1][0]
    assert "ticket" not in payload
    assert "checkout_url" not in payload
    assert "billing_record_id" not in payload
    assert "messages" not in str(event_sink)


@pytest.mark.asyncio
async def test_p2_allocate_matches_loaded_catalog(
    http_client, tenant_headers, host_lookup, plane_hop, tenant_a
):
    device = uuid.UUID("cccccccc-1111-4111-8111-cccccccccccc")
    host_lookup.enroll(
        device,
        tenant_id=tenant_a,
        class_id="agx-large",
        loaded_catalog_id="llama-3.1-8b-q4",
    )
    host = await _lease_host(http_client, tenant_headers, device)
    service = await http_client.post(
        "/api/v1/aiuditor/catalog",
        headers=tenant_headers,
        json={
            "title": "Llama 8B",
            "backend": "hypermesh_catalog",
            "catalog_id": "llama-3.1-8b-q4",
        },
    )
    assert service.status_code == 201, service.text
    allocated = await http_client.post(
        f"/api/v1/aiuditor/catalog/{service.json()['id']}/allocate",
        headers=tenant_headers,
        json={"renter": "acme-renter"},
    )
    assert allocated.status_code == 201, allocated.text
    assert allocated.json()["connection_id"] == host["id"]
    assert allocated.json()["backend"] == "hypermesh_catalog"
    assert plane_hop.offers[0]["kind"] == "p2_loaded_model"
    assert plane_hop.offers[0]["catalog_id"] == "llama-3.1-8b-q4"


@pytest.mark.asyncio
async def test_provider_allocate_signs_without_a_hypermesh_lease(
    http_client, tenant_headers, plane_hop
):
    provider = await _ready_provider(http_client, tenant_headers)
    service = await http_client.post(
        "/api/v1/aiuditor/catalog",
        headers=tenant_headers,
        json={
            "title": "Hosted chat",
            "backend": "provider",
            "connection_id": provider["id"],
        },
    )
    assert service.status_code == 201, service.text
    allocated = await http_client.post(
        f"/api/v1/aiuditor/catalog/{service.json()['id']}/allocate",
        headers=tenant_headers,
        json={"renter": "acme-renter"},
    )
    assert allocated.status_code == 201, allocated.text
    body = allocated.json()
    assert body["connection_id"] == provider["id"]
    assert body["lease_id"] is None
    assert body["billing_record_id"] is None
    assert body["payment_amount_atoms"] is None
    assert body["payment_test_credit_available"] is False
    assert body["purpose"] == "lease"
    assert body["contract"]["status"] == "signed"
    assert plane_hop.offers == []


@pytest.mark.asyncio
async def test_pending_provider_is_not_live(http_client, tenant_headers):
    pending = await http_client.post(
        "/api/v1/aiuditor/connections",
        headers=tenant_headers,
        json={
            "kind": "provider",
            "purpose": "lease",
            "endpoint": "https://llm.example/v1",
        },
    )
    assert pending.status_code == 201, pending.text
    service = await http_client.post(
        "/api/v1/aiuditor/catalog",
        headers=tenant_headers,
        json={
            "title": "Hosted chat",
            "backend": "provider",
            "connection_id": pending.json()["id"],
        },
    )
    assert service.status_code == 201, service.text
    allocated = await http_client.post(
        f"/api/v1/aiuditor/catalog/{service.json()['id']}/allocate",
        headers=tenant_headers,
        json={"renter": "acme-renter"},
    )
    assert allocated.status_code == 409
    assert allocated.json()["detail"] == PROVIDER_NOT_READY


@pytest.mark.asyncio
async def test_no_matching_host_is_409(
    http_client, tenant_headers, host_lookup, tenant_a
):
    host_lookup.enroll(
        FYBER_AGX_DEVICE_ID, tenant_id=tenant_a, class_id="agx-small"
    )
    await _lease_host(http_client, tenant_headers, FYBER_AGX_DEVICE_ID)
    service = await _catalog_p1(http_client, tenant_headers)
    allocated = await http_client.post(
        f"/api/v1/aiuditor/catalog/{service['id']}/allocate",
        headers=tenant_headers,
        json={"renter": "acme-renter"},
    )
    assert allocated.status_code == 409
    assert allocated.json()["detail"] == NO_LIVE_CONNECTION


@pytest.mark.asyncio
async def test_suspended_host_is_skipped(
    http_client, tenant_headers, host_lookup, tenant_a
):
    host_lookup.enroll(
        FYBER_AGX_DEVICE_ID,
        tenant_id=tenant_a,
        class_id="agx-large",
        path_b_state="suspended",
    )
    await _lease_host(http_client, tenant_headers, FYBER_AGX_DEVICE_ID)
    service = await _catalog_p1(http_client, tenant_headers)
    allocated = await http_client.post(
        f"/api/v1/aiuditor/catalog/{service['id']}/allocate",
        headers=tenant_headers,
        json={"renter": "acme-renter"},
    )
    assert allocated.status_code == 409
    assert allocated.json()["detail"] == NO_LIVE_CONNECTION


@pytest.mark.asyncio
async def test_host_allocate_passes_on_test_credit_availability(
    http_client, tenant_headers, host_lookup, plane_hop, tenant_a
):
    plane_hop.offer_test_credit_available = True
    plane_hop.offer_amount_atoms = 12_000_000
    host_lookup.enroll(
        FYBER_AGX_DEVICE_ID, tenant_id=tenant_a, class_id="agx-large"
    )
    await _lease_host(http_client, tenant_headers, FYBER_AGX_DEVICE_ID)
    service = await _catalog_p1(http_client, tenant_headers)
    allocated = await http_client.post(
        f"/api/v1/aiuditor/catalog/{service['id']}/allocate",
        headers=tenant_headers,
        json={"renter": "acme-renter"},
    )
    assert allocated.status_code == 201, allocated.text
    body = allocated.json()
    assert body["payment_amount_atoms"] == 12_000_000
    assert body["payment_test_credit_available"] is True
    assert uuid.UUID(body["billing_record_id"])


@pytest.mark.asyncio
async def test_host_with_existing_renter_lease_reuses_it(
    http_client, tenant_headers, host_lookup, plane_hop, tenant_a
):
    host_lookup.enroll(
        FYBER_AGX_DEVICE_ID, tenant_id=tenant_a, class_id="agx-large"
    )
    host = await _lease_host(http_client, tenant_headers, FYBER_AGX_DEVICE_ID)
    lease_id = uuid.uuid4()
    plane_hop.add_lease(
        lease_id,
        device_id=FYBER_AGX_DEVICE_ID,
        purpose="renter",
        tenant_id=tenant_a,
    )
    assigned = await http_client.post(
        f"/api/v1/aiuditor/connections/{host['id']}/assign",
        headers=tenant_headers,
        json={"lease_id": str(lease_id)},
    )
    assert assigned.status_code == 200, assigned.text
    service = await _catalog_p1(http_client, tenant_headers)
    allocated = await http_client.post(
        f"/api/v1/aiuditor/catalog/{service['id']}/allocate",
        headers=tenant_headers,
        json={"renter": "acme-renter"},
    )
    assert allocated.status_code == 201, allocated.text
    assert allocated.json()["lease_id"] == str(lease_id)
    assert allocated.json()["billing_record_id"] is None
    assert plane_hop.offers == []


@pytest.mark.asyncio
async def test_renter_exchange_cannot_allocate(http_client, tenant_headers, tenant_a):
    from api.infrastructure.security.api_key_exchange_jwt import mint_api_key_session_jwt

    service = await _catalog_p1(http_client, tenant_headers)
    token = mint_api_key_session_jwt(
        key_record_id=uuid.uuid4(),
        tenant_id=tenant_a,
        scopes=[],
        purpose="renter",
    )
    denied = await http_client.post(
        f"/api/v1/aiuditor/catalog/{service['id']}/allocate",
        headers={
            "X-Tenant-ID": str(tenant_a),
            "Authorization": f"Bearer {token}",
        },
        json={"renter": "acme-renter"},
    )
    assert denied.status_code == 401


@pytest.mark.asyncio
async def test_allocate_is_tenant_scoped(http_client, tenant_headers, tenant_b):
    service = await _catalog_p1(http_client, tenant_headers)
    other = await http_client.post(
        f"/api/v1/aiuditor/catalog/{service['id']}/allocate",
        headers=tenant_session_headers(tenant_b),
        json={"renter": "acme-renter"},
    )
    assert other.status_code == 404


@pytest.mark.asyncio
async def test_missing_service_allocate_is_404(http_client, tenant_headers):
    missing = await http_client.post(
        f"/api/v1/aiuditor/catalog/{uuid.uuid4()}/allocate",
        headers=tenant_headers,
        json={"renter": "acme-renter"},
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_catalog_propagates_authenticated_renter_and_custom_meters(http_client, tenant_headers, host_lookup, plane_hop, tenant_a):
    host_lookup.enroll(FYBER_AGX_DEVICE_ID, tenant_id=tenant_a, class_id='agx-large')
    await _lease_host(http_client, tenant_headers, FYBER_AGX_DEVICE_ID)
    created = await http_client.post('/api/v1/aiuditor/catalog', headers=tenant_headers,
        json={'title':'Custom metering', 'backend':'hypermesh_class', 'class_id':'agx-large', 'meters':['token_digest_count']})
    assert created.status_code == 201
    response = await http_client.post(f"/api/v1/aiuditor/catalog/{created.json()['id']}/allocate",
        headers=tenant_headers, json={'renter':'untrusted-display-name'})
    assert response.status_code == 201, response.text
    contract = response.json()['contract']
    assert {p['subject'] for p in contract['parties'] if p['role']=='renter'} == {'11111111-1111-1111-1111-111111111111'}
    assert plane_hop.offers[0]['contract_meters'] == contract['meters'] == ['token_digest_count']


@pytest.mark.asyncio
async def test_repeat_allocation_rejects_bound_connection_without_new_authority(
    http_client, app, tenant_headers, host_lookup, plane_hop, tenant_a, event_sink,
):
    host_lookup.enroll(FYBER_AGX_DEVICE_ID, tenant_id=tenant_a, class_id="agx-large")
    await _lease_host(http_client, tenant_headers, FYBER_AGX_DEVICE_ID)
    service = await _catalog_p1(http_client, tenant_headers)
    path = f"/api/v1/aiuditor/catalog/{service['id']}/allocate"
    first = await http_client.post(path, headers=tenant_headers, json={"renter": "renter"})
    assert first.status_code == 201, first.text
    before = app.state.contract_store.list_for(tenant_a)
    assert len(before) == 1
    # A reconstructed store must also reject the persisted binding.
    from aiuditor_service.store import ContractStore
    app.state.contract_store = ContractStore(persist=app.state.aiuditor_persist)
    second = await http_client.post(path, headers=tenant_headers, json={"renter": "renter"})
    assert second.status_code == 409, second.text
    assert second.json()["detail"] == NO_LIVE_CONNECTION
    assert app.state.contract_store.list_for(tenant_a) == before
    assert len(plane_hop.offers) == 1
    assert sum(e["event_type"] == CATALOG_SERVICE_ALLOCATED_V1 for e in event_sink) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("fresh_alternative", [False, True])
async def test_plane_bound_lease_is_excluded_before_contract_creation(
    http_client, app, tenant_headers, host_lookup, plane_hop, tenant_a, fresh_alternative,
):
    host_lookup.enroll(FYBER_AGX_DEVICE_ID, tenant_id=tenant_a, class_id="agx-large")
    host = await _lease_host(http_client, tenant_headers, FYBER_AGX_DEVICE_ID)
    lease_id = uuid.uuid4()
    plane_hop.add_lease(lease_id, device_id=FYBER_AGX_DEVICE_ID,
                       tenant_id=tenant_a, contract_id=uuid.uuid4())
    # Simulate another replica's contract being absent from the local store.
    row = app.state.connection_store.get(tenant_a, uuid.UUID(host['id']))
    app.state.connection_store.put(row.model_copy(update={"lease_id": lease_id}))
    if fresh_alternative:
        alternate_device = uuid.uuid4()
        host_lookup.enroll(alternate_device, tenant_id=tenant_a, class_id="agx-large")
        alternate = await _lease_host(http_client, tenant_headers, alternate_device)
    service = await _catalog_p1(http_client, tenant_headers)
    response = await http_client.post(f"/api/v1/aiuditor/catalog/{service['id']}/allocate",
                                     headers=tenant_headers, json={"renter": "renter"})
    if fresh_alternative:
        assert response.status_code == 201, response.text
        assert response.json()['connection_id'] == alternate['id']
        assert len(app.state.contract_store.list_for(tenant_a)) == 1
        assert len(plane_hop.offers) == 1
    else:
        assert response.status_code == 409, response.text
        assert app.state.contract_store.list_for(tenant_a) == []
        assert plane_hop.offers == []


@pytest.mark.asyncio
async def test_lease_bound_during_selection_is_rejected_before_signing(
    http_client, app, tenant_headers, host_lookup, plane_hop, tenant_a, monkeypatch,
):
    host_lookup.enroll(FYBER_AGX_DEVICE_ID, tenant_id=tenant_a, class_id="agx-large")
    host = await _lease_host(http_client, tenant_headers, FYBER_AGX_DEVICE_ID)
    lease_id = uuid.uuid4()
    lease = plane_hop.add_lease(lease_id, device_id=FYBER_AGX_DEVICE_ID, tenant_id=tenant_a)
    row = app.state.connection_store.get(tenant_a, uuid.UUID(host['id']))
    app.state.connection_store.put(row.model_copy(update={"lease_id": lease_id}))
    calls = 0
    async def changed_binding(tenant, requested, **kwargs):
        nonlocal calls
        from dataclasses import replace
        assert tenant == tenant_a and requested == lease_id
        calls += 1
        return lease if calls == 1 else replace(lease, contract_id=uuid.uuid4())
    monkeypatch.setattr(plane_hop, "get_lease", changed_binding)
    service = await _catalog_p1(http_client, tenant_headers)
    response = await http_client.post(f"/api/v1/aiuditor/catalog/{service['id']}/allocate",
                                     headers=tenant_headers, json={"renter": "renter"})
    assert response.status_code == 409, response.text
    assert calls == 2
    assert app.state.contract_store.list_for(tenant_a) == []
    assert plane_hop.offers == []
