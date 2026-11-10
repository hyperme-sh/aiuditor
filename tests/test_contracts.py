from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from aiuditor_service.contracts import (
    CHECKOUT_SIGNER,
    CONTRACT_ALREADY_BOUND,
    CONTRACT_METER,
    CONTRACT_NOT_DRAFT,
    CONTRACT_NOT_SIGNED,
    CONTRACT_RAIL,
    CONTRACT_UNTIL_PAST,
    LEASE_ALREADY_BOUND,
    bind_contract,
    fulfill_checkout_contract,
    put_contract,
    sign_contract,
)
from aiuditor_service.domain.models import (
    ContractBind,
    ContractCreate,
    ContractFulfill,
    ContractParty,
    ContractStatus,
)
from aiuditor_service.events import (
    CONTRACT_BOUND_V1,
    CONTRACT_CREATED_V1,
    CONTRACT_FULFILLED_V1,
    CONTRACT_SIGNED_V1,
)
from aiuditor_service.store import ContractStore
from conftest import tenant_session_headers

_UNTIL = datetime(2026, 12, 1, tzinfo=UTC)


def _draft(**overrides) -> ContractCreate:
    body = {
        "parties": [
            ContractParty(role="renter", subject="renter@example.com"),
            ContractParty(role="host", subject="agx-host"),
        ],
        "meters": ["wall_time", "capacity_block"],
        "rail": "stripe",
        "until": _UNTIL,
    }
    body.update(overrides)
    return ContractCreate(**body)


def test_unknown_meter_and_rail_are_refused():
    store = ContractStore()
    tenant = uuid.uuid4()
    with pytest.raises(HTTPException) as meter:
        put_contract(
            store,
            tenant,
            _draft(meters=["tokens"]),
        )
    assert meter.value.detail == CONTRACT_METER
    with pytest.raises(HTTPException) as rail:
        put_contract(store, tenant, _draft(rail="usdc"))
    assert rail.value.detail == CONTRACT_RAIL


def test_until_in_the_past_is_refused():
    store = ContractStore()
    with pytest.raises(HTTPException) as exc:
        put_contract(
            store,
            uuid.uuid4(),
            _draft(until=datetime(2020, 1, 1, tzinfo=UTC)),
            now=datetime(2026, 9, 20, tzinfo=UTC),
        )
    assert exc.value.detail == CONTRACT_UNTIL_PAST


def test_prompt_shaped_party_is_stripped():
    store = ContractStore()
    with pytest.raises(HTTPException):
        put_contract(
            store,
            uuid.uuid4(),
            _draft(
                parties=[
                    ContractParty(role="renter", subject="user prompt: do the thing"),
                    ContractParty(role="host", subject="agx-host"),
                ]
            ),
        )


def test_bind_requires_signed_and_is_one_shot():
    store = ContractStore()
    tenant = uuid.uuid4()
    row = put_contract(store, tenant, _draft())
    lease = uuid.uuid4()
    with pytest.raises(HTTPException) as draft:
        bind_contract(store, tenant, row.id, ContractBind(lease_id=lease))
    assert draft.value.detail == CONTRACT_NOT_SIGNED
    signed = sign_contract(
        store,
        tenant,
        row.id,
        signed_by="11111111-1111-1111-1111-111111111111",
    )
    assert signed.status is ContractStatus.SIGNED
    bound = bind_contract(store, tenant, row.id, ContractBind(lease_id=lease))
    assert bound.lease_id == lease
    with pytest.raises(HTTPException) as again:
        bind_contract(store, tenant, row.id, ContractBind(lease_id=uuid.uuid4()))
    assert again.value.detail == CONTRACT_ALREADY_BOUND
    with pytest.raises(HTTPException) as resign:
        sign_contract(
            store,
            tenant,
            row.id,
            signed_by="11111111-1111-1111-1111-111111111111",
        )
    assert resign.value.detail == CONTRACT_NOT_DRAFT
    other = put_contract(store, tenant, _draft())
    signed_other = sign_contract(
        store,
        tenant,
        other.id,
        signed_by="11111111-1111-1111-1111-111111111111",
    )
    with pytest.raises(HTTPException) as taken:
        bind_contract(
            store, tenant, signed_other.id, ContractBind(lease_id=lease)
        )
    assert taken.value.detail == LEASE_ALREADY_BOUND


