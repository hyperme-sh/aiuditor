from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from aiuditor_service.contracts import put_contract, sign_contract
from aiuditor_service.domain.models import (
    Approval,
    ApprovalDecision,
    ApprovalStatus,
    ApprovalWhy,
    ContractCreate,
    ContractParty,
    ContractStatus,
    ContractViolationCreate,
    ViolationAction,
    ViolationKind,
)
from aiuditor_service.events import CONTRACT_VIOLATED_V1
from aiuditor_service.store import ApprovalStore, ContractStore, ViolationStore
from aiuditor_service.violations import (
    DENY_BUDGET,
    evaluate_kind,
    put_violation,
)
from aiuditor_service.hypermesh import HostView
from conftest import FYBER_AGX_DEVICE_ID

_UNTIL = datetime(2026, 12, 1, tzinfo=UTC)


def _signed(
    store: ContractStore,
    tenant: uuid.UUID,
    *,
    lease_id=None,
    until=_UNTIL,
    now: datetime | None = None,
):
    clock = now or datetime(2026, 1, 1, tzinfo=UTC)
    row = put_contract(
        store,
        tenant,
        ContractCreate(
            parties=[
                ContractParty(role="renter", subject="renter@example.com"),
                ContractParty(role="host", subject="agx-host"),
            ],
            meters=["wall_time"],
            until=until,
            lease_id=lease_id,
        ),
        now=clock,
    )
    return sign_contract(
        store,
        tenant,
        row.id,
        signed_by="11111111-1111-1111-1111-111111111111",
        now=clock,
    )


def test_term_until_is_refund():
    tenant = uuid.uuid4()
    store = ContractStore()
    row = _signed(store, tenant, until=datetime(2026, 2, 1, tzinfo=UTC))
    assert (
        evaluate_kind(row, now=datetime(2026, 9, 20, tzinfo=UTC)) is ViolationKind.TERM
    )


def test_cert_fail_and_deny_budget_and_unpaid():
    tenant = uuid.uuid4()
    store = ContractStore()
    row = _signed(store, tenant)
    assert (
        evaluate_kind(
            row,
            host=HostView(
                id=FYBER_AGX_DEVICE_ID,
                enrolled=True,
                role="host",
                path_b_state="suspended",
            ),
        )
        is ViolationKind.CERT_FAIL
    )
    assert (
        evaluate_kind(row, denied=DENY_BUDGET) is ViolationKind.POLICY_DENY
    )
    from aiuditor_service.hypermesh import LeaseView

    lease = LeaseView(
        id=uuid.uuid4(),
        device_id=FYBER_AGX_DEVICE_ID,
        purpose="renter",
        status="active",
    )
    assert (
        evaluate_kind(
            row,
            host=HostView(
                id=FYBER_AGX_DEVICE_ID,
                enrolled=True,
                role="host",
                path_b_state="green",
                schedule_hold=True,
            ),
            lease=lease,
        )
        is ViolationKind.ABUSE
    )


def test_draft_cannot_take_a_violation():
    store = ContractStore()
    violations = ViolationStore()
    tenant = uuid.uuid4()
    draft = put_contract(
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
    )
    with pytest.raises(Exception) as exc:
        put_violation(
            violations,
            store,
            tenant,
            draft.id,
            ContractViolationCreate(kind=ViolationKind.ABUSE),
        )
    assert exc.value.status_code == 409


def test_file_suspends_the_contract():
    store = ContractStore()
    violations = ViolationStore()
    tenant = uuid.uuid4()
    signed = _signed(store, tenant)
    filed, updated = put_violation(
        violations,
        store,
        tenant,
        signed.id,
        ContractViolationCreate(kind=ViolationKind.POLICY_DENY),
    )
    assert filed.action is ViolationAction.SUSPEND
    assert updated.status is ContractStatus.SUSPENDED


@pytest.mark.asyncio
async def test_evaluate_term_refunds_bound_lease(
    app, http_client, tenant_headers, tenant_a, plane_hop, event_sink, monkeypatch
):
    monkeypatch.setenv("AIUDITOR_INTERNAL_TOKEN", "test-aiuditor-internal-token")
    lease_id = uuid.uuid4()
    plane_hop.add_lease(
        lease_id, device_id=FYBER_AGX_DEVICE_ID, tenant_id=tenant_a
    )
    fulfilled = await http_client.post(
        "/api/v1/aiuditor/contracts/fulfill",
        headers={
            "X-Tenant-ID": str(tenant_a),
            "X-Internal-Token": "test-aiuditor-internal-token",
        },
        json={
            "lease_id": str(lease_id),
            "reserved_hours": 1,
            "renter_subject": "renter@example.com",
            "host_subject": "agx-host",
        },
    )
    assert fulfilled.status_code == 201, fulfilled.text
    contract_id = fulfilled.json()["id"]
    store = app.state.contract_store
    row = store.get(tenant_a, uuid.UUID(contract_id))
    store.put(
        row.model_copy(update={"until": datetime(2020, 1, 1, tzinfo=UTC)})
    )
    evaluated = await http_client.post(
        f"/api/v1/aiuditor/contracts/{contract_id}/evaluate",
        headers=tenant_headers,
    )
    assert evaluated.status_code == 200, evaluated.text
    body = evaluated.json()
    assert len(body) == 1
    assert body[0]["kind"] == "term"
    assert body[0]["action"] == "refund"
    assert plane_hop.violations
    assert plane_hop.violations[0]["action"] == "refund"
    assert CONTRACT_VIOLATED_V1 in [e["event_type"] for e in event_sink]


