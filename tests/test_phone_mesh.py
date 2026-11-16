"""Phone peer permission contracts; memory storage and no network calls."""
import base64
from contextlib import asynccontextmanager
import hashlib
import json
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException, Response

from aiuditor_service import phone_mesh as mesh


@pytest.fixture
def setup(monkeypatch):
    tenant, device, enrollment = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    digest = hashlib.sha256(b"fixture-machine-credential").hexdigest()
    row = SimpleNamespace(revoked=False, request_id=enrollment,
                          state={"owner_attested_client": True, "tokens": [[digest, 1100]]})
    class Session:
        async def scalar(self, query):
            return row
    @asynccontextmanager
    async def transaction(tid):
        assert tid == tenant
        yield Session()
    monkeypatch.setattr(mesh.identities, "transaction", transaction)
    monkeypatch.setattr(mesh.identities, "actor", lambda request: (tenant, "plane"))
    monkeypatch.setattr(mesh.time, "time", lambda: 1000)
    monkeypatch.setenv("AIUDITOR_PHONE_GATEWAYS", json.dumps({
        "gateway": {"token": "test-only-gateway-token-0000000000", "control_planes": ["plane"]}}))
    body = mesh.MeshRequest(device_id=device, request_id=enrollment, digest=digest,
        gateway_id="gateway", wg_public_key=base64.b64encode(bytes(range(32))).decode())
    request = SimpleNamespace(headers={"X-Phone-Gateway-ID": "gateway",
        "X-Phone-Gateway-Token": "test-only-gateway-token-0000000000", "X-Tenant-ID": str(tenant)})
    return row, body, request


async def test_grant_bound_to_registered_key_and_gateway(setup):
    row, body, request = setup
    result = await mesh.authorize(None, body, Response())
    assert result["expires_at"] == 1060
    assert result["grant"] not in json.dumps(row.state)
    lookup = mesh.MeshLookup(device_id=body.device_id, request_id=body.request_id,
                            control_plane_id="plane", grant=result["grant"])
    allowed = await mesh.inspect(request, lookup, Response())
    assert allowed["active"] and allowed["wg_public_key"] == body.wg_public_key
    wrong = lookup.model_copy(update={"request_id": uuid.uuid4()})
    assert await mesh.inspect(request, wrong, Response()) == {"active": False}
    row.revoked = True
    assert await mesh.inspect(request, lookup, Response()) == {"active": False}


@pytest.mark.parametrize("change", ["host", "expired", "revoked", "wrong_registration", "wrong_gateway"])
async def test_refused_before_grant_storage(setup, change):
    row, body, _ = setup
    if change == "host": row.state["owner_attested_client"] = False
    if change == "expired": row.state["tokens"][0][1] = 1000
    if change == "revoked": row.revoked = True
    if change == "wrong_registration": row.request_id = uuid.uuid4()
    if change == "wrong_gateway": body.gateway_id = "other"
    with pytest.raises(HTTPException):
        await mesh.authorize(None, body, Response())
    assert "phone_mesh_grants" not in row.state


async def test_key_change_and_excess_grants_refused(setup):
    row, body, _ = setup
    await mesh.authorize(None, body, Response())
    other = body.model_copy(update={"wg_public_key": base64.b64encode(b"x" * 32).decode()})
    with pytest.raises(HTTPException) as error:
        await mesh.authorize(None, other, Response())
    assert error.value.status_code == 409
    for _ in range(7): await mesh.authorize(None, body, Response())
    with pytest.raises(HTTPException) as error:
        await mesh.authorize(None, body, Response())
    assert error.value.status_code == 429


async def test_introspection_requires_distinct_gateway_credential_and_fresh_grant(setup, monkeypatch):
    row, body, request = setup
    result = await mesh.authorize(None, body, Response())
    lookup = mesh.MeshLookup(device_id=body.device_id, request_id=body.request_id,
                            control_plane_id="plane", grant=result["grant"])
    invalid = SimpleNamespace(headers={**request.headers, "X-Phone-Gateway-Token": "wrong"})
    with pytest.raises(HTTPException) as error:
        await mesh.inspect(invalid, lookup, Response())
    assert error.value.status_code == 401
    monkeypatch.setattr(mesh.time, "time", lambda: 1060)
    assert await mesh.inspect(request, lookup, Response()) == {"active": False}


async def test_gateway_secret_cannot_issue_grants(monkeypatch):
    from aiuditor_service.role import AiuditorRole
    monkeypatch.setenv("AIUDITOR_CONTROL_PLANE_TOKENS", json.dumps({"plane": "plane-only-secret"}))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(aiuditor_role=AiuditorRole.GLOBAL)),
        headers={"X-Control-Plane-ID": "plane", "X-Internal-Token": "gateway-only-secret",
                 "X-Tenant-ID": str(uuid.uuid4())})
    with pytest.raises(HTTPException) as error:
        mesh.identities.actor(request)
    assert error.value.status_code == 401
    request.app.state.aiuditor_role = AiuditorRole.WITNESS
    request.headers["X-Internal-Token"] = "plane-only-secret"
    with pytest.raises(HTTPException) as error:
        mesh.identities.actor(request)
    assert error.value.status_code == 403


@pytest.mark.parametrize("key", ["!" * 44, base64.b64encode(bytes(32)).decode()])
def test_invalid_keys_refused(key):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        mesh.MeshRequest(device_id=uuid.uuid4(), request_id=uuid.uuid4(), digest="a" * 64,
                         gateway_id="gateway", wg_public_key=key)
