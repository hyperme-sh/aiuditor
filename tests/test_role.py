from __future__ import annotations

import uuid
from datetime import UTC, datetime
from contextlib import asynccontextmanager

import httpx
import pytest
from asgi_lifespan import LifespanManager

from aiuditor_service.app import create_app
from aiuditor_service.contracts import put_contract, sign_contract
from aiuditor_service.domain.models import ContractCreate, ContractParty, HydrateSnapshot
from aiuditor_service.parent import (
    PARENT_HOP_FAILED,
    PARENT_REQUIRED,
    ClosedParentHop,
    FakeParentHop,
    HttpParentHop,
    ParentUnavailable,
    apply_hydrate,
    resolve_parent_hop,
)
from aiuditor_service.role import (
    ROLE_INVALID,
    WITNESS_CANNOT_MINT,
    WITNESS_CANNOT_WRITE_GRANT,
    WITNESS_PULL_ONLY,
    WRITER_MARKET,
    WRITER_NONE,
    WRITER_PRIVATE,
    AiuditorRole,
    RoleUnavailable,
    resolve_role,
    writer_for,
)
from aiuditor_service.store import ContractStore
from conftest import tenant_session_headers

_UNTIL = datetime(2026, 12, 1, tzinfo=UTC)


def test_default_role_is_global_market():
    assert resolve_role("") is AiuditorRole.GLOBAL
    assert resolve_role(None) is AiuditorRole.GLOBAL
    assert writer_for(AiuditorRole.GLOBAL) == WRITER_MARKET
    assert writer_for(AiuditorRole.OPERATOR) == WRITER_PRIVATE
    assert writer_for(AiuditorRole.WITNESS) == WRITER_NONE


def test_invalid_role_fails_closed():
    with pytest.raises(RoleUnavailable, match=ROLE_INVALID):
        resolve_role("laptop")


def test_create_app_rejects_bad_role(monkeypatch):
    monkeypatch.setenv("AIUDITOR_ROLE", "laptop")
    with pytest.raises(RoleUnavailable, match=ROLE_INVALID):
        create_app(enable_db_middleware=False)


@asynccontextmanager
async def _client(app):
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


def _role_app(event_sink, host_lookup, plane_hop, provider_hop, role, parent_hop=None):
    return create_app(
        test_event_sink=event_sink,
        enable_db_middleware=False,
        host_lookup=host_lookup,
        plane_hop=plane_hop,
        provider_hop=provider_hop,
        role=role,
        parent_hop=parent_hop,
    )


@pytest.mark.asyncio
async def test_global_meta_is_market_writer(
    event_sink, host_lookup, plane_hop, provider_hop
):
    app = _role_app(
        event_sink, host_lookup, plane_hop, provider_hop, AiuditorRole.GLOBAL
    )
    async with _client(app) as client:
        meta = await client.get("/api/v1/aiuditor/meta")
    assert meta.status_code == 200, meta.text
    assert meta.json() == {
        "role": "global",
        "writer": WRITER_MARKET,
        "parent": False,
    }


@pytest.mark.asyncio
async def test_operator_can_fulfill_private_grant(
    event_sink, host_lookup, plane_hop, provider_hop, tenant_a, monkeypatch
):
    monkeypatch.setenv("AIUDITOR_INTERNAL_TOKEN", "test-aiuditor-internal-token")
    app = _role_app(
        event_sink, host_lookup, plane_hop, provider_hop, AiuditorRole.OPERATOR
    )
    headers = tenant_session_headers(tenant_a)
    async with _client(app) as client:
        meta = await client.get("/api/v1/aiuditor/meta")
        assert meta.json()["writer"] == WRITER_PRIVATE
        fulfilled = await client.post(
            "/api/v1/aiuditor/contracts/fulfill",
            headers={
                "X-Tenant-ID": str(tenant_a),
                "X-Internal-Token": "test-aiuditor-internal-token",
            },
            json={
                "lease_id": str(uuid.uuid4()),
                "renter_subject": "renter@example.com",
                "host_subject": str(uuid.uuid4()),
                "reserved_hours": 1,
            },
        )
        assert fulfilled.status_code == 201, fulfilled.text
        grant = await client.get(
            f"/api/v1/aiuditor/contracts/{fulfilled.json()['id']}/grant",
            headers=headers,
        )
    assert grant.status_code == 200, grant.text
    assert grant.json()["writer"] == WRITER_PRIVATE


