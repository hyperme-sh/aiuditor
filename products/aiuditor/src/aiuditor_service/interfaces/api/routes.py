"""AIuditor HTTP API under /api/v1/aiuditor.

Domain: connections. Classes, devices, and certs stay on Hypermesh.
Site inbox stays /api/v1/auditor. The door stays products/hypermesh-gateway.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status

from api.infrastructure.messaging.tenant_event_publisher import get_tenant_event_publisher

from aiuditor_service.credentials import credential_ref
from aiuditor_service.domain.models import Connection, ConnectionCreate, ConnectionStatus
from aiuditor_service.events import CONNECTION_CREATED_V1
from aiuditor_service.store import ConnectionStore
from aiuditor_service.tenant import require_tenant_id

router = APIRouter(prefix="/aiuditor", tags=["aiuditor"])


def _store(request: Request) -> ConnectionStore:
    store = getattr(request.app.state, "connection_store", None)
    if store is None:
        store = ConnectionStore()
        request.app.state.connection_store = store
    return store


@router.get("/connections", response_model=list[Connection])
async def list_connections(request: Request) -> list[Connection]:
    tenant_id = require_tenant_id(request)
    return _store(request).list_for(tenant_id)


@router.post("/connections", response_model=Connection, status_code=status.HTTP_201_CREATED)
async def create_connection(request: Request, body: ConnectionCreate) -> Connection:
    tenant_id = require_tenant_id(request)
    hashed = credential_ref(body.credential) if body.credential else None
    row = Connection(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        kind=body.kind,
        purpose=body.purpose,
        device_id=body.device_id,
        endpoint=body.endpoint,
        credential_ref=hashed,
        status=ConnectionStatus.READY if hashed else ConnectionStatus.PENDING,
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
        raise HTTPException(status_code=404, detail="Connection not found")
    return row
