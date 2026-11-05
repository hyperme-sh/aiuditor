"""Adopted identity checks must run outside the request's event loop."""
import threading
import asyncio
import jwt
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from controlplane_identity import account_lifecycle
from controlplane_identity.configuration import settings
from controlplane_identity.security.tenant_session_jwt import mint_tenant_session_jwt
from aiuditor_service.approvals import resolver_subject
from aiuditor_service.middleware import AiuditorServiceDbMiddleware


@pytest.fixture(params=["canonical", "legacy"])
def lifecycle(monkeypatch, tenant_a, request):
    monkeypatch.setenv("ACCOUNT_CLOSURE_ENFORCEMENT_ENABLED", "true")
    monkeypatch.setenv("ACCOUNT_CLOSURE_GUARD_URL", "https://closure.invalid")
    monkeypatch.setenv("ACCOUNT_CLOSURE_GUARD_TOKEN", "g" * 40)
    monkeypatch.setattr(settings, "api_key_exchange_jwt_secret", "canonical-test-secret-at-least-32-characters")
    calls = []
    state = {"blocked": False}
    thread = threading.get_ident()

    async def post(url, **kwargs):
        assert threading.get_ident() != thread
        assert kwargs["body"]["tenant_id"] == str(tenant_a)
        calls.append(kwargs["body"])
        if state["blocked"] == "unavailable":
            raise OSError("offline")
        return {"blocked": state["blocked"]}

    monkeypatch.setattr(account_lifecycle, "_post_json", post)
    token = mint_tenant_session_jwt(
        claims={"sub": "human-1", "account_type": "human", "amr": ["pwd"]},
        tenant_id=tenant_a, extra_roles=["ir"], extra_permissions=["operator:program"],
    )
    if request.param == "legacy":
        monkeypatch.setenv("CONTROLPLANE_ACCEPT_LEGACY_AUTH", "1")
        claims = jwt.decode(token, settings.usable_api_key_exchange_secret(), algorithms=["HS256"], options={"verify_aud": False})
        claims["iss"] = "https://panopticon.internal/tenant-session"
        claims["panopticon_auth"] = claims.pop("spanopticon_auth")
        claims["https://panopticon.dev/tenant_id"] = claims.pop("https://spanopticon.dev/tenant_id")
        token = jwt.encode(claims, settings.usable_api_key_exchange_secret(), algorithm="HS256")
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-ID": str(tenant_a)}
    return headers, state, calls


@pytest.mark.parametrize("blocked", [False, True, "unavailable"])
async def test_async_tenant_and_human_routes_enforce_lifecycle(http_client, app, lifecycle, blocked):
    headers, state, calls = lifecycle
    state["blocked"] = blocked
    response = await http_client.get("/api/v1/aiuditor/connections", headers=headers)
    assert response.status_code == (200 if blocked is False else 401)
    request = Request({"type": "http", "method": "GET", "path": "/", "app": app,
                       "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]})
    if blocked is False:
        assert await resolver_subject(request) == "human-1"
    else:
        with pytest.raises(HTTPException) as exc:
            await resolver_subject(request)
        assert exc.value.status_code == 401
    assert calls


@pytest.mark.parametrize("blocked", [False, True, "unavailable"])
async def test_middleware_binds_only_live_credential(monkeypatch, lifecycle, tenant_a, blocked):
    from aiuditor_service import middleware
    headers, state, calls = lifecycle
    state["blocked"] = blocked
    gucs = []

    class Session:
        async def commit(self): pass
        async def rollback(self): pass

    @asynccontextmanager
    async def maker():
        yield Session()

    async def set_guc(session, tenant): gucs.append(tenant)
    monkeypatch.setattr(middleware, "get_session_maker", lambda: maker)
    monkeypatch.setattr(middleware, "set_current_tenant_id_guc", set_guc)
    request = Request({"type": "http", "method": "GET", "path": "/test",
                       "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]})

    async def next_handler(request):
        assert request.state.tenant_id == (tenant_a if blocked is False else None)
        return Response()

    await AiuditorServiceDbMiddleware(SimpleNamespace()).dispatch(request, next_handler)
    assert gucs == ([None, tenant_a] if blocked is False else [None])
    assert calls



@pytest.mark.parametrize("blocked", [False, True, "unavailable"])
async def test_operator_program_uses_current_account_authority(app, lifecycle, blocked):
    from aiuditor_service.interfaces.api.routes import _require_operator_program
    headers, state, calls = lifecycle
    state["blocked"] = blocked
    request = Request({"type": "http", "method": "POST", "path": "/suite", "app": app,
                       "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]})
    if blocked is False:
        await asyncio.to_thread(_require_operator_program, request, allow_service_seed=False)
    else:
        with pytest.raises(HTTPException) as exc:
            await asyncio.to_thread(_require_operator_program, request, allow_service_seed=False)
        assert exc.value.status_code == 403
    assert calls
