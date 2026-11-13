"""AIuditor HTTP API under /api/v1/aiuditor.

Domain: connections and the model cert program (envelopes, suite, admissions).
Path B, scorecards, and catalog stamps stay on Hypermesh.
Site inbox stays /api/v1/auditor. The door stays products/hypermesh-gateway.
"""

from __future__ import annotations

import asyncio

from aiuditor_service.lab_reporting import LabEnvelopeReport
from aiuditor_service.domain.models import AccountingContract, AccountingAllocation

import uuid
from datetime import UTC, datetime

import os

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from aiuditor_service.atomic_events import atomic_events, publish_owned_event, operation

from aiuditor_service.approvals import (
    APPROVAL_NOT_FOUND,
    consume_allow_once,
    expire_due,
    hold_approval,
    latest_for,
    require_hold_actor,
    require_program_reader,
    require_internal_actor,
    resolve_approval,
    resolver_subject,
)
from aiuditor_service.credentials import credential_ref
from aiuditor_service.catalog import (
    SERVICE_NOT_FOUND,
    allocate_service,
    put_service,
)
from aiuditor_service.contracts import (
    CONTRACT_NOT_FOUND,
    bind_contract,
    fulfill_checkout_contract,
    put_contract,
    require_contract_signer,
    require_signed_grant,
    sign_contract,
)
from aiuditor_service.allowed import allowed_host_set
from aiuditor_service.domain.models import (
    Admission,
    AllowedHost,
    AllowedSet,
    AdmissionCreate,
    AdmissionResolve,
    AdmissionQuarantineRetry,
    AdmissionSandboxReport,
    Approval,
    ApprovalCreate,
    ApprovalPolicy,
    ApprovalPolicyCreate,
    ApprovalResolve,
    AiuditorMeta,
    AuditorGrant,
    ClassEnvelope,
    ClassEnvelopeCreate,
    Connection,
    ConnectionAssign,
    ConnectionClassify,
    ConnectionCreate,
    ConnectionKind,
    ConnectionPurpose,
    ConnectionStatus,
    ConnectionTicket,
    CatalogService,
    ServiceAllocate,
    ServiceAllocation,
    Contract,
    ContractGrant,
    ContractBind,
    ContractCreate,
    ContractFulfill,
    ContractStatus,
    ContractViolation,
    ContractViolationCreate,
    CrawlCandidate,
    CrawlRunResult,
    GrantExport,
    HydrateSnapshot,
    ServiceCreate,
    SuiteMember,
    SuiteMemberCreate,
    SuiteMemberReplace,
)
from aiuditor_service.export import build_grant_export
from aiuditor_service.persist import MemoryPersistSession
from aiuditor_service.events import publish_contract
from aiuditor_service.events import (
    ADMISSION_RESOLVED_V1,
    ADMISSION_SUBMITTED_V1,
    APPROVAL_HELD_V1,
    APPROVAL_RESOLVED_V1,
    CONNECTION_ASSIGNED_V1,
    CONNECTION_CREATED_V1,
    CONTRACT_BOUND_V1,
    CONTRACT_CREATED_V1,
    CONTRACT_FULFILLED_V1,
    CONTRACT_SIGNED_V1,
    CONTRACT_VIOLATED_V1,
    CATALOG_SERVICE_CREATED_V1,
    CATALOG_SERVICE_ALLOCATED_V1,
    ENVELOPE_RECORDED_V1,
    POLICY_CREATED_V1,
    POLICY_DELETED_V1,
    SUITE_MEMBER_RECORDED_V1,
)
from aiuditor_service.policies import (
    POLICY_NOT_FOUND,
    put_policy,
)
from aiuditor_service.program import (
    ADMISSION_NOT_FOUND,
    ENVELOPE_NOT_FOUND,
    SUITE_MEMBER_NOT_FOUND,
    put_envelope,
    put_suite_member,
    resolve_admission,
    resolve_admission_async,
    seed_core_suite,
    replace_suite_member,
    submit_admission,
    submit_admission_async,
)
from aiuditor_service.hypermesh import (
    HOST_LOOKUP_FAILED,
    HOST_ROLE,
    PLANE_HOP_FAILED,
    HostLookupUnavailable,
)
from aiuditor_service.parent import (
    PARENT_REQUIRED,
    ClosedParentHop,
    ParentUnavailable,
    apply_hydrate,
    parent_identity,
    build_hydrate,
)
from aiuditor_service.provider import ProviderUnavailable
from aiuditor_service.role import (
    ROLE_HEADER,
    require_grant_write,
    require_market_mint,
    require_witness,
    role_from_request,
    writer_for,
)
from aiuditor_service.store import (
    AdmissionStore,
    ApprovalStore,
    CandidateStore,
    CatalogStore,
    ConnectionStore,
    ContractStore,
    EnvelopeStore,
    PolicyStore,
    StoreBundle,
    SuiteStore,
    ViolationStore,
)
from aiuditor_service.tenant import require_auditor_actor, require_tenant_id
from aiuditor_service.violations import (
    deny_count,
    evaluate_kind,
    put_violation,
)

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
        "Device already has a live lease",
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


def _policies(request: Request) -> PolicyStore:
    store = getattr(request.app.state, "policy_store", None)
    if store is None:
        store = PolicyStore()
        request.app.state.policy_store = store
    return store


def _contracts(request: Request) -> ContractStore:
    store = getattr(request.app.state, "contract_store", None)
    if store is None:
        store = ContractStore()
        request.app.state.contract_store = store
    return store


