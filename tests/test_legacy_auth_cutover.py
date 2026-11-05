"""Exercise the real consumer boundary with a locally signed legacy fixture."""
import time

import jwt
import pytest
from fastapi import HTTPException

from api.core.config import settings
from aiuditor_service.tenant import require_tenant_id
from test_tenant_bind import _Req, TENANT_A, TENANT_B


@pytest.mark.parametrize("enabled", [False, True])
def test_legacy_session_is_opt_in_and_header_cannot_rebind(monkeypatch, enabled):
    monkeypatch.delenv("CONTROLPLANE_ACCEPT_LEGACY_AUTH", raising=False)
    if enabled:
        monkeypatch.setenv("CONTROLPLANE_ACCEPT_LEGACY_AUTH", "1")
    payload = {"iss": "https://panopticon.internal/tenant-session", "sub": "test-person",
               "exp": int(time.time()) + 300, "panopticon_auth": "tenant_session",
               "tenant_id": str(TENANT_A), "azp": "controlplane-frontend"}
    signed = jwt.encode(payload, settings.usable_api_key_exchange_secret(), algorithm="HS256")
    request = _Req({"Authorization": f"Bearer {signed}", "X-Tenant-ID": str(TENANT_A)})
    if enabled:
        assert require_tenant_id(request) == TENANT_A
        request.headers["X-Tenant-ID"] = str(TENANT_B)
        with pytest.raises(HTTPException) as error:
            require_tenant_id(request)
        assert error.value.status_code == 403
    else:
        with pytest.raises(HTTPException) as error:
            require_tenant_id(request)
        assert error.value.status_code == 401