@pytest.mark.asyncio
async def test_witness_refuses_fulfill_and_mint(
    event_sink, host_lookup, plane_hop, provider_hop, tenant_a, tenant_headers, monkeypatch
):
    monkeypatch.setenv("AIUDITOR_INTERNAL_TOKEN", "test-aiuditor-internal-token")
    app = _role_app(
        event_sink, host_lookup, plane_hop, provider_hop, AiuditorRole.WITNESS
    )
    async with _client(app) as client:
        meta = await client.get("/api/v1/aiuditor/meta")
        assert meta.json()["writer"] == WRITER_NONE
        fulfilled = await client.post(
            "/api/v1/aiuditor/contracts/fulfill",
            headers={
                "X-Tenant-ID": str(tenant_a),
                "X-Internal-Token": "test-aiuditor-internal-token",
            },
            json={
                "lease_id": str(uuid.uuid4()),
                "renter_subject": "renter@example.com",
                "host_subject": str(uuid.uuid4()),
            },
        )
        drafted = await client.post(
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
        ticket = await client.post(
            f"/api/v1/aiuditor/connections/{uuid.uuid4()}/ticket",
            headers=tenant_headers,
            json={"reserved_hours": 1},
        )
        assigned = await client.post(
            f"/api/v1/aiuditor/connections/{uuid.uuid4()}/assign",
            headers=tenant_headers,
            json={"lease_id": str(uuid.uuid4())},
        )
        allocated = await client.post(
            f"/api/v1/aiuditor/catalog/{uuid.uuid4()}/allocate",
            headers=tenant_headers,
            json={"renter": "acme-renter"},
        )
        pulled = await client.post(
            "/api/v1/aiuditor/hydrate/pull",
            headers=tenant_headers,
        )
    assert fulfilled.status_code == 403
    assert fulfilled.json()["detail"] == WITNESS_CANNOT_WRITE_GRANT
    assert drafted.status_code == 403
    assert drafted.json()["detail"] == WITNESS_CANNOT_WRITE_GRANT
    assert ticket.status_code == 403
    assert ticket.json()["detail"] == WITNESS_CANNOT_MINT
    assert assigned.status_code == 403
    assert assigned.json()["detail"] == WITNESS_CANNOT_MINT
    assert allocated.status_code == 403
    assert allocated.json()["detail"] == WITNESS_CANNOT_WRITE_GRANT
    assert pulled.status_code == 503
    assert pulled.json()["detail"] == PARENT_REQUIRED


@pytest.mark.asyncio
async def test_operator_cannot_pull_hydrate(
    event_sink, host_lookup, plane_hop, provider_hop, tenant_headers
):
    app = _role_app(
        event_sink,
        host_lookup,
        plane_hop,
        provider_hop,
        AiuditorRole.OPERATOR,
        parent_hop=FakeParentHop(),
    )
    async with _client(app) as client:
        pulled = await client.post(
            "/api/v1/aiuditor/hydrate/pull",
            headers=tenant_headers,
        )
    assert pulled.status_code == 403
    assert pulled.json()["detail"] == WITNESS_PULL_ONLY


@pytest.mark.asyncio
async def test_witness_pulls_parent_snapshot(
    event_sink, host_lookup, plane_hop, provider_hop, tenant_a, tenant_headers
):
    source = ContractStore()
    signed = sign_contract(
        source,
        tenant_a,
        put_contract(
            source,
            tenant_a,
            ContractCreate(
                parties=[
                    ContractParty(role="renter", subject="renter@example.com"),
                    ContractParty(role="host", subject="agx-host"),
                ],
                meters=["wall_time"],
                until=_UNTIL,
            ),
        ).id,
        signed_by="11111111-1111-1111-1111-111111111111",
    )
    snapshot = HydrateSnapshot(
        tenant_id=tenant_a,
        role="global",
        writer=WRITER_MARKET,
        exported_at=datetime.now(UTC),
        connections=[],
        policies=[],
        contracts=[signed.model_dump(mode="json")],
        violations=[],
        approvals=[],
        catalog=[],
    )
    parent = FakeParentHop(snapshot=snapshot)
    app = _role_app(
        event_sink,
        host_lookup,
        plane_hop,
        provider_hop,
        AiuditorRole.WITNESS,
        parent_hop=parent,
    )
    async with _client(app) as client:
        meta = await client.get("/api/v1/aiuditor/meta")
        assert meta.json()["parent"] is True
        pulled = await client.post(
            "/api/v1/aiuditor/hydrate/pull",
            headers=tenant_headers,
        )
        assert pulled.status_code == 200, pulled.text
        assert pulled.json()["writer"] == WRITER_MARKET
        listed = await client.get("/api/v1/aiuditor/contracts", headers=tenant_headers)
        grant = await client.get(
            f"/api/v1/aiuditor/contracts/{signed.id}/grant",
            headers=tenant_headers,
        )
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == str(signed.id)
    assert grant.status_code == 200
    assert grant.json()["writer"] == WRITER_NONE
    assert parent.calls == [tenant_a]


def test_closed_parent_is_the_default():
    assert isinstance(resolve_parent_hop(), ClosedParentHop)


@pytest.mark.asyncio
async def test_closed_parent_hydrate_fails():
    with pytest.raises(ParentUnavailable, match=PARENT_REQUIRED):
        await ClosedParentHop().hydrate(uuid.uuid4())


@pytest.mark.asyncio
async def test_global_hydrate_is_lock_fields_only(
    event_sink, host_lookup, plane_hop, provider_hop, tenant_a, tenant_headers
):
    app = _role_app(
        event_sink, host_lookup, plane_hop, provider_hop, AiuditorRole.GLOBAL
    )
    async with _client(app) as client:
        created = await client.post(
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
        hydrated = await client.get("/api/v1/aiuditor/hydrate", headers=tenant_headers)
    assert hydrated.status_code == 200, hydrated.text
    body = hydrated.json()
    assert body["role"] == "global"
    assert body["writer"] == WRITER_MARKET
    assert body["contracts"][0]["id"] == created.json()["id"]
    dumped = str(body)
    assert "prompt" not in dumped
    assert "ticket" not in dumped
    assert "credential" not in dumped or "credential_ref" in dumped


@pytest.mark.asyncio
async def test_http_parent_hydrate_and_bad_payload(tenant_a):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/hydrate")
        assert request.headers["X-Internal-Token"] == "parent-token"
        return httpx.Response(
            200,
            json={
                "tenant_id": str(tenant_a),
                "role": "global",
                "writer": WRITER_MARKET,
                "exported_at": datetime.now(UTC).isoformat(),
                "connections": [],
                "policies": [],
                "contracts": [],
                "violations": [],
                "approvals": [],
                "catalog": [],
            },
        )

    hop = HttpParentHop(
        "http://parent.test",
        internal_token="parent-token",
        transport=httpx.MockTransport(handler),
    )
    snapshot = await hop.hydrate(tenant_a)
    assert snapshot.writer == WRITER_MARKET

    from aiuditor_service.store import new_stores
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        apply_hydrate(new_stores(), snapshot.model_copy(update={"contracts": ["nope"]}))


@pytest.mark.asyncio
async def test_http_parent_hydrate_fails_closed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "down"})

    hop = HttpParentHop(
        "http://parent.test",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ParentUnavailable, match=PARENT_HOP_FAILED):
        await hop.hydrate(uuid.uuid4())


@pytest.mark.asyncio
async def test_fake_parent_error_and_http_resolve(monkeypatch, tenant_a):
    hop = FakeParentHop(error=PARENT_HOP_FAILED)
    with pytest.raises(ParentUnavailable, match=PARENT_HOP_FAILED):
        await hop.hydrate(tenant_a)
    missing = FakeParentHop()
    with pytest.raises(ParentUnavailable, match=PARENT_HOP_FAILED):
        await missing.hydrate(tenant_a)
    monkeypatch.setenv("AIUDITOR_PARENT_URL", "http://parent.test")
    monkeypatch.setenv("AIUDITOR_PARENT_TOKEN", "tok")
    resolved = resolve_parent_hop()
    assert isinstance(resolved, HttpParentHop)


def test_role_from_request_reads_string_state():
    from types import SimpleNamespace

    from aiuditor_service.role import role_from_request

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(aiuditor_role="operator")))
    assert role_from_request(request) is AiuditorRole.OPERATOR
