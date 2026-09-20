from __future__ import annotations

import uuid

import pytest

from aiuditor_service.credentials import credential_ref
from aiuditor_service.domain.models import Connection, ConnectionKind, ConnectionPurpose, ConnectionStatus
from aiuditor_service.events import CONNECTION_CREATED_V1


def test_credential_ref_is_sha256_hex_not_raw():
    raw = "sk-live-example"
    hashed = credential_ref(raw)
    assert hashed != raw
    assert len(hashed) == 64
    assert hashed == credential_ref(raw)
    assert raw not in hashed


def test_connection_event_payload_is_lock_fields_only():
    tid = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    device = uuid.UUID("a6400000-0640-4000-8000-000000000001")
    row = Connection(
        id=uuid.uuid4(),
        tenant_id=tid,
        kind=ConnectionKind.HOST,
        purpose=ConnectionPurpose.AUDITOR,
        device_id=device,
        endpoint=None,
        credential_ref="abc" * 21 + "abcd",
        status=ConnectionStatus.READY,
        created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
    )
    payload = row.to_event_payload()
    assert set(payload) == {
        "id",
        "tenant_id",
        "kind",
        "purpose",
        "device_id",
        "endpoint",
        "credential_ref",
        "status",
    }
    assert "credential" not in payload
    assert payload["kind"] == "host"
    assert payload["purpose"] == "auditor"


@pytest.mark.asyncio
async def test_health(http_client):
    r = await http_client.get("/api/v1/health/")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "Ok"


@pytest.mark.asyncio
async def test_openapi_is_aiuditor_not_hypermesh_or_site_inbox(http_client):
    r = await http_client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    assert "/api/v1/health/" in paths
    assert "/api/v1/aiuditor/connections" in paths
    assert "/api/v1/aiuditor/connections/{connection_id}" in paths
    banned = (
        "/api/v1/hypermesh/classes",
        "/api/v1/hypermesh/devices",
        "/api/v1/hypermesh/catalog",
        "/api/v1/auditor/v0/tickets",
        "/api/v1/grants/v0/grants",
        "/api/v1/samples",
    )
    for path in banned:
        assert path not in paths, path


def test_main_starts_uvicorn(monkeypatch):
    called: dict[str, object] = {}

    def _run(app, host, port):
        called["host"] = host
        called["port"] = port
        called["app"] = app

    monkeypatch.setattr("uvicorn.run", _run)
    from aiuditor_service.main import main

    main()
    assert called["host"] == "0.0.0.0"
    assert called["port"] == 8000


@pytest.mark.asyncio
async def test_store_lazy_inits_when_missing(http_client, app, tenant_headers):
    del app.state.connection_store
    listed = await http_client.get("/api/v1/aiuditor/connections", headers=tenant_headers)
    assert listed.status_code == 200, listed.text
    assert listed.json() == []


@pytest.mark.asyncio
async def test_create_connection_emits_event(
    http_client, tenant_headers, tenant_a, event_sink
):
    device = uuid.UUID("a6400000-0640-4000-8000-000000000001")
    r = await http_client.post(
        "/api/v1/aiuditor/connections",
        headers=tenant_headers,
        json={
            "kind": "host",
            "purpose": "auditor",
            "device_id": str(device),
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["tenant_id"] == str(tenant_a)
    assert body["kind"] == "host"
    assert body["purpose"] == "auditor"
    assert body["device_id"] == str(device)
    assert "credential" not in body
    assert body["credential_ref"] is None
    assert body["status"] == "ready"
    assert event_sink
    assert event_sink[0]["event_type"] == CONNECTION_CREATED_V1
    assert event_sink[0]["tenant_id"] == tenant_a
    assert "credential" not in event_sink[0]["payload"]
    assert event_sink[0]["payload"]["credential_ref"] is None