def _violations(request: Request) -> ViolationStore:
    store = getattr(request.app.state, "violation_store", None)
    if store is None:
        store = ViolationStore()
        request.app.state.violation_store = store
    return store


def _catalog(request: Request) -> CatalogStore:
    store = getattr(request.app.state, "catalog_store", None)
    if store is None:
        store = CatalogStore()
        request.app.state.catalog_store = store
    return store


def _envelopes(request: Request) -> EnvelopeStore:
    store = getattr(request.app.state, "envelope_store", None)
    if store is None:
        store = EnvelopeStore()
        request.app.state.envelope_store = store
    return store


def _require_operator_program(request: Request, *, allow_service_seed: bool = False) -> None:
    """Suite edits require operator:program on a verified tenant session.

    purpose=service automation may still seed. Support and purpose=ir cannot.
    """

    from aiuditor_service.tenant import bearer_secret, verified_credential_claims
    from api.domain.operator.claims import permissions_from_claims, program_may_edit_suite
    from controlplane_legacy_auth_bridge import (
        try_decode_api_key_session_jwt,
    )
    from controlplane_legacy_auth_bridge import (
        try_decode_tenant_session_jwt,
    )

    secret = bearer_secret(request)
    if not secret:
        raise HTTPException(status_code=403, detail="operator:program required")
    session = verified_credential_claims(request, "tenant-session", try_decode_tenant_session_jwt)
    if session is not None:
        if not program_may_edit_suite(permissions_from_claims(session)):
            raise HTTPException(status_code=403, detail="operator:program required")
        return
    exchange = verified_credential_claims(request, "api-key", try_decode_api_key_session_jwt)
    purpose = str((exchange or {}).get("purpose") or "").strip().lower()
    if allow_service_seed and purpose == "service":
        return
    raise HTTPException(status_code=403, detail="operator:program required")


def _suite(request: Request) -> SuiteStore:
    store = getattr(request.app.state, "suite_store", None)
    if store is None:
        store = SuiteStore()
        request.app.state.suite_store = store
    return store


def _admissions(request: Request) -> AdmissionStore:
    store = getattr(request.app.state, "admission_store", None)
    if store is None:
        store = AdmissionStore()
        request.app.state.admission_store = store
    return store


def _candidates(request: Request) -> CandidateStore:
    store = getattr(request.app.state, "candidate_store", None)
    if store is None:
        store = CandidateStore()
        request.app.state.candidate_store = store
    return store


def _stores(request: Request) -> StoreBundle:
    persist = getattr(request.app.state, "aiuditor_persist", None)
    return StoreBundle(
        persist=persist or MemoryPersistSession(),
        connection_store=_store(request),
        approval_store=_approvals(request),
        policy_store=_policies(request),
        contract_store=_contracts(request),
        violation_store=_violations(request),
        catalog_store=_catalog(request),
        envelope_store=_envelopes(request),
        suite_store=_suite(request),
        admission_store=_admissions(request),
        candidate_store=_candidates(request),
    )


@router.get(
    "/meta",
    response_model=AiuditorMeta,
    summary="Deployment role. Hypermesh checks writer before honoring a market grant.",
)
async def get_meta(request: Request) -> AiuditorMeta:
    role = role_from_request(request)
    parent = getattr(request.app.state, "parent_hop", None)
    return AiuditorMeta(
        role=role.value,
        writer=writer_for(role),
        parent=parent is not None and not isinstance(parent, ClosedParentHop),
    )


@router.get(
    "/hydrate",
    response_model=HydrateSnapshot,
    summary="Tenant snapshot a witness may pull. No prompt bodies or tickets.",
)
async def get_hydrate(request: Request) -> HydrateSnapshot:
    tenant_id = await asyncio.to_thread(require_hold_actor, request)
    return build_hydrate(
        tenant_id=tenant_id,
        role=role_from_request(request),
        stores=_stores(request),
    )


@router.post(
    "/hydrate/pull",
    response_model=HydrateSnapshot,
    summary="Witness pull from AIUDITOR_PARENT_URL. Does not fulfill.",
)
async def pull_hydrate(request: Request) -> HydrateSnapshot:
    require_witness(role_from_request(request))
    tenant_id = await asyncio.to_thread(require_hold_actor, request)
    hop = getattr(request.app.state, "parent_hop", None)
    if hop is None or isinstance(hop, ClosedParentHop):
        raise HTTPException(status_code=503, detail=PARENT_REQUIRED)
    try:
        snapshot = await hop.hydrate(tenant_id, headers=_forward_headers(request))
    except ParentUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc) or PARENT_REQUIRED) from exc
    return apply_hydrate(_stores(request), snapshot, tenant_id=tenant_id, parent_id=parent_identity(hop))


@router.get("/connections", response_model=list[Connection])
async def list_connections(request: Request) -> list[Connection]:
    tenant_id = await asyncio.to_thread(require_tenant_id, request)
    return _store(request).list_for(tenant_id)