def test_checkout_auto_signs_and_binds_a_lease():
    store = ContractStore()
    tenant = uuid.uuid4()
    lease = uuid.uuid4()
    row = fulfill_checkout_contract(
        store,
        tenant,
        ContractFulfill(
            lease_id=lease,
            reserved_hours=2,
            renter_subject="renter@example.com",
            host_subject="agx-host",
        ),
        now=datetime(2026, 9, 20, tzinfo=UTC),
    )
    assert row.status is ContractStatus.SIGNED
    assert row.signed_by == CHECKOUT_SIGNER
    assert row.lease_id == lease
    assert row.rail == "stripe"
    assert "wall_time" in row.meters
    assert row.until == datetime(2026, 9, 20, 2, tzinfo=UTC)


def test_checkout_can_bind_a_human_signed_contract():
    store = ContractStore()
    tenant = uuid.uuid4()
    draft = put_contract(store, tenant, _draft())
    signed = sign_contract(
        store, tenant, draft.id, signed_by="11111111-1111-1111-1111-111111111111"
    )
    lease = uuid.uuid4()
    bound = fulfill_checkout_contract(
        store,
        tenant,
        ContractFulfill(
            lease_id=lease,
            contract_id=signed.id,
            renter_subject="renter@example.com",
            host_subject="agx-host",
        ),
    )
    assert bound.id == signed.id
    assert bound.lease_id == lease


