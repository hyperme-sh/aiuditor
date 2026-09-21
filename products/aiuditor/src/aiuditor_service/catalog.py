"""AIuditor service catalog. Distinct from Hypermesh classes and catalog.

A row names a backend: Hypermesh P1 class, Hypermesh P2 catalog id, or a
provider connection. TURN, oracles, and payments stay sibling products.
Allocate (contract + ticket) is the next Phase 6 cut.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException

from aiuditor_service.approvals import _safe_text
from aiuditor_service.contracts import SPINE_METERS
from aiuditor_service.domain.models import (
    CatalogService,
    ConnectionKind,
    ConnectionPurpose,
    ConnectionStatus,
    ServiceBackend,
    ServiceCreate,
)
from aiuditor_service.store import CatalogStore, ConnectionStore

SERVICE_NOT_FOUND = "Service not found"
SERVICE_TITLE = "title is required"
SERVICE_METER = "meters must be a non-empty subset of the spine E vocabulary"
SERVICE_SIBLING = "TURN, oracles, and payments stay sibling products"
PROVIDER_CONNECTION_REQUIRED = "provider services require a purpose=lease provider connection"
PROVIDER_NOT_LEASE = "only a purpose=lease provider connection can be cataloged"
CONNECTION_NOT_FOUND = "Connection not found"
CONNECTION_DISABLED = "connection is disabled"

_SIBLING = frozenset({"turn", "oracle", "oracles", "payment", "payments"})


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
    if tokens & _SIBLING:
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