@router.get(
    "/allowed",
    response_model=AllowedSet,
    summary="Hosts this tenant may lease",
    description=(
        "Every purpose=lease host connection that is not disabled. "
        "Does not allocate, does not open a Hypermesh lease, and does not "
        "replace POST /catalog/{service_id}/allocate (that call still binds one host)."
    ),
)
async def list_allowed(request: Request) -> AllowedSet:
    tenant_id = await asyncio.to_thread(require_tenant_id, request)
    hosts: list[AllowedHost] = []
    for row in allowed_host_set(_store(request).list_for(tenant_id)):
        if row.device_id is None:
            continue
        hosts.append(
            AllowedHost(
                connection_id=row.id,
                device_id=row.device_id,
                status=row.status,
                lease_id=row.lease_id,
            )
        )
    return AllowedSet(hosts=hosts)


def _forward_headers(request: Request) -> dict[str, str]:
    auth = request.headers.get("Authorization")
    hop: dict[str, str] = {}
    if auth:
        hop["Authorization"] = auth
    hop[ROLE_HEADER] = role_from_request(request).value
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
@atomic_events
async def create_connection(request: Request, body: ConnectionCreate) -> Connection:
    tenant_id = await asyncio.to_thread(require_tenant_id, request)
    plane = "local"
    if request.headers.get("X-Control-Plane-ID"):
        from aiuditor_service.machine_identity import actor
        attested_tenant, plane = actor(request)
        if attested_tenant != tenant_id:
            raise HTTPException(403, "Control-plane tenant mismatch")
    if body.control_plane_id is not None and body.control_plane_id != plane:
        # The body names the owner; only that plane's attestation may claim it.
        raise HTTPException(403, "Connection control plane must match the attesting plane")
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
        control_plane_id=plane,
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
    await publish_owned_event(request,
        tenant_id=tenant_id,
        event_type=CONNECTION_CREATED_V1,
        payload=row.to_event_payload(),
    )
    return row


@router.get("/connections/{connection_id}", response_model=Connection)
async def get_connection(request: Request, connection_id: uuid.UUID) -> Connection:
    tenant_id = await asyncio.to_thread(require_tenant_id, request)
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


