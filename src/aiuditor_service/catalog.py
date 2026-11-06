"""AIuditor service catalog. Distinct from Hypermesh classes and catalog.

A row names a backend: Hypermesh P1 class, Hypermesh P2 catalog id, or a
provider connection. TURN, oracles, and payments stay sibling products
until they can cite the same signed Contract — they are not catalog
backends and are not wrapped here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException

from aiuditor_service.approvals import _safe_text
from aiuditor_service.contracts import (
    LEASE_ALREADY_BOUND,
    SPINE_METERS,
    SPINE_RAIL,
    bind_contract,
    put_contract,
    sign_contract,
)
from aiuditor_service.domain.models import (
    CatalogService,
    Connection,
    ConnectionKind,
    ConnectionPurpose,
    ConnectionStatus,
    ContractBind,
    ContractCreate,
    ContractParty,
    ServiceAllocate,
    ServiceAllocation,
    ServiceBackend,
    ServiceCreate,
)
from aiuditor_service.hypermesh import HOST_ROLE, HostLookup, HostView, PlaneHop
from aiuditor_service.store import CatalogStore, ConnectionStore, ContractStore

SERVICE_NOT_FOUND = "Service not found"
SERVICE_TITLE = "title is required"
SERVICE_METER = "meters must be a non-empty subset of the spine E vocabulary"
SERVICE_SIBLING = "TURN, oracles, and payments stay sibling products"
PROVIDER_CONNECTION_REQUIRED = "provider services require a purpose=lease provider connection"
PROVIDER_NOT_LEASE = "only a purpose=lease provider connection can be cataloged"
CONNECTION_NOT_FOUND = "Connection not found"
CONNECTION_DISABLED = "connection is disabled"
NO_LIVE_CONNECTION = "no live connection matches this service"
RENTER_REQUIRED = "renter is required"
PROVIDER_NOT_READY = "provider connection is not ready"
LEASE_IS_AUDITOR = "auditor tickets are minted; they are not bound as renter leases"

SIBLING_TOKENS = frozenset({"turn", "oracle", "oracles", "payment", "payments"})


def _now(clock: datetime | None) -> datetime:
    stamp = clock if clock is not None else datetime.now(UTC)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp


def _meters(raw: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in raw:
        name = (item or "").strip().lower()
        if name not in SPINE_METERS:
            raise HTTPException(status_code=400, detail=SERVICE_METER)
        if name in seen:
            continue
        seen.add(name)
        cleaned.append(name)
    if not cleaned:
        raise HTTPException(status_code=400, detail=SERVICE_METER)
    return cleaned


def _refuse_sibling(title: str, class_id: str | None, catalog_id: str | None) -> None:
    tokens = {title.strip().lower()}
    if class_id:
        tokens.add(class_id.strip().lower())
    if catalog_id:
        tokens.add(catalog_id.strip().lower())
    if tokens & SIBLING_TOKENS:
        raise HTTPException(status_code=400, detail=SERVICE_SIBLING)


def put_service(
    store: CatalogStore,
    connections: ConnectionStore,
    tenant_id: uuid.UUID,
    body: ServiceCreate,
    *,
    now: datetime | None = None,
) -> CatalogService:
    title = _safe_text(body.title)
    if title is None:
        raise HTTPException(status_code=400, detail=SERVICE_TITLE)
    _refuse_sibling(title, body.class_id, body.catalog_id)
    connection_id = body.connection_id
    if body.backend is ServiceBackend.PROVIDER:
        if connection_id is None:
            raise HTTPException(status_code=400, detail=PROVIDER_CONNECTION_REQUIRED)
        row = connections.get(tenant_id, connection_id)
        if row is None:
            raise HTTPException(status_code=404, detail=CONNECTION_NOT_FOUND)
        if row.kind is not ConnectionKind.PROVIDER:
            raise HTTPException(status_code=409, detail=PROVIDER_CONNECTION_REQUIRED)
        if row.purpose is not ConnectionPurpose.LEASE:
            raise HTTPException(status_code=409, detail=PROVIDER_NOT_LEASE)
        if row.status is ConnectionStatus.DISABLED:
            raise HTTPException(status_code=409, detail=CONNECTION_DISABLED)
    service = CatalogService(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        title=title,
        backend=body.backend,
        class_id=body.class_id,
        catalog_id=body.catalog_id,
        connection_id=connection_id,
        meters=_meters(body.meters),
        created_at=_now(now),
    )
    store.put(service)
    return service


_NOT_LIVE_SELL = frozenset({"paused", "off"})
_NOT_LIVE_CERT = frozenset({"suspended"})
_RENTER_PURPOSE = frozenset({"renter", "service"})


def _host_is_live(view: HostView) -> bool:
    if not view.enrolled or (view.role or "").strip() != HOST_ROLE:
        return False
    if view.schedule_hold:
        return False
    if (view.sell_state or "").strip().lower() in _NOT_LIVE_SELL:
        return False
    if (view.path_b_state or "").strip().lower() in _NOT_LIVE_CERT:
        return False
    return True


def _matches_service(service: CatalogService, view: HostView) -> bool:
    if service.backend is ServiceBackend.HYPERMESH_CLASS:
        return (view.class_id or "") == (service.class_id or "")
    if service.backend is ServiceBackend.HYPERMESH_CATALOG:
        return (view.loaded_catalog_id or "") == (service.catalog_id or "")
    return False


async def pick_live_connection(
    connections: ConnectionStore,
    service: CatalogService,
    *,
    lookup: HostLookup,
    contracts: ContractStore,
    plane: PlaneHop,
    headers: dict[str, str] | None = None,
) -> Connection:
    if service.backend is ServiceBackend.PROVIDER:
        if service.connection_id is None:
            raise HTTPException(status_code=409, detail=NO_LIVE_CONNECTION)
        row = connections.get(service.tenant_id, service.connection_id)
        if row is None:
            raise HTTPException(status_code=404, detail=CONNECTION_NOT_FOUND)
        if row.kind is not ConnectionKind.PROVIDER or row.purpose is not ConnectionPurpose.LEASE:
            raise HTTPException(status_code=409, detail=PROVIDER_CONNECTION_REQUIRED)
        if row.status is ConnectionStatus.DISABLED:
            raise HTTPException(status_code=409, detail=CONNECTION_DISABLED)
        if row.status is not ConnectionStatus.READY:
            raise HTTPException(status_code=409, detail=PROVIDER_NOT_READY)
        return row

    candidates: list[Connection] = []
    for row in connections.list_for(service.tenant_id):
        if row.kind is not ConnectionKind.HOST or row.purpose is not ConnectionPurpose.LEASE:
            continue
        if row.status is ConnectionStatus.DISABLED or row.device_id is None:
            continue
        host = await lookup.get(service.tenant_id, row.device_id, headers=headers)
        if host is None or not _host_is_live(host) or not _matches_service(service, host):
            continue
        if row.lease_id is not None:
            # Reusing a connection must not mint new authority for a lease
            # already owned by a contract, including another plane replica.
            if contracts.lease_for(service.tenant_id, row.lease_id) is not None:
                continue
            lease = await plane.get_lease(service.tenant_id, row.lease_id, headers=headers)
            if lease is None or lease.contract_id is not None:
                continue
        candidates.append(row)
    if not candidates:
        raise HTTPException(status_code=409, detail=NO_LIVE_CONNECTION)
    candidates.sort(
        key=lambda row: (
            row.lease_id is not None,
            row.status is not ConnectionStatus.READY,
            row.created_at,
        )
    )
    return candidates[0]


def _host_subject(row: Connection) -> str:
    if row.device_id is not None:
        return str(row.device_id)
    if row.endpoint:
        return row.endpoint
    return str(row.id)


async def allocate_service(
    catalog: CatalogStore,
    connections: ConnectionStore,
    contracts: ContractStore,
    tenant_id: uuid.UUID,
    service_id: uuid.UUID,
    body: ServiceAllocate,
    *,
    lookup: HostLookup,
    plane: PlaneHop,
    signed_by: str,
    headers: dict[str, str] | None = None,
    now: datetime | None = None,
) -> ServiceAllocation:
    service = catalog.get(tenant_id, service_id)
    if service is None:
        raise HTTPException(status_code=404, detail=SERVICE_NOT_FOUND)
    _refuse_sibling(service.title, service.class_id, service.catalog_id)
    renter = _safe_text(body.renter)
    if service.backend is not ServiceBackend.PROVIDER:
        renter = str(await plane.checkout_renter(tenant_id, subject=signed_by, headers=headers))
    if renter is None:
        raise HTTPException(status_code=400, detail=RENTER_REQUIRED)
    row = await pick_live_connection(
        connections, service, lookup=lookup, contracts=contracts, plane=plane, headers=headers
    )
    # Validate existing lease authority before persisting a new signed contract.
    existing_lease = None
    if service.backend is not ServiceBackend.PROVIDER and row.lease_id is not None:
        existing_lease = await plane.get_lease(tenant_id, row.lease_id, headers=headers)
        if existing_lease is None:
            raise HTTPException(status_code=404, detail="Lease not found")
        if existing_lease.contract_id is not None or contracts.lease_for(tenant_id, row.lease_id) is not None:
            raise HTTPException(status_code=409, detail=LEASE_ALREADY_BOUND)
        if existing_lease.purpose not in _RENTER_PURPOSE:
            raise HTTPException(status_code=409, detail=LEASE_IS_AUDITOR)
    clock = _now(now)
    hours = max(1, min(int(body.reserved_hours), 24 * 30))
    draft = put_contract(
        contracts,
        tenant_id,
        ContractCreate(
            parties=[
                ContractParty(role="renter", subject=renter),
                ContractParty(role="host", subject=_host_subject(row)),
            ],
            meters=list(service.meters),
            rail=SPINE_RAIL,
            until=clock + timedelta(hours=hours),
            connection_id=row.id,
        ),
        now=clock,
    )
    signed = sign_contract(contracts, tenant_id, draft.id, signed_by=signed_by, now=clock)
    lease_id = row.lease_id
    purpose = "lease"
    offered = None
    if service.backend is ServiceBackend.PROVIDER:
        if row.status is not ConnectionStatus.READY:
            row = row.model_copy(update={"status": ConnectionStatus.READY})
            connections.put(row)
        return ServiceAllocation(
            service_id=service.id,
            contract=signed,
            connection_id=row.id,
            backend=service.backend,
            purpose=purpose,
            exp=signed.until,
        )

    if row.device_id is None:
        raise HTTPException(status_code=409, detail=NO_LIVE_CONNECTION)
    if lease_id is not None:
        purpose = existing_lease.purpose
    else:
        kind = (
            "p1_hardware"
            if service.backend is ServiceBackend.HYPERMESH_CLASS
            else "p2_loaded_model"
        )
        offered = await plane.offer_lease(
            tenant_id,
            kind=kind,
            device_id=row.device_id,
            reserved_hours=hours,
            contract_id=signed.id,
            contract_meters=list(signed.meters),
            class_id=service.class_id,
            catalog_id=service.catalog_id,
            headers=headers,
        )
        lease_id = offered.lease_id
        purpose = offered.purpose
    current = contracts.get(tenant_id, signed.id) or signed
    if current.lease_id is None:
        current = bind_contract(
            contracts, tenant_id, signed.id, ContractBind(lease_id=lease_id)
        )
    elif current.lease_id != lease_id:
        raise HTTPException(status_code=409, detail=LEASE_ALREADY_BOUND)
    assigned = row.model_copy(
        update={"lease_id": lease_id, "status": ConnectionStatus.READY}
    )
    connections.put(assigned)
    return ServiceAllocation(
        service_id=service.id,
        contract=current,
        connection_id=assigned.id,
        backend=service.backend,
        lease_id=lease_id,
        purpose=purpose,
        exp=current.until,
        billing_record_id=offered.billing_record_id if offered else None,
        payment_amount_atoms=offered.payment_amount_atoms if offered else None,
        payment_test_credit_available=(
            offered.payment_test_credit_available if offered else False
        ),
    )
