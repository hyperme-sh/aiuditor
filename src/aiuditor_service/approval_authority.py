"""Current approval authority from User; interactive proof remains local."""

import os
import ssl
from urllib.parse import urlsplit
import uuid

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt
from aiuditor_service.tenant import bearer_secret


class AuthorityReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: StrictInt
    request_id: uuid.UUID
    action: str
    network: str | None
    tenant_id: uuid.UUID
    organization_id: uuid.UUID | None
    subject: str
    allowed: StrictBool


async def require_current_approval(request, tenant, subject):
    try:
        token = bearer_secret(request)
        origin = os.environ.get("USER_AUTHORITY_URL", "").rstrip("/")
        target = urlsplit(origin)
        if (
            not token
            or target.scheme != "https"
            or not target.hostname
            or target.username
            or target.password
            or target.path
            or target.query
            or target.fragment
        ):
            raise ValueError("Verified caller and owner origin required")
        nonce = uuid.uuid4()
        context = ssl.create_default_context(
            cafile=os.environ.get("USER_AUTHORITY_CA_FILE") or None
        )
        async with httpx.AsyncClient(
            timeout=8, follow_redirects=False, trust_env=False, verify=context
        ) as client:
            response = await client.post(
                origin + "/api/v1/authority/check",
                json={
                    "version": 1,
                    "request_id": str(nonce),
                    "action": "approval.resolve",
                    "tenant_id": str(tenant),
                },
                headers={
                    "Authorization": "Bearer " + token,
                    "X-Tenant-ID": str(tenant),
                },
            )
            if response.status_code in (401, 403):
                raise HTTPException(
                    response.status_code,
                    "Current network or tenant approval authority required",
                )
            response.raise_for_status()
            fact = AuthorityReply.model_validate(response.json())
        if (
            fact.version != 1
            or fact.request_id != nonce
            or fact.action != "approval.resolve"
            or fact.network is not None
            or fact.organization_id is not None
            or fact.tenant_id != tenant
            or fact.subject != subject
        ):
            raise ValueError("Approval authority response binding differs")
        if not fact.allowed:
            raise HTTPException(
                403, "Current network or tenant approval authority required"
            )
    except (OSError, UnicodeError, ValueError, TypeError, httpx.HTTPError):
        raise HTTPException(503, "Approval authority is unavailable") from None