@atomic_events
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
    await publish_owned_event(request,
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
    require_market_mint(role_from_request(request))
    tenant_id = await asyncio.to_thread(require_auditor_actor, request)
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
    require_market_mint(role_from_request(request))
    tenant_id = await asyncio.to_thread(require_auditor_actor, request)
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
    tenant_id = await asyncio.to_thread(require_auditor_actor, request)
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
@atomic_events
async def create_approval(request: Request, body: ApprovalCreate) -> Approval:
    tenant_id = await asyncio.to_thread(require_hold_actor, request)
    row = hold_approval(_approvals(request), tenant_id, body)
    await publish_owned_event(request,
        tenant_id=tenant_id,
        event_type=APPROVAL_HELD_V1,
        payload=row.to_event_payload(),
    )
    return row


@router.get("/approvals", response_model=list[Approval], summary="Operator inbox")
async def list_approvals(
    request: Request,
    approval_status: str | None = Query(default=None, alias="status"),
    lease_id: uuid.UUID | None = Query(default=None),
    who: str | None = Query(default=None),
) -> list[Approval]:
    tenant_id = await asyncio.to_thread(require_hold_actor, request)
    store = _approvals(request)
    expire_due(store, tenant_id)
    return store.list_for(
        tenant_id, status=approval_status, lease_id=lease_id, who=who
    )


@router.get(
    "/approvals/latest",
    response_model=Approval,
    summary="Door lookup for a lease + who",
)
async def latest_approval(
    request: Request,
    lease_id: uuid.UUID,
    who: str,
    prompt_class: str | None = Query(default=None),
) -> Approval:
    tenant_id = await asyncio.to_thread(require_hold_actor, request)
    store = _approvals(request)
    row = latest_for(
        store, tenant_id, lease_id=lease_id, who=who, prompt_class=prompt_class
    )
    if row is None:
        raise HTTPException(status_code=404, detail=APPROVAL_NOT_FOUND)
    return row


@router.get("/approvals/{approval_id}", response_model=Approval)
async def get_approval(request: Request, approval_id: uuid.UUID) -> Approval:
    tenant_id = await asyncio.to_thread(require_hold_actor, request)
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
@atomic_events
async def resolve_approval_route(
    request: Request, approval_id: uuid.UUID, body: ApprovalResolve
) -> Approval:
    resolved_by = await resolver_subject(request)
    tenant_id = await asyncio.to_thread(require_auditor_actor, request)
    row = resolve_approval(
        _approvals(request),
        tenant_id,
        approval_id,
        decision=body.decision,
        resolved_by=resolved_by,
    )
    await publish_owned_event(request,
        tenant_id=tenant_id,
        event_type=APPROVAL_RESOLVED_V1,
        payload=row.to_event_payload(),
    )
    return row


@router.post(
    "/approvals/{approval_id}/consume",
    response_model=Approval,
    summary="Door consumes an unused allow_once",
)
async def consume_approval(
    request: Request, approval_id: uuid.UUID
) -> Approval:
    tenant_id = require_internal_actor(request)
    return consume_allow_once(_approvals(request), tenant_id, approval_id)


@router.post(
    "/policies",
    response_model=ApprovalPolicy,
    status_code=status.HTTP_201_CREATED,
    summary="Org allow/hold/deny. Default renter chat is unclassified.",
)
@atomic_events
async def create_policy(request: Request, body: ApprovalPolicyCreate) -> ApprovalPolicy:
    tenant_id = await asyncio.to_thread(require_auditor_actor, request)
    row = put_policy(_policies(request), tenant_id, body)
    await publish_owned_event(request,
        tenant_id=tenant_id,
        event_type=POLICY_CREATED_V1,
        payload=row.to_event_payload(),
    )
    return row


@router.get("/policies", response_model=list[ApprovalPolicy], summary="Org policy")
async def list_policies(
    request: Request,
    purpose: str | None = Query(default=None),
) -> list[ApprovalPolicy]:
    tenant_id = await asyncio.to_thread(require_hold_actor, request)
    return _policies(request).list_for(tenant_id, purpose=purpose)


@router.delete("/policies/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
@atomic_events
async def delete_policy(request: Request, policy_id: uuid.UUID) -> None:
    tenant_id = await asyncio.to_thread(require_auditor_actor, request)
    row = _policies(request).delete(tenant_id, policy_id)
    if row is None:
        raise HTTPException(status_code=404, detail=POLICY_NOT_FOUND)
    await publish_owned_event(request,
        tenant_id=tenant_id,
        event_type=POLICY_DELETED_V1,
        payload=row.to_event_payload(),
    )


@router.post(
    "/contracts",
    response_model=Contract,
    status_code=status.HTTP_201_CREATED,
    summary="Draft a contract with an opaque accounting binding.",
)
@atomic_events
async def create_contract(request: Request, body: ContractCreate) -> Contract:
    require_grant_write(role_from_request(request))
    tenant_id = await asyncio.to_thread(require_auditor_actor, request)
    row = put_contract(_contracts(request), tenant_id, body)
    await publish_contract(request, tenant_id=tenant_id,
                           event_type=CONTRACT_CREATED_V1, contract=row)
    return row


@router.get(
    "/export",
    response_model=GrantExport,
    summary="Operator grant export. No prompt bodies. Not the grant SoT.",
)
async def export_grants(request: Request) -> GrantExport:
    tenant_id = await asyncio.to_thread(require_auditor_actor, request)
    return build_grant_export(
        tenant_id=tenant_id,
        contracts=_contracts(request),
        violations=_violations(request),
        approvals=_approvals(request),
    )


@router.get("/contracts", response_model=list[Contract], summary="Tenant contracts")
async def list_contracts(request: Request) -> list[Contract]:
    tenant_id = await asyncio.to_thread(require_hold_actor, request)
    return _contracts(request).list_for(tenant_id)


@router.get("/contracts/{contract_id}", response_model=Contract)
async def get_contract(request: Request, contract_id: uuid.UUID) -> Contract:
    tenant_id = await asyncio.to_thread(require_hold_actor, request)
    row = _contracts(request).get(tenant_id, contract_id)
    if row is None:
        raise HTTPException(status_code=404, detail=CONTRACT_NOT_FOUND)
    return row


@router.get(
    "/contracts/{contract_id}/grant",
    response_model=ContractGrant,
    summary="Signed spine §D grant a sibling product may cite. Not a catalog row.",
)
async def get_contract_grant(request: Request, contract_id: uuid.UUID) -> ContractGrant:
    tenant_id = await asyncio.to_thread(require_hold_actor, request)
    return ContractGrant.from_contract(
        require_signed_grant(_contracts(request), tenant_id, contract_id),
        writer=writer_for(role_from_request(request)),
    )


@router.post(
    "/contracts/{contract_id}/sign",
    response_model=Contract,
    summary="Named human signs. LLM is never signed_by.",
)
@atomic_events
async def sign_contract_route(request: Request, contract_id: uuid.UUID) -> Contract:
    require_grant_write(role_from_request(request))
    signed_by = await require_contract_signer(request)
    tenant_id = await asyncio.to_thread(require_auditor_actor, request)
    row = sign_contract(
        _contracts(request), tenant_id, contract_id, signed_by=signed_by
    )
    await publish_contract(request, tenant_id=tenant_id,
                           event_type=CONTRACT_SIGNED_V1, contract=row)
    return row


@router.post(
    "/contracts/{contract_id}/bind",
    response_model=Contract,
    summary="Cite a Hypermesh lease. Does not mint a ticket.",
)
@atomic_events
async def bind_contract_route(
    request: Request, contract_id: uuid.UUID, body: ContractBind
) -> Contract:
    require_grant_write(role_from_request(request))
    tenant_id = await asyncio.to_thread(require_auditor_actor, request)
    row = bind_contract(_contracts(request), tenant_id, contract_id, body)
    await publish_contract(request, tenant_id=tenant_id,
                           event_type=CONTRACT_BOUND_V1, contract=row)
    return row


@router.post(
    "/contracts/fulfill",
    response_model=Contract,
    status_code=status.HTTP_201_CREATED,
    summary="Auto-sign a spine §D contract for Checkout. Internal only.",
)
@atomic_events
async def fulfill_contract_route(
    request: Request, body: ContractFulfill
) -> Contract:
    require_grant_write(role_from_request(request))
    tenant_id = require_internal_actor(request)
    from aiuditor_service.accounting import selected_backend
    if selected_backend() == 'http' and body.contract_id is None:
        raise HTTPException(409, 'External accounting requires a signed commit callback')
    row = fulfill_checkout_contract(_contracts(request), tenant_id, body)
    await publish_contract(request, tenant_id=tenant_id,
                           event_type=CONTRACT_FULFILLED_V1, contract=row)
    return row


@router.post("/contracts/accounting/committed", response_model=AccountingContract, status_code=201)
@router.post("/contracts/paid/fulfill", response_model=Contract, status_code=201, deprecated=True)
@atomic_events
async def fulfill_accounting_contract(request: Request) -> Contract:
    from aiuditor_service.accounting import authenticated_commit, apply_commit
    require_grant_write(role_from_request(request))
    event = await authenticated_commit(request)
    row = apply_commit(_contracts(request), event)
    await publish_contract(request, tenant_id=event.tenant_id,
                           event_type=CONTRACT_FULFILLED_V1, contract=row)
    return row


@router.post(
    "/catalog",
    response_model=CatalogService,
    status_code=status.HTTP_201_CREATED,
    summary="Name a leasable backend. Not a Hypermesh class or catalog row.",
)
@atomic_events
async def create_catalog_service(request: Request, body: ServiceCreate) -> CatalogService:
    tenant_id = await asyncio.to_thread(require_auditor_actor, request)
    row = put_service(_catalog(request), _store(request), tenant_id, body)
    await publish_owned_event(request,
        tenant_id=tenant_id,
        event_type=CATALOG_SERVICE_CREATED_V1,
        payload=row.to_event_payload(),
    )
    return row


@router.get("/catalog", response_model=list[CatalogService], summary="Tenant service catalog")
async def list_catalog_services(request: Request) -> list[CatalogService]:
    tenant_id = await asyncio.to_thread(require_hold_actor, request)
    return _catalog(request).list_for(tenant_id)


@router.get("/catalog/{service_id}", response_model=CatalogService)
async def get_catalog_service(request: Request, service_id: uuid.UUID) -> CatalogService:
    tenant_id = await asyncio.to_thread(require_hold_actor, request)
    row = _catalog(request).get(tenant_id, service_id)
    if row is None:
        raise HTTPException(status_code=404, detail=SERVICE_NOT_FOUND)
    return row


@router.post("/catalog/{service_id}/allocate/accounting", response_model=AccountingAllocation, status_code=201)
@router.post(
    "/catalog/{service_id}/allocate",
    response_model=ServiceAllocation,
    status_code=status.HTTP_201_CREATED,
    summary="Pick a live connection, sign a Contract, and offer the host ticket.",
)
@atomic_events
async def allocate_catalog_service(
    request: Request, service_id: uuid.UUID, body: ServiceAllocate
) -> ServiceAllocation:
    role = role_from_request(request)
    require_grant_write(role)
    require_market_mint(role)
    tenant_id = await asyncio.to_thread(require_auditor_actor, request)
    signed_by = await require_contract_signer(request)
    lookup = getattr(request.app.state, "host_lookup", None)
    plane = getattr(request.app.state, "plane_hop", None)
    if lookup is None or plane is None:
        raise HTTPException(status_code=503, detail=PLANE_HOP_FAILED)
    try:
        allocation = await allocate_service(
            _catalog(request),
            _store(request),
            _contracts(request),
            tenant_id,
            service_id,
            body,
            lookup=lookup,
            plane=plane,
            signed_by=signed_by,
            headers=_forward_headers(request),
        )
    except HostLookupUnavailable as exc:
        raise _plane_error(exc) from exc
    await publish_owned_event(request,
        tenant_id=tenant_id,
        event_type=CATALOG_SERVICE_ALLOCATED_V1,
        payload=allocation.to_event_payload(),
    )
    return allocation


@router.post("/envelopes/initial", response_model=ClassEnvelope, status_code=status.HTTP_201_CREATED)
@router.post(
    "/envelopes",
    response_model=ClassEnvelope,
    status_code=status.HTTP_201_CREATED,
    summary="Record a lab class envelope. Not a Hypermesh scorecard stamp.",
)
async def record_envelope(request: Request, body: "LabEnvelopeReport", response: Response) -> ClassEnvelope:
    require_grant_write(role_from_request(request))
    from aiuditor_service.lab_reporting import authenticate
    from sqlalchemy.exc import SQLAlchemyError
    now = await authenticate(request, body)
    try:
        row, fresh = _envelopes(request).persist.accept_lab_report(body, now)
    except SQLAlchemyError:
        raise HTTPException(503, "Lab evidence persistence unavailable") from None
    response.headers["ETag"] = f'"{row.revision}"'
    response.headers["X-Lab-Report-Replay"] = "false" if fresh else "true"
    # Receipt, envelope and owner outbox obligation commit together. Replays
    # reconcile historical missing obligations inside that same transaction.
    return row


@router.get(
    "/envelopes",
    response_model=list[ClassEnvelope],
    summary="Class envelopes for a tenant. Filter by class_id.",
)
async def list_envelopes(
    request: Request,
    class_id: str | None = Query(default=None),
) -> list[ClassEnvelope]:
    tenant_id = await asyncio.to_thread(require_program_reader, request)
    return _envelopes(request).list_for(tenant_id, class_id=class_id)


@router.get("/envelopes/{envelope_id}", response_model=ClassEnvelope)
async def get_envelope(request: Request, envelope_id: uuid.UUID) -> ClassEnvelope:
    tenant_id = await asyncio.to_thread(require_hold_actor, request)
    row = _envelopes(request).get(tenant_id, envelope_id)
    if row is None:
        raise HTTPException(status_code=404, detail=ENVELOPE_NOT_FOUND)
    return row


@router.post(
    "/suite",
    response_model=SuiteMember,
    status_code=status.HTTP_201_CREATED,
    summary="Add a catalog/pack id to the compact host cert suite.",
)
@atomic_events
async def record_suite_member(
    request: Request, body: SuiteMemberCreate
) -> SuiteMember:
    require_grant_write(role_from_request(request))
    await asyncio.to_thread(_require_operator_program, request, allow_service_seed=False)
    tenant_id = await asyncio.to_thread(require_auditor_actor, request)
    row = put_suite_member(_suite(request), tenant_id, body)
    await publish_owned_event(request,
        tenant_id=tenant_id,
        event_type=SUITE_MEMBER_RECORDED_V1,
        payload=row.to_event_payload(),
    )
    return row


@router.put("/suite/{member_id}", response_model=SuiteMember,
            summary="Replace a reviewed class-suite model with compare-and-set")
@atomic_events
async def replace_class_suite_member(request: Request, member_id: uuid.UUID,
                                     body: SuiteMemberReplace) -> SuiteMember:
    require_grant_write(role_from_request(request))
    _require_operator_program(request, allow_service_seed=False)
    tenant_id = require_auditor_actor(request)
    row = replace_suite_member(_suite(request), tenant_id, member_id, body)
    await publish_owned_event(request,
        tenant_id=tenant_id, event_type=SUITE_MEMBER_RECORDED_V1,
        payload=row.to_event_payload(),
    )
    return row


@router.post(
    "/suite/seed",
    response_model=list[SuiteMember],
    status_code=status.HTTP_201_CREATED,
    summary="Seed pack-v1 with the reviewed class-scoped 8B catalog entries.",
)
@atomic_events
async def seed_suite(request: Request) -> list[SuiteMember]:
    require_grant_write(role_from_request(request))
    await asyncio.to_thread(_require_operator_program, request, allow_service_seed=True)
    tenant_id = await asyncio.to_thread(require_auditor_actor, request)
    rows = seed_core_suite(_suite(request), tenant_id)
    for row in rows:
        await publish_owned_event(request,
            tenant_id=tenant_id,
            event_type=SUITE_MEMBER_RECORDED_V1,
            payload=row.to_event_payload(),
        )
    return rows


@router.get(
    "/suite",
    response_model=list[SuiteMember],
    summary="Core cert suite membership. Compact-cert jobs read this list.",
)
async def list_suite_members(
    request: Request,
    class_id: str | None = Query(default=None),
) -> list[SuiteMember]:
    tenant_id = await asyncio.to_thread(require_program_reader, request)
    return _suite(request).list_for(tenant_id, class_id=class_id)


@router.get("/suite/{member_id}", response_model=SuiteMember)
async def get_suite_member(request: Request, member_id: uuid.UUID) -> SuiteMember:
    tenant_id = await asyncio.to_thread(require_hold_actor, request)
    row = _suite(request).get(tenant_id, member_id)
    if row is None:
        raise HTTPException(status_code=404, detail=SUITE_MEMBER_NOT_FOUND)
    return row


@router.post(
    "/admissions",
    response_model=Admission,
    status_code=status.HTTP_201_CREATED,
    summary="Submit an uploaded model. Stays pending until a named verdict.",
)
async def create_admission(request: Request, body: AdmissionCreate) -> Admission:
    require_grant_write(role_from_request(request))
    tenant_id = await asyncio.to_thread(require_auditor_actor, request)
    plane = getattr(request.app.state, "plane_hop", None)
    async with operation(request.app.state.aiuditor_persist):
        row = submit_admission(_admissions(request), tenant_id, body, run_pipeline=False)
        await publish_owned_event(request, tenant_id=tenant_id,
            event_type=ADMISSION_SUBMITTED_V1, payload=row.to_event_payload())
    # The lab may call back while this hop is outstanding. Its admission and
    # submitted event must already be committed and visible to that callback.
    from aiuditor_service.admission import advance_admission_async
    return await advance_admission_async(_admissions(request), tenant_id, row.id,
        body=body, hold_for_human=bool(body.hold_for_human), plane=plane,
        approvals=_approvals(request))


@router.post("/admissions/{admission_id}/quarantine", response_model=Admission,
    summary="Retry an unassigned upload quarantine using its persisted artifact pins")
async def retry_admission_quarantine(request: Request, admission_id: uuid.UUID,
    body: AdmissionQuarantineRetry) -> Admission:
    require_grant_write(role_from_request(request))
    tenant_id = await asyncio.to_thread(require_auditor_actor, request)
    store = _admissions(request)
    row = store.get(tenant_id, admission_id)
    if row is None:
        raise HTTPException(404, ADMISSION_NOT_FOUND)
    if row.verdict is not None:
        raise HTTPException(409, "Admission already resolved")
    from aiuditor_service.admission import advance_admission_async
    return await advance_admission_async(store, tenant_id, admission_id,
        body=AdmissionCreate(file_hash=row.file_hash, format=row.format,
            quant=row.quant, license=row.license, intended_class=row.intended_class,
            size_bytes=row.size_bytes, source_ref=body.source_ref),
        plane=getattr(request.app.state, "plane_hop", None), approvals=_approvals(request))


@router.get(
    "/admissions",
    response_model=list[Admission],
    summary="Uploaded-model admissions. Filter by status or intended_class.",
)
async def list_admissions(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    intended_class: str | None = Query(default=None),
) -> list[Admission]:
    tenant_id = await asyncio.to_thread(require_hold_actor, request)
    return _admissions(request).list_for(
        tenant_id, status=status_filter, intended_class=intended_class
    )


@router.get("/admissions/{admission_id}", response_model=Admission)
async def get_admission(request: Request, admission_id: uuid.UUID) -> Admission:
    tenant_id = await asyncio.to_thread(require_hold_actor, request)
    row = _admissions(request).get(tenant_id, admission_id)
    if row is None:
        raise HTTPException(status_code=404, detail=ADMISSION_NOT_FOUND)
    return row


@router.post(
    "/admissions/{admission_id}/sandbox",
    response_model=Admission,
    summary="Record lab soak + egress observation for an upload admission",
    description="Dedicated signed job/device/artifact-bound lab evidence. Never certifies or promotes.",
)
async def report_admission_sandbox(request: Request, admission_id: uuid.UUID) -> Admission:
    require_grant_write(role_from_request(request))
    from aiuditor_service.admission_reporting import authenticated_report, persist_report
    body, now = await authenticated_report(request)
    if body.admission_id != admission_id:
        raise HTTPException(409, "Lab report admission mismatch")
    return await asyncio.to_thread(persist_report, _admissions(request).persist, body, now)


@router.post(
    "/admissions/{admission_id}/verdict",
    response_model=Admission,
    summary="Allow or deny an upload. LLM is never resolved_by.",
)
async def verdict_admission(
    request: Request, admission_id: uuid.UUID, body: AdmissionResolve
) -> Admission:
    require_grant_write(role_from_request(request))
    tenant_id = await asyncio.to_thread(require_auditor_actor, request)
    plane = getattr(request.app.state, "plane_hop", None)
    actor = await resolver_subject(request)
    async with operation(request.app.state.aiuditor_persist):
        row = await resolve_admission_async(_admissions(request), tenant_id,
            admission_id, body, resolved_by=actor, plane=None)
        await publish_owned_event(request, tenant_id=tenant_id,
            event_type=ADMISSION_RESOLVED_V1, payload=row.to_event_payload())
    if row.verdict.value == 'allow' and plane is not None:
        # Promotion is a retryable peer action after the recorded decision.
        return await resolve_admission_async(_admissions(request), tenant_id,
            admission_id, body, resolved_by=actor, plane=plane)
    return row


@router.post(
    "/crawl/run",
    response_model=CrawlRunResult,
    summary="Run one metadata discovery and fit filter",
    description=(
        "Control-plane intake. Fetches Hugging Face / allowlisted GGUF publisher "
        "metadata only (no weight download). Drops artifacts that cannot fit the "
        "class after OS reserve + KV headroom. Stores candidates for reviewed "
        "qualification; discovery does not schedule downloads or benchmarks."
    ),
)
async def run_crawl(request: Request) -> CrawlRunResult:
    require_grant_write(role_from_request(request))
    tenant_id = await asyncio.to_thread(require_auditor_actor, request)
    from aiuditor_service.crawl import StaticMetadataSource
    from aiuditor_service.intake import run_intake

    # HTTP trigger uses an injected source when tests set app.state.crawl_source.
    source = getattr(request.app.state, "crawl_source", None)
    if source is None:
        from aiuditor_service.crawl import HuggingFaceMetadataSource, default_seed_repos
        import os

        raw = (os.environ.get("AIUDITOR_CRAWL_REPOS") or "").strip()
        repos = (
            tuple(part.strip() for part in raw.split(",") if part.strip())
            if raw
            else default_seed_repos()
        )
        source = HuggingFaceMetadataSource(repos)
    plane = getattr(request.app.state, "plane_hop", None)
    from aiuditor_service.hypermesh import ClosedPlaneHop

    lab_hop = None if plane is None or isinstance(plane, ClosedPlaneHop) else plane
    report = await run_intake(
        tenant_id=tenant_id,
        source=source or StaticMetadataSource([]),
        candidates=_candidates(request),
        envelopes=_envelopes(request),
        lab_hop=lab_hop,
    )
    return CrawlRunResult(
        crawled=report.crawled,
        stored=report.stored,
        dropped_no_fit=report.dropped_no_fit,
        queued=report.queued,
        skipped_fresh=report.skipped_fresh,
        errors=list(report.errors),
    )


@router.get(
    "/crawl/candidates",
    response_model=list[CrawlCandidate],
    summary="List crawl metadata candidates. Filter by class_id or status.",
)
async def list_crawl_candidates(
    request: Request,
    class_id: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
) -> list[CrawlCandidate]:
    tenant_id = await asyncio.to_thread(require_hold_actor, request)
    return _candidates(request).list_for(
        tenant_id, class_id=class_id, status=status_filter
    )


@router.get(
    "/violations",
    response_model=list[ContractViolation],
    summary="Tenant violations. Dispute reads cert + audit, not a court.",
)
async def list_tenant_violations(
    request: Request,
    contract_id: uuid.UUID | None = Query(default=None),
) -> list[ContractViolation]:
    tenant_id = await asyncio.to_thread(require_hold_actor, request)
    if contract_id is not None and _contracts(request).get(tenant_id, contract_id) is None:
        raise HTTPException(status_code=404, detail=CONTRACT_NOT_FOUND)
    return _violations(request).list_for(tenant_id, contract_id=contract_id)


@router.get(
    "/contracts/{contract_id}/violations",
    response_model=list[ContractViolation],
    summary="Violations on a contract. Dispute reads cert + audit, not a court.",
)
async def list_violations(
    request: Request, contract_id: uuid.UUID
) -> list[ContractViolation]:
    tenant_id = await asyncio.to_thread(require_hold_actor, request)
    row = _contracts(request).get(tenant_id, contract_id)
    if row is None:
        raise HTTPException(status_code=404, detail=CONTRACT_NOT_FOUND)
    return _violations(request).list_for(tenant_id, contract_id=contract_id)


async def _apply_filed_violation(
    request: Request, tenant_id: uuid.UUID, filed: ContractViolation
) -> None:
    from aiuditor_service.enforcement import enforcement_lock
    from datetime import UTC, datetime
    store = _violations(request)
    with enforcement_lock(store.persist, tenant_id, filed.id):
        current = store.for_tenant(tenant_id).get(filed.id)
        if current is None:
            raise HTTPException(404, "Violation not found")
        if current.enforcement_completed_at is not None:
            return
        current = store.put(current.model_copy(update={"enforcement_attempts": current.enforcement_attempts + 1}))
        if current.lease_id is not None:
            plane = getattr(request.app.state, "plane_hop", None)
            if plane is None:
                raise HTTPException(status_code=503, detail=PLANE_HOP_FAILED)
            try:
                await plane.apply_violation(
                    tenant_id, current.lease_id, operation_id=current.id,
                    action=current.action.value, reason=current.reason,
                    headers=_forward_headers(request),
                )
            except HostLookupUnavailable as exc:
                raise _plane_error(exc) from exc
        store.put(current.model_copy(update={"enforcement_completed_at": datetime.now(UTC)}))


@router.post(
    "/contracts/{contract_id}/violations",
    response_model=ContractViolation,
    status_code=status.HTTP_201_CREATED,
    summary="File a term, cert-fail, policy-deny, or abuse violation.",
)
async def file_violation_route(
    request: Request, contract_id: uuid.UUID, body: ContractViolationCreate
) -> ContractViolation:
    require_grant_write(role_from_request(request))
    tenant_id = await asyncio.to_thread(require_auditor_actor, request)
    async with operation(request.app.state.aiuditor_persist):
        filed, _updated = put_violation(
            _violations(request), _contracts(request), tenant_id, contract_id, body
        )
        await publish_owned_event(request,
            tenant_id=tenant_id,
            event_type=CONTRACT_VIOLATED_V1,
            payload=filed.to_event_payload(),
        )
    await _apply_filed_violation(request, tenant_id, filed)
    return _violations(request).for_tenant(tenant_id)[filed.id]


@router.post(
    "/contracts/{contract_id}/evaluate",
    response_model=list[ContractViolation],
    summary="Evaluate term, cert-fail, deny budget, and settlement overdue.",
)
async def evaluate_contract_route(
    request: Request, contract_id: uuid.UUID
) -> list[ContractViolation]:
    require_grant_write(role_from_request(request))
    tenant_id = await asyncio.to_thread(require_hold_actor, request)
    row = _contracts(request).get(tenant_id, contract_id)
    if row is None:
        raise HTTPException(status_code=404, detail=CONTRACT_NOT_FOUND)
    existing = _violations(request).list_for(tenant_id, contract_id=contract_id)
    if row.status is ContractStatus.DRAFT:
        raise HTTPException(status_code=409, detail="only a signed contract can be evaluated")
    if row.status is ContractStatus.SUSPENDED:
        for pending in existing:
            await _apply_filed_violation(request, tenant_id, pending)
        return _violations(request).list_for(tenant_id, contract_id=contract_id)
    lease = None
    host = None
    if row.lease_id is not None:
        plane = getattr(request.app.state, "plane_hop", None)
        lookup = getattr(request.app.state, "host_lookup", None)
        if plane is None:
            raise HTTPException(status_code=503, detail=PLANE_HOP_FAILED)
        try:
            lease = await plane.get_lease(
                tenant_id, row.lease_id, headers=_forward_headers(request)
            )
            if lease is not None and lease.device_id is not None and lookup is not None:
                host = await lookup.get(
                    tenant_id, lease.device_id, headers=_forward_headers(request)
                )
        except HostLookupUnavailable as exc:
            raise _plane_error(exc) from exc
    denied = (
        deny_count(_approvals(request), tenant_id, row.lease_id)
        if row.lease_id is not None
        else 0
    )
    from aiuditor_service.accounting import settlement_overdue
    kind = evaluate_kind(row, host=host, lease=lease, denied=denied)
    if kind is None and lease is not None and lease.status in {"active", "starting"}:
        overdue = await settlement_overdue(row)
        kind = evaluate_kind(row, host=host, lease=lease, denied=denied, settlement_overdue=overdue)
    if kind is None:
        return existing
    async with operation(request.app.state.aiuditor_persist):
        filed, _updated = put_violation(
            _violations(request),
            _contracts(request),
            tenant_id,
            contract_id,
            ContractViolationCreate(kind=kind),
        )
        await publish_owned_event(request,
            tenant_id=tenant_id,
            event_type=CONTRACT_VIOLATED_V1,
            payload=filed.to_event_payload(),
        )
    await _apply_filed_violation(request, tenant_id, filed)
    return _violations(request).list_for(tenant_id, contract_id=contract_id)


from aiuditor_service.routing_contracts import ReviewEnvelope, ReviewVerdict
from aiuditor_service.routing_review import review as review_routing, require_reviewer


@router.post('/review', response_model=ReviewVerdict, response_model_exclude_none=True)
async def review_conductor(request: Request, body: ReviewEnvelope) -> ReviewVerdict:
    tenant = require_reviewer(request)
    return review_routing(_policies(request), tenant, body)
