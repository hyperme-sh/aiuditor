from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from aiuditor_service.tenant import (
    AUDITOR_ACTOR_REQUIRED,
    BOUND_JWT_REQUIRED,
    HEADER_REQUIRED,
    TOKEN_TENANT_REQUIRED,
    require_auditor_actor,
    require_tenant_id,
    rls_tenant_id,
)
from conftest import DEFAULT_SUB, tenant_session_headers

TENANT_A = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
TENANT_B = uuid.UUID("11111111-2222-3333-4444-555555555555")


class _Req:
    def __init__(self, headers: dict, tenant_id=None):
        self.headers = headers
        self.state = type("S", (), {"tenant_id": tenant_id})()


def test_require_tenant_id_rejects_missing_header():
    with pytest.raises(HTTPException) as exc:
        require_tenant_id(_Req({}))
    assert exc.value.status_code == 400
    assert exc.value.detail == HEADER_REQUIRED


def test_require_tenant_id_rejects_header_only():
    with pytest.raises(HTTPException) as exc:
        require_tenant_id(_Req({"X-Tenant-ID": str(TENANT_A)}))
    assert exc.value.status_code == 401
    assert exc.value.detail == BOUND_JWT_REQUIRED


def test_require_tenant_id_uses_session_claim():
    assert require_tenant_id(_Req(tenant_session_headers(TENANT_A))) == TENANT_A


def test_require_tenant_id_rejects_mismatched_header():
    headers = tenant_session_headers(TENANT_A)
    headers["X-Tenant-ID"] = str(TENANT_B)
    with pytest.raises(HTTPException) as exc:
        require_tenant_id(_Req(headers))
    assert exc.value.status_code == 403


@pytest.mark.parametrize(
    "secret",
    ["hm_dev_x", "hm_gw_x", "hm_site_x", "hm1.ticket"],
)
def test_require_tenant_id_refuses_hypermesh_machine_prefixes(secret: str):
    with pytest.raises(HTTPException) as exc:
        require_tenant_id(
            _Req(
                {
                    "X-Tenant-ID": str(TENANT_A),
                    "Authorization": f"Bearer {secret}",
                }
            )
        )
    assert exc.value.status_code == 401
    assert exc.value.detail == BOUND_JWT_REQUIRED


def test_require_tenant_id_rejects_invalid_header():
    with pytest.raises(HTTPException) as exc:
        require_tenant_id(_Req({"X-Tenant-ID": "nope"}))
    assert exc.value.status_code == 400


def test_require_tenant_id_ignores_non_bearer():
    with pytest.raises(HTTPException) as exc:
        require_tenant_id(
            _Req(
                {
                    "X-Tenant-ID": str(TENANT_A),
                    "Authorization": "Basic not-a-grant",
                }
            )
        )
    assert exc.value.status_code == 401


def test_require_tenant_id_rejects_bound_jwt_without_tenant():
    token = _session_missing_tenant()
    with pytest.raises(HTTPException) as exc:
        require_tenant_id(
            _Req(
                {
                    "X-Tenant-ID": str(TENANT_A),
                    "Authorization": f"Bearer {token}",
                }
            )
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == TOKEN_TENANT_REQUIRED


def test_rls_tenant_id_bound_jwt_wins():
    assert rls_tenant_id(_Req(tenant_session_headers(TENANT_A))) == TENANT_A


def test_rls_tenant_id_header_is_not_a_grant():
    assert rls_tenant_id(_Req({"X-Tenant-ID": str(TENANT_A)})) is None


def test_rls_tenant_id_rejects_mismatch():
    headers = tenant_session_headers(TENANT_A)
    headers["X-Tenant-ID"] = str(TENANT_B)
    with pytest.raises(HTTPException) as exc:
        rls_tenant_id(_Req(headers))
    assert exc.value.status_code == 403


def test_rls_tenant_id_raw_jwt_does_not_bind():
    import base64
    import json

    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"sub": str(TENANT_A)}).encode()).decode().rstrip(
        "="
    )
    raw = f"{header}.{payload}.sig"
    assert (
        rls_tenant_id(
            _Req(
                {
                    "X-Tenant-ID": str(TENANT_A),
                    "Authorization": f"Bearer {raw}",
                }
            )
        )
        is None
    )


def _session_missing_tenant() -> str:
    import time

    import jwt

    from api.core.config import settings
    from api.infrastructure.security.tenant_session_jwt import ALG, ISSUER, SPANOPTICON_AUTH

    now = int(time.time())
    return jwt.encode(
        {
            "sub": DEFAULT_SUB,
            "iss": ISSUER,
            "exp": now + 900,
            "spanopticon_auth": SPANOPTICON_AUTH,
            "tenant_id": "",
            "https://spanopticon.dev/tenant_id": "",
        },
        settings.usable_api_key_exchange_secret(),
        algorithm=ALG,
    )


def test_require_auditor_actor_refuses_renter_exchange():
    from api.infrastructure.security.api_key_exchange_jwt import mint_api_key_session_jwt

    token = mint_api_key_session_jwt(
        key_record_id=uuid.uuid4(),
        tenant_id=TENANT_A,
        scopes=[],
        purpose="renter",
    )
    with pytest.raises(HTTPException) as exc:
        require_auditor_actor(
            _Req(
                {
                    "X-Tenant-ID": str(TENANT_A),
                    "Authorization": f"Bearer {token}",
                }
            )
        )
    assert exc.value.status_code == 401
    assert exc.value.detail == AUDITOR_ACTOR_REQUIRED


def test_require_auditor_actor_accepts_ir_exchange():
    from api.infrastructure.security.api_key_exchange_jwt import mint_api_key_session_jwt

    token = mint_api_key_session_jwt(
        key_record_id=uuid.uuid4(),
        tenant_id=TENANT_A,
        scopes=[],
        purpose="ir",
    )
    assert (
        require_auditor_actor(
            _Req(
                {
                    "X-Tenant-ID": str(TENANT_A),
                    "Authorization": f"Bearer {token}",
                }
            )
        )
        == TENANT_A
    )


def test_require_tenant_id_rejects_raw_jwt():
    import base64
    import json

    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"sub": str(TENANT_A)}).encode()).decode().rstrip(
        "="
    )
    raw = f"{header}.{payload}.sig"
    with pytest.raises(HTTPException) as exc:
        require_tenant_id(
            _Req(
                {
                    "X-Tenant-ID": str(TENANT_A),
                    "Authorization": f"Bearer {raw}",
                }
            )
        )
    assert exc.value.status_code == 401
