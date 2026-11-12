from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from aiuditor_service.approvals import hold_approval
from aiuditor_service.contracts import put_contract, sign_contract
from aiuditor_service.domain.models import (
    ApprovalCreate,
    ApprovalWhy,
    ContractCreate,
    ContractParty,
    ContractViolation,
    ViolationAction,
    ViolationKind,
)
from aiuditor_service.export import EXPORT_BODIES_REFUSED, _reject_bodies, build_grant_export
from aiuditor_service.store import ApprovalStore, ContractStore, ViolationStore
from conftest import tenant_session_headers

_UNTIL = datetime(2026, 12, 1, tzinfo=UTC)
_NOW = datetime(2026, 9, 20, 18, 0, tzinfo=UTC)
_BANNED = ("prompt", "ticket", "credential", "messages", "hmac", "completion")


def _walk_keys(value, found: set[str] | None = None) -> set[str]:
    keys = found if found is not None else set()
    if isinstance(value, dict):
        for key, item in value.items():
            keys.add(str(key))
            _walk_keys(item, keys)
    elif isinstance(value, list):
        for item in value:
            _walk_keys(item, keys)
    return keys


def _signed(store: ContractStore, tenant: uuid.UUID):
    row = put_contract(
        store,
        tenant,
        ContractCreate(
            parties=[
                ContractParty(role="renter", subject="renter@example.com"),
                ContractParty(role="host", subject="agx-host"),
            ],
            meters=["wall_time"],
            until=_UNTIL,
        ),
        now=_NOW,
    )
    return sign_contract(
        store,
        tenant,
        row.id,
        signed_by="11111111-1111-1111-1111-111111111111",
        now=_NOW,
    )


def test_grant_export_is_lock_fields_only():
    tenant = uuid.uuid4()
    contracts = ContractStore()
    violations = ViolationStore()
    approvals = ApprovalStore()
    signed = _signed(contracts, tenant)
    violations.put(
        ContractViolation(
            id=uuid.uuid4(),
            tenant_id=tenant,
            contract_id=signed.id,
            kind=ViolationKind.TERM,
            action=ViolationAction.REFUND,
            reason="until passed",
            created_at=_NOW,
        )
    )
    hold_approval(
        approvals,
        tenant,
        ApprovalCreate(who="renter-key", why=ApprovalWhy.COMPLETION),
        now=_NOW,
    )
    payload = build_grant_export(
        tenant_id=tenant,
        contracts=contracts,
        violations=violations,
        approvals=approvals,
        now=_NOW,
    )
    dumped = payload.model_dump(mode="json")
    assert dumped["tenant_id"] == str(tenant)
    assert len(dumped["contracts"]) == 1
    assert "rail" not in dumped["contracts"][0]
    assert dumped["contracts"][0]["accounting_backend"] == "internal"
    assert dumped["violations"][0]["kind"] == "term"
    assert dumped["approvals"][0]["who"] == "renter-key"
    keys = _walk_keys(dumped)
    for banned in _BANNED:
        assert banned not in keys
    assert "ticket" not in keys


def test_reject_bodies_fail_closes():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _reject_bodies({"contracts": [{"prompt": "secret"}]})
    assert exc.value.detail == EXPORT_BODIES_REFUSED


@pytest.mark.asyncio
async def test_export_http_and_renter_refused(
    http_client, tenant_headers, tenant_a, tenant_b
):
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
    held = await http_client.post(
        "/api/v1/aiuditor/approvals",
        headers=tenant_headers,
        json={"who": "renter-key", "why": "completion"},
    )
    assert held.status_code == 201, held.text

    exported = await http_client.get("/api/v1/aiuditor/export", headers=tenant_headers)
    assert exported.status_code == 200, exported.text
    body = exported.json()
    assert body["tenant_id"] == str(tenant_a)
    assert body["contracts"][0]["id"] == created.json()["id"]
    assert body["approvals"][0]["who"] == "renter-key"
    keys = _walk_keys(body)
    for banned in _BANNED:
        assert banned not in keys

    other = await http_client.get(
        "/api/v1/aiuditor/export",
        headers=tenant_session_headers(tenant_b),
    )
    assert other.status_code == 200, other.text
    assert other.json()["contracts"] == []
    assert other.json()["approvals"] == []

    token = mint_api_key_session_jwt(
        key_record_id=uuid.uuid4(),
        tenant_id=tenant_a,
        scopes=[],
        purpose="renter",
    )
    denied = await http_client.get(
        "/api/v1/aiuditor/export",
        headers={
            "X-Tenant-ID": str(tenant_a),
            "Authorization": f"Bearer {token}",
        },
    )
    assert denied.status_code == 401


@pytest.mark.asyncio
async def test_export_is_on_openapi_not_site_inbox(http_client):
    spec = await http_client.get("/openapi.json")
    paths = spec.json()["paths"]
    assert "/api/v1/aiuditor/export" in paths
    assert "/api/v1/auditor/v0/tickets" not in paths
    assert "/api/v1/grants/v0/grants" not in paths
