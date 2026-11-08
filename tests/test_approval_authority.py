"""Network approval facts cannot replace verified interactive authentication."""

import json
import uuid
import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request
from aiuditor_service.approval_authority import require_current_approval


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "none",
        "deny",
        "nonce",
        "subject",
        "tenant",
        "action",
        "org",
        "redirect",
        "missing",
        "truthy",
    ],
)
async def test_exact_normal_caller_owner_reply(monkeypatch, change):
    tenant = uuid.uuid4()
    subject = "verified-person"
    calls = []
    monkeypatch.setenv("USER_AUTHORITY_URL", "https://user-owner.test")
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/resolve",
            "headers": [(b"authorization", b"Bearer interactive-session")],
        }
    )

    def reply(request):
        calls.append(request)
        body = json.loads(request.content)
        assert request.headers["Authorization"] == "Bearer interactive-session"
        assert "subject" not in body
        if change == "redirect":
            return httpx.Response(307, headers={"Location": "https://wrong.test"})
        value = {
            **body,
            "network": None,
            "organization_id": None,
            "subject": subject,
            "allowed": True,
        }
        if change == "deny":
            value["allowed"] = False
        if change == "nonce":
            value["request_id"] = str(uuid.uuid4())
        if change == "subject":
            value["subject"] = "wrong"
        if change == "tenant":
            value["tenant_id"] = str(uuid.uuid4())
        if change == "action":
            value["action"] = "organization.admin"
        if change == "org":
            value["organization_id"] = str(uuid.uuid4())
        if change == "missing":
            value.pop("allowed")
        if change == "truthy":
            value["allowed"] = "true"
        return httpx.Response(200, json=value)

    original = httpx.AsyncClient

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        kwargs["transport"] = httpx.MockTransport(reply)
        return original(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    if change == "none":
        await require_current_approval(request, tenant, subject)
    else:
        with pytest.raises(HTTPException) as error:
            await require_current_approval(request, tenant, subject)
        assert error.value.status_code == (403 if change == "deny" else 503)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_scoped_approver_does_not_query_foreign_user_roles(monkeypatch):
    from aiuditor_service import approval_authority
    from aiuditor_service.private_models import routes

    monkeypatch.setenv("USER_SCOPED_AUTHORITY_REQUIRED", "1")
    tenant = uuid.uuid4()
    request = object()
    calls = []

    async def check(req, tid, subject):
        calls.append((req, tid, subject))

    async def foreign_sql(*args, **kwargs):
        raise AssertionError("User roles are foreign SQL")

    monkeypatch.setattr(routes, "get_active_tenant_role_member", foreign_sql)
    monkeypatch.setattr(approval_authority, "require_current_approval", check)
    await routes.approver(None, tenant, "verified-person", request)
    assert calls == [(request, tenant, "verified-person")]
    with pytest.raises(HTTPException) as refused:
        await routes.approver(None, tenant, "verified-person")
    assert refused.value.status_code == 403
