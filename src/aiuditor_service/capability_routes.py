"""Owner-managed opt-in policies and enrolled-machine capability review."""

import os
import time

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select

from aiuditor_service.capability_contracts import CapabilityPolicy, CapabilityReview, review, verify_signature
from aiuditor_service.machine_identity import match, transaction
from aiuditor_service.models import AiuditorMachineIdentity
from aiuditor_service.private_models.routes import approver, human
from aiuditor_service.role import require_grant_write, role_from_request

router = APIRouter(prefix="/aiuditor/capabilities", tags=["host-capabilities"])


async def registration(session, tenant, plane, device, request_id):
    row = await session.scalar(
        select(AiuditorMachineIdentity)
        .where(*match(tenant, plane, device))
        .with_for_update()
    )
    if row is None or row.request_id != request_id:
        raise HTTPException(401, "Unknown machine registration")
    if row.revoked:
        raise HTTPException(410, "Machine authority revoked")
    return row


@router.put("/policy")
async def replace_policy(request: Request, body: CapabilityPolicy, response: Response):
    """Replace the complete opt-in set; an empty set disables all capabilities."""
    tenant, subject = human(request)
    response.headers["Cache-Control"] = "no-store"
    now = int(time.time())
    if any(not now < grant.expires_at <= now + 86400 for grant in body.grants):
        raise HTTPException(400, "Capability grants require expiry within one day")
    async with transaction(tenant) as session:
        await approver(session, tenant, subject, request)
        row = await registration(session, tenant, body.control_plane_id, body.device_id, body.request_id)
        # No prompts, machine credentials or human session tokens are retained.
        # Replacing policy preserves consumed calls and the signing-key state.
        row.state = {**row.state, "capability_policy": body.model_dump(mode="json")}
    return {"protocol_version": 1, "grant_count": len(body.grants)}


@router.post("/review")
async def review_call(request: Request, body: CapabilityReview, response: Response):
    require_grant_write(role_from_request(request))
    response.headers["Cache-Control"] = "no-store"
    if body.authority_id != os.environ.get("AIUDITOR_ID", "local"):
        raise HTTPException(401, "Capability authority mismatch")
    tenant_header = request.headers.get("X-Tenant-ID")
    if tenant_header is not None and tenant_header != str(body.tenant_id):
        raise HTTPException(403, "Capability tenant mismatch")
    # Authenticate the signed tenant before opening the owner transaction. A
    # header, JWT or internal token alone cannot authorize this endpoint.
    verify_signature(body)
    async with transaction(body.tenant_id) as session:
        row = await registration(session, body.tenant_id, body.control_plane_id, body.device_id, body.request_id)
        row.state, decision = review(row.state, body)
    return decision
