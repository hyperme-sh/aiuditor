from contextlib import asynccontextmanager
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from aiuditor_service import capability_routes as routes
from test_capability_contracts import NOW, policy, signed_call


def request(headers=None):
    return Request({"type": "http", "app": SimpleNamespace(state=SimpleNamespace()), "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]})


@pytest.fixture
def owner_store(monkeypatch, signed_call):
    body, _ = signed_call
    row = SimpleNamespace(request_id=body.request_id, revoked=False,
                          state={"public_key": body.public_key, "capability_policy": policy(body)})
    calls = []
    class Session:
        async def scalar(self, query):
            calls.append("select")
            return row
    @asynccontextmanager
    async def transaction(tenant):
        calls.append(tenant)
        yield Session()
    monkeypatch.setattr(routes, "transaction", transaction)
    monkeypatch.setattr(routes.time, "time", lambda: NOW)
    monkeypatch.setenv("AIUDITOR_ID", "local")
    return row, calls


async def test_review_reads_current_registration(signed_call, owner_store):
    body, _ = signed_call
    row, calls = owner_store
    response = Response()
    result = await routes.review_call(request(), body, response)
    assert result["decision"] == "allow"
    assert calls[0] == body.tenant_id
    assert response.headers["Cache-Control"] == "no-store"
    row.revoked = True
    with pytest.raises(HTTPException) as error:
        await routes.review_call(request(), body, Response())
    assert error.value.status_code == 410
    row.revoked = False
    row.request_id = uuid.uuid4()
    with pytest.raises(HTTPException) as error:
        await routes.review_call(request(), body, Response())
    assert error.value.status_code == 401


async def test_invalid_signature_and_tenant_never_open_database(signed_call, owner_store):
    body, _ = signed_call
    _, calls = owner_store
    for req, changed in [(request({"X-Tenant-ID": str(uuid.uuid4())}), body),
                         (request(), body.model_copy(update={"signature": "x" * 88})),
                         (request(), body.model_copy(update={"authority_id": "other"}))]:
        with pytest.raises(HTTPException):
            await routes.review_call(req, changed, Response())
    assert calls == []


async def test_policy_requires_human_and_current_authority(monkeypatch, signed_call, owner_store):
    body, _ = signed_call
    row, calls = owner_store
    data = routes.CapabilityPolicy.model_validate(policy(body))
    with pytest.raises(HTTPException):
        await routes.replace_policy(request(), data, Response())
    assert calls == []
    monkeypatch.setattr(routes, "human", lambda req: (body.tenant_id, "human-owner"))
    async def refuse(*args):
        raise HTTPException(403, "Current authority required")
    monkeypatch.setattr(routes, "approver", refuse)
    with pytest.raises(HTTPException):
        await routes.replace_policy(request(), data, Response())
    assert "select" not in calls
    async def approve(session, tenant, subject, req):
        assert tenant == body.tenant_id and subject == "human-owner"
    monkeypatch.setattr(routes, "approver", approve)
    row.state["capability_calls"] = [["nonce", "call", NOW + 20]]
    data.grants = []
    assert (await routes.replace_policy(request(), data, Response()))["grant_count"] == 0
    assert row.state["capability_calls"] == [["nonce", "call", NOW + 20]]
    assert row.state["public_key"] == body.public_key
    assert (await routes.review_call(request(), body, Response()))["decision"] == "deny"


async def test_witness_refuses_review(monkeypatch, signed_call, owner_store):
    monkeypatch.setenv("AIUDITOR_ROLE", "witness")
    with pytest.raises(HTTPException) as error:
        await routes.review_call(request(), signed_call[0], Response())
    assert error.value.status_code == 403
    assert owner_store[1] == []


async def test_http_validation_redacts_inputs(http_client):
    result = await http_client.post("/api/v1/aiuditor/capabilities/review", json={"signature": "private-input"})
    assert result.status_code == 422
    assert result.json() == {"error": "Invalid capability request"}
    assert result.headers["cache-control"] == "no-store"


async def test_http_route_requires_signature(http_client, signed_call, owner_store):
    body, _ = signed_call
    data = body.model_dump(mode="json")
    data["signature"] = "x" * 88
    result = await http_client.post("/api/v1/aiuditor/capabilities/review", json=data)
    assert result.status_code == 401
    assert owner_store[1] == []
