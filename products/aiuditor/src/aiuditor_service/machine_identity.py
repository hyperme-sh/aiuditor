"""Credential authority behind authenticated control-plane attestations.

Each independently authenticated control plane can bind only its own namespace.
These host-management tokens never authorize contracts or paid workloads.
"""

from __future__ import annotations
import hashlib
import hmac
import json
import os
import uuid
from contextlib import asynccontextmanager
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from controlplane_tenancy.db_session import get_session_maker
from controlplane_tenancy.tenant_guc import set_current_tenant_id_guc
from aiuditor_service.models import AiuditorMachineIdentity
from aiuditor_service.machine_proof import Proof, issue, access_allowed
from aiuditor_service.role import require_grant_write, role_from_request

router = APIRouter(prefix="/aiuditor/machine", tags=["machine-identity"])


def actor(request):
    require_grant_write(role_from_request(request))
    plane = request.headers.get("X-Control-Plane-ID", "")
    provided = request.headers.get("X-Internal-Token", "")
    # Explicit registry for federation; the existing single-plane deployment
    # has a local entry only. Never let one shared token choose arbitrary IDs.
    raw = os.environ.get("AIUDITOR_CONTROL_PLANE_TOKENS")
    try:
        trusted = (
            json.loads(raw)
            if raw
            else {"local": os.environ.get("AIUDITOR_INTERNAL_TOKEN", "")}
        )
        expected = trusted.get(plane) if isinstance(trusted, dict) else None
    except ValueError:
        raise HTTPException(503, "Control-plane trust configuration invalid") from None
    if (
        not isinstance(expected, str)
        or not expected
        or not hmac.compare_digest(expected, provided)
    ):
        raise HTTPException(401, "Trusted control-plane attestation required")
    if not plane or len(plane) > 128:
        raise HTTPException(400, "Invalid control-plane identity")
    try:
        tid = uuid.UUID(request.headers.get("X-Tenant-ID", ""))
    except ValueError:
        raise HTTPException(400, "Tenant cross-check required") from None
    return tid, plane


@asynccontextmanager
async def transaction(tid):
    async with get_session_maker()() as session:
        try:
            await set_current_tenant_id_guc(session, tid)
            yield session
            await session.commit()
        except SQLAlchemyError:
            await session.rollback()
            raise HTTPException(503, "Machine identity storage unavailable") from None
        except BaseException:
            await session.rollback()
            raise


def match(tid, plane, device):
    return (
        AiuditorMachineIdentity.authority_id == os.environ.get("AIUDITOR_ID", "local"),
        AiuditorMachineIdentity.tenant_id == tid,
        AiuditorMachineIdentity.control_plane_id == plane,
        AiuditorMachineIdentity.device_id == device,
    )


async def exchange(request, body, response, bind):
    tid, plane = actor(request)
    validate_audience(body, plane)
    response.headers["Cache-Control"] = "no-store"
    async with transaction(tid) as session:
        if bind:
            # Insert does not replace an existing key, including concurrent binds.
            await session.execute(
                insert(AiuditorMachineIdentity)
                .values(
                    authority_id=os.environ.get("AIUDITOR_ID", "local"),
                    tenant_id=tid,
                    control_plane_id=plane,
                    device_id=body.device_id,
                    request_id=body.request_id,
                    state={},
                    revoked=False,
                )
                .on_conflict_do_nothing()
            )
        row = await session.scalar(
            select(AiuditorMachineIdentity)
            .where(*match(tid, plane, body.device_id))
            .with_for_update()
        )
        if row is None or row.request_id != body.request_id:
            raise HTTPException(401, "Unknown machine registration")
        if row.revoked:
            raise HTTPException(410, "Machine authority revoked")
        row.state, result = issue(row.state, tid, body, bind=bind)
    return result


@router.post("/bind")
async def bind(request: Request, body: Proof, response: Response):
    return await exchange(request, body, response, True)


@router.post("/token")
async def renew(request: Request, body: Proof, response: Response):
    return await exchange(request, body, response, False)


class Lookup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: uuid.UUID
    request_id: uuid.UUID
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")


@router.post("/introspect")
async def introspect(request: Request, body: Lookup, response: Response):
    tid, plane = actor(request)
    response.headers["Cache-Control"] = "no-store"
    async with transaction(tid) as session:
        row = await session.scalar(
            select(AiuditorMachineIdentity).where(*match(tid, plane, body.device_id))
        )
        active = bool(
            row
            and not row.revoked
            and row.request_id == body.request_id
            and access_allowed(row.state, body.digest)
        )
    return {"active": active}


