"""AIuditor HTTP API under /api/v1/aiuditor.

Domain: connections. Classes, devices, and certs stay on Hypermesh.
Site inbox stays /api/v1/auditor. The door stays products/hypermesh-gateway.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request, status

from api.infrastructure.messaging.tenant_event_publisher import get_tenant_event_publisher

from aiuditor_service.approvals import (
    APPROVAL_NOT_FOUND,
    expire_due,
    hold_approval,
    require_hold_actor,
    resolve_approval,
    resolver_subject,
)
from aiuditor_service.credentials import credential_ref
from aiuditor_service.domain.models import (
    Approval,
    ApprovalCreate,
    ApprovalResolve,
    AuditorGrant,
    Connection,
    ConnectionAssign,
    ConnectionClassify,
    ConnectionCreate,
    ConnectionKind,
    ConnectionPurpose,
    ConnectionStatus,
    ConnectionTicket,
)
from aiuditor_service.events import (
    APPROVAL_HELD_V1,
    APPROVAL_RESOLVED_V1,
    CONNECTION_ASSIGNED_V1,
    CONNECTION_CREATED_V1,
)
from aiuditor_service.hypermesh import (
    HOST_LOOKUP_FAILED,
    HOST_ROLE,
    PLANE_HOP_FAILED,
    HostLookupUnavailable,
)
from aiuditor_service.provider import ProviderUnavailable
from aiuditor_service.store import ApprovalStore, ConnectionStore
from aiuditor_service.tenant import require_auditor_actor, require_tenant_id

router = APIRouter(prefix="/aiuditor", tags=["aiuditor"])

DEVICE_NOT_FOUND = "Device not found"
DEVICE_NOT_ENROLLED = "device is not enrolled"
DEVICE_ROLE_NOT_HOST = "device role is not host"
HOST_ALREADY_ASSIGNED = "host device already assigned for this purpose"
CONNECTION_NOT_FOUND = "Connection not found"
LEASE_NOT_FOUND = "Lease not found"
LEASE_DEVICE_MISMATCH = "lease device does not match this connection"
LEASE_IS_AUDITOR = "auditor tickets are minted; they are not bound as renter leases"
LEASE_ALREADY_ASSIGNED = "lease already assigned"
CONNECTION_ALREADY_BOUND = "connection already has a lease"
HOST_TICKET_ONLY = "only a purpose=auditor host connection can mint a ticket"
LEASE_ASSIGN_ONLY = "only a purpose=lease host connection can bind a renter lease"
PROVIDER_CLASSIFY_ONLY = (
    "only a purpose=auditor provider connection can classify over HTTPS"
)
CONNECTION_DISABLED = "connection is disabled"
RENTER_PURPOSE = frozenset({"renter", "service"})
_PLANE_CONFLICTS = frozenset(
    {
        "device has a renter lease",
        "device already has an auditor ticket",
        "device is not enrolled",
        "device role is not host",
        LEASE_IS_AUDITOR,
        LEASE_DEVICE_MISMATCH,
    }
)


def _store(request: Request) -> ConnectionStore:
    store = getattr(request.app.state, "connection_store", None)
    if store is None:
        store = ConnectionStore()
        request.app.state.connection_store = store
    return store


def _approvals(request: Request) -> ApprovalStore:
    store = getattr(request.app.state, "approval_store", None)
    if store is None:
        store = ApprovalStore()
        request.app.state.approval_store = store
    return store


@router.get("/connections", response_model=list[Connection])
async def list_connections(request: Request) -> list[Connection]:
    tenant_id = require_tenant_id(request)
    return _store(request).list_for(tenant_id)


def _forward_headers(request: Request) -> dict[str, str]:
    auth = request.headers.get("Authorization")
    hop: dict[str, str] = {}
    if auth:
        hop["Authorization"] = auth
    return hop


async def _require_enrolled_host(request: Request, tenant_id: uuid.UUID, device_id: uuid.UUID) -> None:
    lookup = getattr(request.app.state, "host_lookup", None)
    if lookup is None:
        raise HTTPException(status_code=503, detail=HOST_LOOKUP_FAILED)
    try:
        view = await lookup.get(tenant_id, device_id, headers=_forward_headers(request))
    except HostLookupUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if view is None:
        raise HTTPException(status_code=404, detail=DEVICE_NOT_FOUND)
    if view.tenant_id is not None and view.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail=DEVICE_NOT_FOUND)
    if not view.enrolled:
        raise HTTPException(status_code=409, detail=DEVICE_NOT_ENROLLED)
    if view.role != HOST_ROLE:
        raise HTTPException(status_code=409, detail=DEVICE_ROLE_NOT_HOST)


@router.post("/connections", response_model=Connection, status_code=status.HTTP_201_CREATED)
async def create_connection(request: Request, body: ConnectionCreate) -> Connection:
    tenant_id = require_tenant_id(request)
    store = _store(request)
    if body.kind is ConnectionKind.HOST:
        assert body.device_id is not None
        if store.host_for(tenant_id, body.device_id, body.purpose) is not None:
            raise HTTPException(status_code=409, detail=HOST_ALREADY_ASSIGNED)
        await _require_enrolled_host(request, tenant_id, body.device_id)
    hashed = credential_ref(body.credential) if body.credential else None
    row = Connection(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        kind=body.kind,
        purpose=body.purpose,
        device_id=body.device_id,
        endpoint=body.endpoint,
        credential_ref=hashed,
        lease_id=None,
        status=(
            ConnectionStatus.READY
            if body.kind is ConnectionKind.HOST or hashed
            else ConnectionStatus.PENDING
        ),
        created_at=datetime.now(UTC),
    )
    _store(request).put(row)
    await get_tenant_event_publisher().publish(
        tenant_id=tenant_id,
        event_type=CONNECTION_CREATED_V1,
        payload=row.to_event_payload(),
    )
    return row


@router.get("/connections/{connection_id}", response_model=Connection)
async def get_connection(request: Request, connection_id: uuid.UUID) -> Connection:
    tenant_id = require_tenant_id(request)
    row = _store(request).get(tenant_id, connection_id)
    if row is None:
        raise HTTPException(status_code=404, detail=CONNECTION_NOT_FOUND)
    return row


def _plane_error(exc: HostLookupUnavailable) -> HTTPException:
    detail = str(exc)
    if detail == DEVICE_NOT_FOUND:
        return HTTPException(status_code=404, detail=detail)
    if detail in _PLANE_CONFLICTS:
        return HTTPException(status_code=409, detail=detail)
    return HTTPException(status_code=503, detail=detail)


def _connection_or_404(request: Request, tenant_id: uuid.UUID, connection_id: uuid.UUID) -> Connection:
    row = _store(request).get(tenant_id, connection_id)
    if row is None:
        raise HTTPException(status_code=404, detail=CONNECTION_NOT_FOUND)
    return row


async def _bind_lease(
    request: Request,
    row: Connection,
    lease_id: uuid.UUID,
    *,
    purpose: str,
    exp: datetime | None = None,
) -> Connection:
    del exp
    store = _store(request)
    taken = store.lease_for(row.tenant_id, lease_id)
    if taken is not None and taken.id != row.id:
        raise HTTPException(status_code=409, detail=LEASE_ALREADY_ASSIGNED)
    bound = row.model_copy(update={"lease_id": lease_id, "status": ConnectionStatus.READY})
    store.put(bound)
    await get_tenant_event_publisher().publish(
        tenant_id=row.tenant_id,
        event_type=CONNECTION_ASSIGNED_V1,
        payload=bound.to_event_payload(),
    )
    return bound


@router.post(
    "/connections/{connection_id}/assign",
    response_model=Connection,
    summary="Bind a purpose=lease host to a Hypermesh renter lease",
)
async def assign_connection(
    request: Request, connection_id: uuid.UUID, body: ConnectionAssign
) -> Connection:
    tenant_id = require_auditor_actor(request)
    row = _connection_or_404(request, tenant_id, connection_id)
    if row.kind is not ConnectionKind.HOST or row.purpose is not ConnectionPurpose.LEASE:
        raise HTTPException(status_code=409, detail=LEASE_ASSIGN_ONLY)
    if row.status is ConnectionStatus.DISABLED:
        raise HTTPException(status_code=409, detail=CONNECTION_DISABLED)
    if row.lease_id is not None:
        raise HTTPException(status_code=409, detail=CONNECTION_ALREADY_BOUND)
    plane = getattr(request.app.state, "plane_hop", None)
    if plane is None:
        raise HTTPException(status_code=503, detail=PLANE_HOP_FAILED)
    try:
        lease = await plane.get_lease(
            tenant_id, body.lease_id, headers=_forward_headers(request)
        )
    except HostLookupUnavailable as exc:
        raise _plane_error(exc) from exc
    if lease is None:
        raise HTTPException(status_code=404, detail=LEASE_NOT_FOUND)
    if lease.tenant_id is not None and lease.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail=LEASE_NOT_FOUND)
    if lease.purpose not in RENTER_PURPOSE:
        raise HTTPException(status_code=409, detail=LEASE_IS_AUDITOR)
    if row.device_id is None or lease.device_id != row.device_id:
        raise HTTPException(status_code=409, detail=LEASE_DEVICE_MISMATCH)
    return await _bind_lease(request, row, lease.id, purpose=lease.purpose)


@router.post(
    "/connections/{connection_id}/ticket",
    response_model=AuditorGrant,
    status_code=status.HTTP_201_CREATED,
    summary="Mint a purpose=ir ticket for a purpose=auditor host",
)
async def mint_connection_ticket(
    request: Request, connection_id: uuid.UUID, body: ConnectionTicket
) -> AuditorGrant:
    tenant_id = require_auditor_actor(request)
    row = _connection_or_404(request, tenant_id, connection_id)
    if row.kind is not ConnectionKind.HOST or row.purpose is not ConnectionPurpose.AUDITOR:
        raise HTTPException(status_code=409, detail=HOST_TICKET_ONLY)
    if row.status is ConnectionStatus.DISABLED:
        raise HTTPException(status_code=409, detail=CONNECTION_DISABLED)
    if row.device_id is None:
        raise HTTPException(status_code=409, detail=HOST_TICKET_ONLY)
    if row.lease_id is not None:
        raise HTTPException(status_code=409, detail=CONNECTION_ALREADY_BOUND)
    plane = getattr(request.app.state, "plane_hop", None)
    if plane is None:
        raise HTTPException(status_code=503, detail=PLANE_HOP_FAILED)
    try:
        grant = await plane.mint_auditor_ticket(
            tenant_id,
            row.device_id,
            reserved_hours=body.reserved_hours,
            headers=_forward_headers(request),
        )
    except HostLookupUnavailable as exc:
        raise _plane_error(exc) from exc
    if grant.purpose != "ir":
        raise HTTPException(status_code=409, detail="auditor ticket must be purpose=ir")
    exp = None
    if grant.exp:
        try:
            exp = datetime.fromisoformat(grant.exp)
        except ValueError:
            exp = None
    bound = await _bind_lease(request, row, grant.lease_id, purpose=grant.purpose, exp=exp)
    return AuditorGrant(
        connection_id=bound.id,
        lease_id=grant.lease_id,
        purpose=grant.purpose,
        device_id=grant.device_id,
        exp=exp,
    )


@router.post(
    "/connections/{connection_id}/classify",
    summary="AIuditor-owned HTTPS hop on a provider connection",
)
async def classify_connection(
    request: Request, connection_id: uuid.UUID, body: ConnectionClassify
) -> dict[str, int]:
    tenant_id = require_auditor_actor(request)
    row = _connection_or_404(request, tenant_id, connection_id)
    if (
        row.kind is not ConnectionKind.PROVIDER
        or row.purpose is not ConnectionPurpose.AUDITOR
    ):
        raise HTTPException(status_code=409, detail=PROVIDER_CLASSIFY_ONLY)
    if row.status is ConnectionStatus.DISABLED:
        raise HTTPException(status_code=409, detail=CONNECTION_DISABLED)
    if not row.endpoint:
        raise HTTPException(status_code=409, detail=PROVIDER_CLASSIFY_ONLY)
    hop = getattr(request.app.state, "provider_hop", None)
    if hop is None:
        raise HTTPException(status_code=503, detail="provider hop failed")
    one_shot = request.headers.get("X-Provider-Authorization")
    try:
        upstream = await hop.complete(
            row.endpoint,
            messages=body.messages,
            model=body.model,
            authorization=one_shot,
        )
    except ProviderUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if 200 <= upstream < 300 and row.status is ConnectionStatus.PENDING:
        _store(request).put(row.model_copy(update={"status": ConnectionStatus.READY}))
    return {"upstream_status": upstream}


@router.post(
    "/approvals",
    response_model=Approval,
    status_code=status.HTTP_201_CREATED,
    summary="Hold an AI-use ask for a named human",
    description=(
        "AIuditor inbox. Not /api/v1/auditor and not privilege_grant. "
        "No prompt body. Internal token (door) or AIuditor grant."
    ),
)
async def create_approval(request: Request, body: ApprovalCreate) -> Approval:
    tenant_id = require_hold_actor(request)
    row = hold_approval(_approvals(request), tenant_id, body)
    await get_tenant_event_publisher().publish(
        tenant_id=tenant_id,
        event_type=APPROVAL_HELD_V1,
        payload=row.to_event_payload(),
    )
    return row


@router.get("/approvals", response_model=list[Approval], summary="Operator inbox")
async def list_approvals(
    request: Request,
    approval_status: str | None = Query(default=None, alias="status"),
) -> list[Approval]:
    tenant_id = require_auditor_actor(request)
    store = _approvals(request)
    expire_due(store, tenant_id)
    return store.list_for(tenant_id, status=approval_status)


@router.get("/approvals/{approval_id}", response_model=Approval)
async def get_approval(request: Request, approval_id: uuid.UUID) -> Approval:
    tenant_id = require_auditor_actor(request)
    store = _approvals(request)
    expire_due(store, tenant_id)
    row = store.get(tenant_id, approval_id)
    if row is None:
        raise HTTPException(status_code=404, detail=APPROVAL_NOT_FOUND)
    return row


@router.post(
    "/approvals/{approval_id}/resolve",
    response_model=Approval,
    summary="Allow-once, allow-session, or deny. LLM is never resolved_by.",
)
async def resolve_approval_route(
    request: Request, approval_id: uuid.UUID, body: ApprovalResolve
) -> Approval:
    resolved_by = resolver_subject(request)
    tenant_id = require_auditor_actor(request)
    row = resolve_approval(
        _approvals(request),
        tenant_id,
        approval_id,
        decision=body.decision,
        resolved_by=resolved_by,
    )
    await get_tenant_event_publisher().publish(
        tenant_id=tenant_id,
        event_type=APPROVAL_RESOLVED_V1,
        payload=row.to_event_payload(),
    )
    return row