@pytest.mark.asyncio
async def test_operator_can_draft_sign_and_bind(
    http_client, tenant_headers, event_sink
):
    created = await http_client.post(
        "/api/v1/aiuditor/contracts",
        headers=tenant_headers,
        json={
            "parties": [
                {"role": "renter", "subject": "renter@example.com"},
                {"role": "host", "subject": "agx-host"},
            ],
            "meters": ["wall_time", "prompt_digest_count"],
            "rail": "stripe",
            "until": _UNTIL.isoformat(),
            "dispute_tip": "receipt-tip-1",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "draft"
    assert body["rail"] == "stripe"
    assert body["meters"] == ["wall_time", "prompt_digest_count"]
    assert body["dispute_tip"] == "receipt-tip-1"
    assert "prompt" not in body["dispute_tip"]
    assert CONTRACT_CREATED_V1 in [e["event_type"] for e in event_sink]
    assert "messages" not in str(event_sink)

    signed = await http_client.post(
        f"/api/v1/aiuditor/contracts/{body['id']}/sign",
        headers=tenant_headers,
    )
    assert signed.status_code == 200, signed.text
    assert signed.json()["status"] == "signed"
    assert signed.json()["signed_by"] == "11111111-1111-1111-1111-111111111111"
    assert CONTRACT_SIGNED_V1 in [e["event_type"] for e in event_sink]

    lease = uuid.uuid4()
    bound = await http_client.post(
        f"/api/v1/aiuditor/contracts/{body['id']}/bind",
        headers=tenant_headers,
        json={"lease_id": str(lease)},
    )
    assert bound.status_code == 200, bound.text
    assert bound.json()["lease_id"] == str(lease)
    assert CONTRACT_BOUND_V1 in [e["event_type"] for e in event_sink]


@pytest.mark.asyncio
async def test_internal_can_fulfill_checkout_contract(
    http_client, tenant_a, internal_token, event_sink
):
    lease = uuid.uuid4()
    fulfilled = await http_client.post(
        "/api/v1/aiuditor/contracts/fulfill",
        headers={
            "X-Tenant-ID": str(tenant_a),
            "X-Internal-Token": internal_token,
        },
        json={
            "lease_id": str(lease),
            "reserved_hours": 3,
            "renter_subject": "renter@example.com",
            "host_subject": "agx-host",
        },
    )
    assert fulfilled.status_code == 201, fulfilled.text
    body = fulfilled.json()
    assert body["status"] == "signed"
    assert body["signed_by"] == CHECKOUT_SIGNER
    assert body["lease_id"] == str(lease)
    assert CONTRACT_FULFILLED_V1 in [e["event_type"] for e in event_sink]


@pytest.mark.asyncio
async def test_operator_cannot_use_checkout_fulfill(http_client, tenant_headers):
    refused = await http_client.post(
        "/api/v1/aiuditor/contracts/fulfill",
        headers=tenant_headers,
        json={
            "lease_id": str(uuid.uuid4()),
            "renter_subject": "renter@example.com",
            "host_subject": "agx-host",
        },
    )
    assert refused.status_code == 401


@pytest.fixture
def internal_token(monkeypatch):
    monkeypatch.setenv("AIUDITOR_INTERNAL_TOKEN", "test-aiuditor-internal-token")
    return "test-aiuditor-internal-token"


@pytest.mark.asyncio
async def test_internal_can_read_contract_not_write(
    http_client, tenant_headers, tenant_a, internal_token
):
    created = await http_client.post(
        "/api/v1/aiuditor/contracts",
        headers=tenant_headers,
        json={
            "parties": [
                {"role": "auditor", "subject": "ir-op"},
                {"role": "host", "subject": "agx-host"},
            ],
            "meters": ["capacity_block"],
            "until": _UNTIL.isoformat(),
        },
    )
    assert created.status_code == 201
    listed = await http_client.get(
        "/api/v1/aiuditor/contracts",
        headers={
            "X-Tenant-ID": str(tenant_a),
            "X-Internal-Token": internal_token,
        },
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    refused = await http_client.post(
        "/api/v1/aiuditor/contracts",
        headers={
            "X-Tenant-ID": str(tenant_a),
            "X-Internal-Token": internal_token,
        },
        json={
            "parties": [
                {"role": "renter", "subject": "x"},
                {"role": "host", "subject": "y"},
            ],
            "meters": ["wall_time"],
            "until": _UNTIL.isoformat(),
        },
    )
    assert refused.status_code == 401
    sign_refused = await http_client.post(
        f"/api/v1/aiuditor/contracts/{created.json()['id']}/sign",
        headers={
            "X-Tenant-ID": str(tenant_a),
            "X-Internal-Token": internal_token,
        },
    )
    assert sign_refused.status_code == 401


@pytest.mark.asyncio
async def test_renter_exchange_cannot_draft(http_client, tenant_a):
    from api.infrastructure.security.api_key_exchange_jwt import mint_api_key_session_jwt

    token = mint_api_key_session_jwt(
        key_record_id=uuid.uuid4(),
        tenant_id=tenant_a,
        scopes=[],
        purpose="renter",
    )
    denied = await http_client.post(
        "/api/v1/aiuditor/contracts",
        headers={
            "X-Tenant-ID": str(tenant_a),
            "Authorization": f"Bearer {token}",
        },
        json={
            "parties": [
                {"role": "renter", "subject": "x"},
                {"role": "host", "subject": "y"},
            ],
            "meters": ["wall_time"],
            "until": _UNTIL.isoformat(),
        },
    )
    assert denied.status_code == 401


@pytest.mark.asyncio
async def test_contracts_are_tenant_scoped(http_client, tenant_headers, tenant_b):
    await http_client.post(
        "/api/v1/aiuditor/contracts",
        headers=tenant_headers,
        json={
            "parties": [
                {"role": "renter", "subject": "alice"},
                {"role": "host", "subject": "box"},
            ],
            "meters": ["wall_time"],
            "until": _UNTIL.isoformat(),
        },
    )
    other = await http_client.get(
        "/api/v1/aiuditor/contracts",
        headers=tenant_session_headers(tenant_b),
    )
    assert other.status_code == 200
    assert other.json() == []


@pytest.mark.asyncio
async def test_missing_contract_is_404(http_client, tenant_headers):
    missing = await http_client.get(
        f"/api/v1/aiuditor/contracts/{uuid.uuid4()}",
        headers=tenant_headers,
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_site_inbox_is_not_on_contract_paths(http_client):
    spec = await http_client.get("/openapi.json")
    paths = spec.json()["paths"]
    assert "/api/v1/aiuditor/export" in paths
    assert "/api/v1/aiuditor/contracts" in paths
    assert "/api/v1/aiuditor/contracts/{contract_id}/sign" in paths
    assert "/api/v1/aiuditor/contracts/{contract_id}/bind" in paths
    assert "/api/v1/aiuditor/contracts/fulfill" in paths
    assert "/api/v1/aiuditor/meta" in paths
    assert "/api/v1/aiuditor/hydrate" in paths
    assert "/api/v1/auditor/v0/tickets" not in paths
    assert "/api/v1/grants/v0/grants" not in paths


async def test_llm_cannot_sign():
    from fastapi import Request
    from starlette.datastructures import Headers

    from aiuditor_service.approvals import LLM_NEVER_RESOLVES
    from aiuditor_service.contracts import require_contract_signer
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
        "path": "/api/v1/aiuditor/contracts/x/sign",
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
        await require_contract_signer(Request(scope))
    assert exc.value.detail == LLM_NEVER_RESOLVES


def test_checkout_replay_reuses_owner_contract_and_refuses_changed_authority():
    store = ContractStore()
    tenant = uuid.uuid4()
    body = ContractFulfill(lease_id=uuid.uuid4(), renter_subject='renter', host_subject='host')
    first = fulfill_checkout_contract(store, tenant, body)
    assert fulfill_checkout_contract(store, tenant, body).id == first.id
    for change in ({'renter_subject':'other'}, {'host_subject':'other'}, {'reserved_hours':2},
                   {'meters':['token_digest_count']}, {'dispute_tip':'changed'},
                   {'connection_id':uuid.uuid4()}, {'contract_id':uuid.uuid4()}):
        with pytest.raises(HTTPException) as refused:
            fulfill_checkout_contract(store, tenant, body.model_copy(update=change))
        assert refused.value.status_code == 409
    assert len(store.list_for(tenant)) == 1
    assert fulfill_checkout_contract(store, uuid.uuid4(), body).id != first.id


def test_named_checkout_contract_requires_matching_parties_and_live_signature():
    store = ContractStore()
    tenant = uuid.uuid4()
    row = sign_contract(store, tenant, put_contract(store, tenant, _draft()).id, signed_by='human')
    body = ContractFulfill(lease_id=uuid.uuid4(), contract_id=row.id, renter_subject='attacker', host_subject='agx-host')
    with pytest.raises(HTTPException):
        fulfill_checkout_contract(store, tenant, body)
    assert store.get(tenant, row.id).lease_id is None
    good = body.model_copy(update={'renter_subject':'renter@example.com'})
    assert fulfill_checkout_contract(store, tenant, good).id == row.id
    assert fulfill_checkout_contract(store, tenant, good).id == row.id
    with pytest.raises(HTTPException):
        fulfill_checkout_contract(store, tenant, good, now=datetime(2027, 1, 1, tzinfo=UTC))


@pytest.mark.asyncio
async def test_http_fulfillment_reply_can_be_retried_without_duplicate_binding(http_client, tenant_a, internal_token):
    internal_headers = {"X-Tenant-ID": str(tenant_a), "X-Internal-Token": internal_token}
    body = {'lease_id': str(uuid.uuid4()), 'renter_subject': 'renter', 'host_subject': 'host'}
    first = await http_client.post('/api/v1/aiuditor/contracts/fulfill', headers=internal_headers, json=body)
    assert first.status_code == 201, first.text
    retry = await http_client.post('/api/v1/aiuditor/contracts/fulfill', headers=internal_headers, json=body)
    assert retry.status_code == 201, retry.text
    assert retry.json() == first.json()
    changed = await http_client.post('/api/v1/aiuditor/contracts/fulfill', headers=internal_headers, json={**body, 'host_subject':'attacker'})
    assert changed.status_code == 409


def test_public_draft_cannot_claim_a_lease_identifier():
    from aiuditor_service.domain.models import ContractCreate
    store = ContractStore()
    with pytest.raises(HTTPException) as refused:
        put_contract(store, uuid.uuid4(), _draft(lease_id=uuid.uuid4()))
    assert refused.value.status_code == 400
    assert store.contracts == {}


def test_named_contract_cannot_authorize_a_longer_reserved_window():
    from datetime import timedelta
    store = ContractStore()
    tenant = uuid.uuid4()
    now = datetime.now(UTC)
    row = sign_contract(store, tenant, put_contract(store, tenant, _draft(until=now+timedelta(hours=1))).id, signed_by='human')
    with pytest.raises(HTTPException) as refused:
        fulfill_checkout_contract(store, tenant, ContractFulfill(lease_id=uuid.uuid4(), contract_id=row.id,
            renter_subject='renter@example.com', host_subject='agx-host', reserved_hours=720))
    assert refused.value.status_code == 409
    assert store.get(tenant, row.id).lease_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("rail", ["stripe", "usdc"])
async def test_paid_checkout_scope_and_rail(http_client, tenant_a, internal_token, monkeypatch, event_sink, rail):
    monkeypatch.setenv("AIUDITOR_PAID_CHECKOUT_RAIL", rail)
    key = 'paid-checkout-only-' + 'a' * 32
    monkeypatch.setenv('AIUDITOR_PAID_CHECKOUT_TOKEN', key)
    monkeypatch.setenv('AIUDITOR_PAID_CHECKOUT_TENANT_ID', str(tenant_a))
    path = '/api/v1/aiuditor/contracts/paid/fulfill'
    body = dict(lease_id=str(uuid.uuid4()), reserved_hours=1, renter_subject='renter', host_subject='host')
    headers = {'X-Tenant-ID': str(tenant_a), 'X-Internal-Token': key}
    assert (await http_client.post(path, headers=headers | {'X-Internal-Token':internal_token}, json=body)).status_code == 401
    assert (await http_client.post(path, headers=headers | {'X-Tenant-ID':str(uuid.uuid4())}, json=body)).status_code == 403
    first = await http_client.post(path, headers=headers, json=body)
    assert first.status_code == 201, first.text
    assert first.json()['rail'] == rail
    assert (await http_client.post(path, headers=headers, json=body)).json()['id'] == first.json()['id']
    assert (await http_client.post('/api/v1/aiuditor/contracts/fulfill',headers=headers,json=body)).status_code == 401
