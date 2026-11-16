"""Short-lived, audience-bound permission for an enrolled phone peer.

Plane attests current ownership and the enrolled public key. Gateway verifies
the opaque grant with this authority before applying any network change.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from aiuditor_service import machine_identity as identities
from aiuditor_service.machine_proof import access_allowed
from aiuditor_service.models import AiuditorMachineIdentity

router = APIRouter(prefix="/aiuditor/machine", tags=["phone-mesh"])
TTL = 60


class MeshRequest(identities.Lookup):
    gateway_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    wg_public_key: str = Field(min_length=44, max_length=44)

    @field_validator("wg_public_key")
    @classmethod
    def canonical_key(cls, value):
        raw = base64.b64decode(value, validate=True)
        if len(raw) != 32 or not any(raw) or base64.b64encode(raw).decode() != value:
            raise ValueError("Canonical nonzero WireGuard public key required")
        return value


class MeshLookup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: uuid.UUID
    request_id: uuid.UUID
    control_plane_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    grant: str = Field(pattern=r"^hm_pm_[A-Za-z0-9_-]{43}$", repr=False)


def registry():
    try:
        value = json.loads(os.environ.get("AIUDITOR_PHONE_GATEWAYS", "{}"))
        if not isinstance(value, dict):
            raise ValueError()
        for entry in value.values():
            if (not isinstance(entry, dict) or not isinstance(entry.get("token"), str)
                    or len(entry["token"]) < 32
                    or not isinstance(entry.get("control_planes"), list)
                    or not all(isinstance(p, str) and p for p in entry["control_planes"])):
                raise ValueError()
        return value
    except (ValueError, TypeError):
        raise HTTPException(503, "Phone gateway trust configuration invalid") from None


@router.post("/client-mesh-grant")
async def authorize(request: Request, body: MeshRequest, response: Response):
    tid, plane = identities.actor(request)
    response.headers["Cache-Control"] = "no-store"
    target = registry().get(body.gateway_id)
    if not target or plane not in target["control_planes"]:
        raise HTTPException(403, "Phone gateway is not authorized for this plane")
    now = int(time.time())
    async with identities.transaction(tid) as session:
        row = await session.scalar(select(AiuditorMachineIdentity)
            .where(*identities.match(tid, plane, body.device_id)).with_for_update())
        if (not row or row.revoked or row.request_id != body.request_id
                or not access_allowed(row.state, body.digest, now, client=True)):
            raise HTTPException(403, "Current enrolled client credential required")
        # Enrollment fixes the key. A later attestation cannot rotate it under
        # the same registration or silently widen an existing peer's identity.
        bound = row.state.get("phone_wg_public_key")
        if bound is not None and bound != body.wg_public_key:
            raise HTTPException(409, "Phone key requires a new enrollment")
        grants = [g for g in row.state.get("phone_mesh_grants", []) if g["expires_at"] > now]
        if len(grants) >= 8:
            raise HTTPException(429, "Phone mesh grant budget exceeded")
        token = "hm_pm_" + secrets.token_urlsafe(32)
        expires = min(now + TTL, max(v[1] for v in row.state["tokens"]
                                    if hmac.compare_digest(v[0], body.digest)))
        grants.append({"digest": hashlib.sha256(token.encode()).hexdigest(),
                       "gateway_id": body.gateway_id, "expires_at": expires,
                       "credential_digest": body.digest})
        row.state = {**row.state, "phone_wg_public_key": body.wg_public_key,
                     "phone_mesh_grants": grants}
    return {"grant": token, "expires_at": expires, "gateway_id": body.gateway_id}


@router.post("/client-mesh-introspect")
async def inspect(request: Request, body: MeshLookup, response: Response):
    response.headers["Cache-Control"] = "no-store"
    gateway = request.headers.get("X-Phone-Gateway-ID", "")
    target = registry().get(gateway)
    supplied = request.headers.get("X-Phone-Gateway-Token", "")
    if (not target or not hmac.compare_digest(target["token"].encode(), supplied.encode())
            or body.control_plane_id not in target["control_planes"]):
        raise HTTPException(401, "Authenticated phone gateway required")
    try:
        tid = uuid.UUID(request.headers.get("X-Tenant-ID", ""))
    except ValueError:
        raise HTTPException(400, "Tenant cross-check required") from None
    digest = hashlib.sha256(body.grant.encode()).hexdigest()
    now = int(time.time())
    async with identities.transaction(tid) as session:
        row = await session.scalar(select(AiuditorMachineIdentity)
            .where(*identities.match(tid, body.control_plane_id, body.device_id)))
        if not row or row.revoked or row.request_id != body.request_id:
            return {"active": False}
        for grant in row.state.get("phone_mesh_grants", []):
            if (grant["gateway_id"] == gateway and grant["expires_at"] > now
                    and hmac.compare_digest(grant["digest"], digest)
                    and access_allowed(row.state, grant["credential_digest"], now, client=True)):
                return {"active": True, "tenant_id": str(tid),
                        "device_id": str(body.device_id), "request_id": str(body.request_id),
                        "control_plane_id": body.control_plane_id, "gateway_id": gateway,
                        "wg_public_key": row.state["phone_wg_public_key"],
                        "expires_at": grant["expires_at"]}
    return {"active": False}