class Revoke(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: uuid.UUID
    request_id: uuid.UUID


@router.post("/revoke")
async def revoke(request: Request, body: Revoke, response: Response):
    tid, plane = actor(request)
    response.headers["Cache-Control"] = "no-store"
    async with transaction(tid) as session:
        row = await session.scalar(
            select(AiuditorMachineIdentity)
            .where(*match(tid, plane, body.device_id))
            .with_for_update()
        )
        if row is None or row.request_id != body.request_id:
            raise HTTPException(404, "Machine registration not found")
        row.revoked = True
        row.state = {**row.state, "tokens": []}
    return {"revoked": True}


class ContractProof(Proof):
    contract_id: uuid.UUID


async def live_contract(session, tid, plane, device_id, contract_id):
    """Fresh authority: a signed, unexpired contract bound to this live connection."""
    from datetime import UTC, datetime
    from aiuditor_service.models import AiuditorContract, AiuditorConnection

    row = await session.scalar(
        select(AiuditorContract)
        .join(
            AiuditorConnection,
            (AiuditorConnection.id == AiuditorContract.connection_id)
            & (AiuditorConnection.tenant_id == AiuditorContract.tenant_id),
        )
        .where(
            AiuditorContract.tenant_id == tid,
            AiuditorContract.id == contract_id,
            AiuditorContract.status == "signed",
            AiuditorContract.until > datetime.now(UTC),
            AiuditorConnection.control_plane_id == plane,
            AiuditorConnection.device_id == device_id,
            AiuditorConnection.kind == "host",
            AiuditorConnection.status == "ready",
            AiuditorConnection.purpose == "lease",
        )
    )
    if row is None:
        raise HTTPException(403, "No active contract for this host")
    return row


@router.post("/contract-token")
async def contract_token(request: Request, body: ContractProof, response: Response):
    import secrets
    import time

    tid, plane = actor(request)
    validate_audience(body, plane)
    response.headers["Cache-Control"] = "no-store"
    async with transaction(tid) as session:
        row = await session.scalar(
            select(AiuditorMachineIdentity)
            .where(*match(tid, plane, body.device_id))
            .with_for_update()
        )
        if not row or row.revoked or row.request_id != body.request_id:
            raise HTTPException(401, "Unknown machine identity")
        if body.next_public_key:
            raise HTTPException(400, "Rotate using the identity endpoint")
        contract = await live_contract(
            session, tid, plane, body.device_id, body.contract_id
        )
        # Consume proof through the same lock/replay window; discard the newly
        # generated management credential rather than returning wider authority.
        state, _ = issue(row.state, tid, body)
        state["tokens"] = state["tokens"][:-1]
        now = int(time.time())
        grants = [g for g in state.get("contracts", []) if g["expires"] > now]
        if len(grants) >= 128:
            raise HTTPException(429, "Contract credential rate exceeded")
        token = "hm_ct_" + secrets.token_urlsafe(32)
        expires = min(now + 600, int(contract.until.timestamp()))
        grants.append(
            {
                "digest": hashlib.sha256(token.encode()).hexdigest(),
                "contract_id": str(body.contract_id),
                "expires": expires,
            }
        )
        row.state = {**state, "contracts": grants}
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": expires - now,
        "device_id": str(body.device_id),
        "contract_id": str(body.contract_id),
    }


class ContractLookup(Lookup):
    contract_id: uuid.UUID


@router.post("/contract-introspect")
async def contract_introspect(
    request: Request, body: ContractLookup, response: Response
):
    import time

    tid, plane = actor(request)
    response.headers["Cache-Control"] = "no-store"
    async with transaction(tid) as session:
        row = await session.scalar(
            select(AiuditorMachineIdentity).where(*match(tid, plane, body.device_id))
        )
        active = bool(
            row
            and not row.revoked
            and row.request_id == body.request_id
            and any(
                hmac.compare_digest(g["digest"], body.digest)
                and g["expires"] > time.time()
                and g["contract_id"] == str(body.contract_id)
                for g in row.state.get("contracts", [])
            )
        )
        if active:
            try:
                await live_contract(
                    session, tid, plane, body.device_id, body.contract_id
                )
            except HTTPException as exc:
                if exc.status_code != 403:
                    raise
                active = False
    return {"active": active}


def validate_audience(body, plane):
    if body.control_plane_id != plane or body.authority_id != os.environ.get(
        "AIUDITOR_ID", "local"
    ):
        raise HTTPException(403, "Machine proof belongs to another authority")


@router.post("/configuration")
async def configuration(request: Request, response: Response):
    _, plane = actor(request)
    response.headers["Cache-Control"] = "no-store"
    return {
        "authority_id": os.environ.get("AIUDITOR_ID", "local"),
        "control_plane_id": plane,
    }
