"""Tenant binding from a verified credential.

X-Tenant-ID is a cross-check, not the grant. Bound JWTs (tenant-session or
API-key exchange) carry the tenant. Hypermesh machine prefixes and raw
Keycloak tokens cannot pick a tenant by header. Site inbox stays /api/v1/auditor.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException, Request

HEADER_REQUIRED = "X-Tenant-ID header is required"
INVALID_HEADER = "Invalid X-Tenant-ID"
TENANT_MISMATCH = "X-Tenant-ID must match token tenant"
TOKEN_TENANT_REQUIRED = "token tenant required"
BOUND_JWT_REQUIRED = "tenant-session JWT or purpose=service exchange JWT required"
AUDITOR_ACTOR_REQUIRED = (
    "tenant-session JWT or purpose=ir or purpose=service exchange required"
)
_AUDITOR_EXCHANGE_PURPOSES = frozenset({"ir", "service"})

TENANT_CLAIM = "https://spanopticon.dev/tenant_id"
_MACHINE_PREFIXES = ("hm_msi_", "hm_ct_", "hm_dev_", "hm_gw_", "hm_site_", "hm1.")


def bearer_secret(request: Request) -> str | None:
    raw = request.headers.get("Authorization")
    if not raw:
        return None
    scheme, _, token = raw.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _header_tenant(request: Request) -> uuid.UUID | None:
    raw = request.headers.get("X-Tenant-ID")
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=INVALID_HEADER) from exc


def _machine_bearer(secret: str) -> bool:
    return secret.startswith(_MACHINE_PREFIXES)


def _claims_tenant(claims: dict[str, Any]) -> uuid.UUID | None:
    raw = claims.get(TENANT_CLAIM) or claims.get("tenant_id")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        return None


def _bound_jwt_claims(secret: str) -> dict[str, Any] | None:
    try:
        from controlplane_legacy_auth_bridge import (
            try_decode_tenant_session_jwt,
        )
    except Exception:
        try_decode_tenant_session_jwt = None
    if try_decode_tenant_session_jwt is not None:
        session = try_decode_tenant_session_jwt(secret)
        if session is not None:
            return session
    try:
        from controlplane_legacy_auth_bridge import (
            try_decode_api_key_session_jwt,
        )
    except Exception:
        return None
    return try_decode_api_key_session_jwt(secret)


def _tenant_from_bound_jwt(secret: str) -> uuid.UUID | None:
    """Return the tenant claim from a bound JWT, or None if this is not one.

    A bound JWT with no tenant claim is refused. A raw Keycloak (or other)
    bearer is not a bound JWT — callers must not fall back to the header.
    """

    claims = _bound_jwt_claims(secret)
    if claims is None:
        return None
    claimed = _claims_tenant(claims)
    if claimed is None:
        raise HTTPException(status_code=403, detail=TOKEN_TENANT_REQUIRED)
    return claimed


def require_tenant_id(request: Request) -> uuid.UUID:
    header = _header_tenant(request)
    secret = bearer_secret(request)

    if secret is not None and _machine_bearer(secret):
        raise HTTPException(status_code=401, detail=BOUND_JWT_REQUIRED)

    if secret is not None:
        claimed = _tenant_from_bound_jwt(secret)
        if claimed is not None:
            if header is not None and header != claimed:
                raise HTTPException(status_code=403, detail=TENANT_MISMATCH)
            request.state.tenant_id = claimed
            return claimed
        raise HTTPException(status_code=401, detail=BOUND_JWT_REQUIRED)

    if header is not None:
        raise HTTPException(status_code=401, detail=BOUND_JWT_REQUIRED)
    raise HTTPException(status_code=400, detail=HEADER_REQUIRED)


def require_auditor_actor(request: Request) -> uuid.UUID:
    """Bound session or IR/service exchange. A renter key cannot use a connection."""

    tenant_id = require_tenant_id(request)
    secret = bearer_secret(request)
    if secret is None:
        raise HTTPException(status_code=401, detail=AUDITOR_ACTOR_REQUIRED)
    try:
        from controlplane_legacy_auth_bridge import (
            try_decode_tenant_session_jwt,
        )
    except Exception:
        try_decode_tenant_session_jwt = None
    if try_decode_tenant_session_jwt is not None:
        session = try_decode_tenant_session_jwt(secret)
        if session is not None:
            return tenant_id
    try:
        from controlplane_legacy_auth_bridge import (
            try_decode_api_key_session_jwt,
        )
    except Exception:
        raise HTTPException(status_code=401, detail=AUDITOR_ACTOR_REQUIRED)
    exchange = try_decode_api_key_session_jwt(secret)
    if exchange is None:
        raise HTTPException(status_code=401, detail=AUDITOR_ACTOR_REQUIRED)
    purpose = str(exchange.get("purpose") or "").strip().lower()
    if purpose not in _AUDITOR_EXCHANGE_PURPOSES:
        raise HTTPException(status_code=401, detail=AUDITOR_ACTOR_REQUIRED)
    return tenant_id


def rls_tenant_id(request: Request) -> uuid.UUID | None:
    """Tenant for the request GUC. Bound JWT wins. Header is not a grant."""

    secret = bearer_secret(request)
    if secret is not None and not _machine_bearer(secret):
        claims = _bound_jwt_claims(secret)
        if claims is not None:
            claimed = _claims_tenant(claims)
            if claimed is None:
                raise HTTPException(status_code=403, detail=TOKEN_TENANT_REQUIRED)
            header = _header_tenant(request)
            if header is not None and header != claimed:
                raise HTTPException(status_code=403, detail=TENANT_MISMATCH)
            return claimed
        if _looks_like_jwt(secret):
            return None
    return None


def _looks_like_jwt(secret: str) -> bool:
    if _machine_bearer(secret):
        return False
    parts = secret.split(".")
    return len(parts) == 3 and all(parts)