@pytest.mark.asyncio
async def test_file_abuse_stops_lease(
    http_client, tenant_headers, tenant_a, plane_hop, monkeypatch
):
    monkeypatch.setenv("AIUDITOR_INTERNAL_TOKEN", "test-aiuditor-internal-token")
    lease_id = uuid.uuid4()
    plane_hop.add_lease(
        lease_id, device_id=FYBER_AGX_DEVICE_ID, tenant_id=tenant_a
    )
    fulfilled = await http_client.post(
        "/api/v1/aiuditor/contracts/fulfill",
        headers={
            "X-Tenant-ID": str(tenant_a),
            "X-Internal-Token": "test-aiuditor-internal-token",
        },
        json={
            "lease_id": str(lease_id),
            "renter_subject": "renter@example.com",
            "host_subject": "agx-host",
        },
    )
    assert fulfilled.status_code == 201, fulfilled.text
    filed = await http_client.post(
        f"/api/v1/aiuditor/contracts/{fulfilled.json()['id']}/violations",
        headers=tenant_headers,
        json={"kind": "abuse"},
    )
    assert filed.status_code == 201, filed.text
    assert filed.json()["action"] == "stop"
    assert plane_hop.violations[-1]["action"] == "stop"
    listed = await http_client.get("/api/v1/aiuditor/violations", headers=tenant_headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()[0]["id"] == filed.json()["id"]
    assert listed.json()[0]["kind"] == "abuse"
    scoped = await http_client.get(
        f"/api/v1/aiuditor/violations?contract_id={fulfilled.json()['id']}",
        headers=tenant_headers,
    )
    assert scoped.status_code == 200
    assert scoped.json()[0]["contract_id"] == fulfilled.json()["id"]


@pytest.mark.asyncio
async def test_evaluate_deny_budget_suspends(
    app, http_client, tenant_headers, tenant_a, plane_hop, monkeypatch
):
    monkeypatch.setenv("AIUDITOR_INTERNAL_TOKEN", "test-aiuditor-internal-token")
    lease_id = uuid.uuid4()
    plane_hop.add_lease(
        lease_id, device_id=FYBER_AGX_DEVICE_ID, tenant_id=tenant_a
    )
    fulfilled = await http_client.post(
        "/api/v1/aiuditor/contracts/fulfill",
        headers={
            "X-Tenant-ID": str(tenant_a),
            "X-Internal-Token": "test-aiuditor-internal-token",
        },
        json={
            "lease_id": str(lease_id),
            "renter_subject": "renter@example.com",
            "host_subject": "agx-host",
        },
    )
    assert fulfilled.status_code == 201
    approvals: ApprovalStore = app.state.approval_store
    now = datetime(2026, 9, 20, tzinfo=UTC)
    for _ in range(DENY_BUDGET):
        approvals.put(
            Approval(
                id=uuid.uuid4(),
                tenant_id=tenant_a,
                lease_id=lease_id,
                purpose="renter",
                who="renter@example.com",
                why=ApprovalWhy.COMPLETION,
                status=ApprovalStatus.DENIED,
                decision=ApprovalDecision.DENY,
                created_at=now,
                expires_at=now,
                resolved_at=now,
                resolved_by="11111111-1111-1111-1111-111111111111",
            )
        )
    evaluated = await http_client.post(
        f"/api/v1/aiuditor/contracts/{fulfilled.json()['id']}/evaluate",
        headers=tenant_headers,
    )
    assert evaluated.status_code == 200, evaluated.text
    assert evaluated.json()[0]["kind"] == "policy_deny"
    assert evaluated.json()[0]["action"] == "suspend"
    assert plane_hop.violations[-1]["action"] == "suspend"


@pytest.mark.asyncio
async def test_site_inbox_is_not_on_violation_paths(http_client):
    spec = await http_client.get("/openapi.json")
    paths = spec.json()["paths"]
    assert "/api/v1/aiuditor/violations" in paths
    assert "/api/v1/aiuditor/contracts/{contract_id}/violations" in paths
    assert "/api/v1/aiuditor/contracts/{contract_id}/evaluate" in paths
    assert "/api/v1/auditor/v0/tickets" not in paths


@pytest.mark.parametrize("path", ["violations", "evaluate"])
@pytest.mark.parametrize("bound", [False, True])
async def test_witness_cannot_mutate_hydrated_contract(
    app, http_client, tenant_a, tenant_headers, plane_hop, event_sink, path, bound
):
    from aiuditor_service.role import AiuditorRole
    row = _signed(app.state.contract_store, tenant_a).model_copy(update={
        "lease_id": uuid.uuid4() if bound else None,
        "until": datetime(2020, 1, 1, tzinfo=UTC),
    })
    app.state.contract_store.put(row)
    app.state.aiuditor_role = AiuditorRole.WITNESS
    before_events = list(event_sink)
    response = await http_client.post(
        f"/api/v1/aiuditor/contracts/{row.id}/{path}", headers=tenant_headers,
        **({"json": {"kind": "abuse"}} if path == "violations" else {}),
    )
    assert response.status_code == 403
    assert app.state.contract_store.get(tenant_a, row.id) == row
    assert app.state.violation_store.list_for(tenant_a) == []
    assert not plane_hop.violations
    assert event_sink == before_events


async def test_violation_http_hop_regenerates_framing(
    app, http_client, tenant_a, tenant_headers, monkeypatch
):
    import httpx
    from aiuditor_service.hypermesh import HttpPlaneHop
    row = _signed(app.state.contract_store, tenant_a).model_copy(update={"lease_id": uuid.uuid4()})
    app.state.contract_store.put(row)
    seen = []
    real_client = httpx.AsyncClient
    async def upstream(request):
        seen.append(request)
        assert request.headers["host"] == "plane.example"
        assert int(request.headers["content-length"]) == len(request.content)
        assert request.headers["authorization"] == tenant_headers["Authorization"]
        assert request.headers["x-tenant-id"] == str(tenant_a)
        assert "cookie" not in request.headers
        assert "x-untrusted" not in request.headers
        return httpx.Response(200, json={})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(
        transport=httpx.MockTransport(upstream), **kw))
    app.state.plane_hop = HttpPlaneHop("https://plane.example")
    response = await http_client.post(
        f"/api/v1/aiuditor/contracts/{row.id}/violations",
        headers={**tenant_headers, "Host": "inbound.example", "Cookie": "private=secret", "X-Untrusted": "no"},
        json={"kind": "abuse"},
    )
    assert response.status_code == 201, response.text
    assert len(seen) == 1

