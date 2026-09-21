from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from aiuditor_service.catalog import SERVICE_SIBLING, SIBLING_TOKENS
from aiuditor_service.contracts import CONTRACT_NOT_SIGNED
from aiuditor_service.domain.models import (
    CatalogService,
    ContractStatus,
    ServiceBackend,
)
from conftest import tenant_session_headers

_UNTIL = datetime(2026, 12, 1, tzinfo=UTC)


def test_catalog_backends_are_not_sibling_products():
    assert {item.value for item in ServiceBackend} == {
        "hypermesh_class",
        "hypermesh_catalog",
        "provider",
    }
    assert SIBLING_TOKENS.isdisjoint({item.value for item in ServiceBackend})


@pytest.mark.asyncio
async def test_planted_sibling_row_cannot_allocate(
    http_client, tenant_headers, app, tenant_a
):
    planted = CatalogService(
        id=uuid.uuid4(),
        tenant_id=tenant_a,
        title="turn",
        backend=ServiceBackend.HYPERMESH_CLASS,
        class_id="agx-large",
        meters=["wall_time", "capacity_block"],
        created_at=datetime.now(UTC),
    )
    app.state.catalog_store.put(planted)
    allocated = await http_client.post(
        f"/api/v1/aiuditor/catalog/{planted.id}/allocate",
        headers=tenant_headers,
        json={"renter": "acme-renter"},
    )
    assert allocated.status_code == 400
    assert allocated.json()["detail"] == SERVICE_SIBLING


@pytest.mark.asyncio
async def test_signed_contract_is_the_sibling_grant(
    http_client, tenant_headers, tenant_b
):
    created = await http_client.post(
        "/api/v1/aiuditor/contracts",
        headers=tenant_headers,
        json={
            "parties": [
                {"role": "renter", "subject": "renter@example.com"},
                {"role": "host", "subject": "agx-host"},
            ],
            "meters": ["wall_time", "capacity_block"],
            "rail": "stripe",
            "until": _UNTIL.isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    contract_id = created.json()["id"]
    draft = await http_client.get(
        f"/api/v1/aiuditor/contracts/{contract_id}/grant",
        headers=tenant_headers,
    )
    assert draft.status_code == 409
    assert draft.json()["detail"] == CONTRACT_NOT_SIGNED

    signed = await http_client.post(
        f"/api/v1/aiuditor/contracts/{contract_id}/sign",
        headers=tenant_headers,
    )
    assert signed.status_code == 200, signed.text
    grant = await http_client.get(
        f"/api/v1/aiuditor/contracts/{contract_id}/grant",
        headers=tenant_headers,
    )
    assert grant.status_code == 200, grant.text
    body = grant.json()
    assert body["id"] == contract_id
    assert body["status"] == ContractStatus.SIGNED.value
    assert body["rail"] == "stripe"
    assert body["meters"] == ["wall_time", "capacity_block"]
    assert body["writer"] == "market"
    assert "signed_by" not in body
    assert "parties" not in body
    assert "messages" not in str(body)

    other = await http_client.get(
        f"/api/v1/aiuditor/contracts/{contract_id}/grant",
        headers=tenant_session_headers(tenant_b),
    )
    assert other.status_code == 404


@pytest.mark.asyncio
async def test_renter_exchange_cannot_read_sibling_grant(http_client, tenant_headers, tenant_a):
    from api.infrastructure.security.api_key_exchange_jwt import mint_api_key_session_jwt

    created = await http_client.post(
        "/api/v1/aiuditor/contracts",
        headers=tenant_headers,
        json={
            "parties": [
                {"role": "renter", "subject": "renter@example.com"},
                {"role": "host", "subject": "agx-host"},
            ],
            "meters": ["wall_time"],
            "rail": "stripe",
            "until": _UNTIL.isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    token = mint_api_key_session_jwt(
        key_record_id=uuid.uuid4(),
        tenant_id=tenant_a,
        scopes=[],
        purpose="renter",
    )
    denied = await http_client.get(
        f"/api/v1/aiuditor/contracts/{created.json()['id']}/grant",
        headers={
            "X-Tenant-ID": str(tenant_a),
            "Authorization": f"Bearer {token}",
        },
    )
    assert denied.status_code == 401