async def test_pending_violation_retries_after_failure_and_restart(app, http_client, tenant_headers, tenant_a, plane_hop, monkeypatch):
    monkeypatch.setenv('AIUDITOR_INTERNAL_TOKEN', 'test-aiuditor-internal-token')
    lease_id = uuid.uuid4()
    plane_hop.add_lease(lease_id, device_id=FYBER_AGX_DEVICE_ID, tenant_id=tenant_a)
    contract = await http_client.post('/api/v1/aiuditor/contracts/fulfill',
        headers={'X-Tenant-ID': str(tenant_a), 'X-Internal-Token': 'test-aiuditor-internal-token'},
        json={'lease_id': str(lease_id), 'renter_subject': 'renter', 'host_subject': 'host'})
    cid = uuid.UUID(contract.json()['id'])
    plane_hop.violate_error = 'temporarily unavailable'
    url = f'/api/v1/aiuditor/contracts/{cid}/violations'
    first = await http_client.post(url, headers=tenant_headers, json={'kind': 'abuse'})
    assert first.status_code == 503
    pending = app.state.violation_store.list_for(tenant_a)[0]
    assert pending.enforcement_completed_at is None
    assert pending.enforcement_attempts == 1
    assert app.state.contract_store.get(tenant_a, cid).status == ContractStatus.SUSPENDED
    # Restart the owner store, retaining only persisted rows.
    app.state.violation_store = ViolationStore(persist=app.state.violation_store.persist)
    plane_hop.violate_error = None
    recovered = await http_client.post(f'/api/v1/aiuditor/contracts/{cid}/evaluate', headers=tenant_headers)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()[0]['id'] == str(pending.id)
    assert recovered.json()[0]['enforcement_completed_at'] is not None
    for _ in range(3):
        assert (await http_client.post(url, headers=tenant_headers, json={'kind': 'abuse'})).status_code == 201
    assert len(plane_hop.violations) == 1
    assert len(app.state.violation_store.list_for(tenant_a)) == 1
