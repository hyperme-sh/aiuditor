"""Live workspace grants shared by Portal and the connector broker."""

import time
import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from aiuditor_service.approval_authority import require_authority
from aiuditor_service.machine_identity import transaction
from aiuditor_service.models import (
    AiuditorWorkspaceConnection as Connection,
    AiuditorWorkspaceDecision as Receipt,
    AiuditorWorkspaceGrant as Grant,
)
from aiuditor_service.private_models.routes import human
from aiuditor_service.role import require_grant_write, role_from_request
from aiuditor_service.workspace_connection_contracts import (
    MAX_GRANT_SECONDS,
    MAX_PAST_SECONDS,
    ConnectionCreate,
    ConnectionView,
    Decision,
    GrantCreate,
    GrantView,
    decide,
    verified_authorization,
)

router = APIRouter(prefix="/aiuditor/workspace-connections", tags=["workspace-connections"])
PageSize = Annotated[int, Query(ge=1, le=100)]


async def workspace_actor(request, response, workspace_id, account_action):
    response.headers["Cache-Control"] = "no-store"
    tenant, subject = human(request)
    # Workspace IDs use the User owner's organization identifier.
    await require_authority(request, tenant, subject, account_action, workspace_id)
    return tenant, subject


def connection_view(row) -> ConnectionView:
    return ConnectionView(
        workspace_id=row.workspace_id,
        connection_id=row.connection_id,
        provider=row.provider,
        target=row.target,
        status="enabled" if row.disabled_at is None else "disabled",
    )


def grant_view(connection, row, now) -> GrantView:
    if row.revoked_at is not None:
        status = "revoked"
    elif connection.disabled_at is not None:
        status = "connection_disabled"
    elif row.expires_at <= now:
        status = "expired"
    else:
        status = "active"
    return GrantView(
        workspace_id=row.workspace_id,
        connection_id=row.connection_id,
        grant_id=row.grant_id,
        actor_kind=row.actor_kind,
        actor_id=row.actor_id,
        operations=row.operations,
        expires_at=row.expires_at,
        status=status,
    )


async def find_connection(session, tenant, workspace_id, connection_id, *, lock=False):
    query = select(Connection).where(
        Connection.tenant_id == tenant,
        Connection.workspace_id == workspace_id,
        Connection.connection_id == connection_id,
    )
    return await session.scalar(query.with_for_update() if lock else query)


async def connection_or_404(session, tenant, workspace_id, connection_id, *, lock=False):
    row = await find_connection(session, tenant, workspace_id, connection_id, lock=lock)
    if row is None:
        raise HTTPException(404, "Workspace connector not found")
    return row


def scoped_grants(tenant, workspace_id, connection_id):
    return select(Grant).where(
        Grant.tenant_id == tenant,
        Grant.workspace_id == workspace_id,
        Grant.connection_id == connection_id,
    )


@router.get("", response_model=list[ConnectionView])
async def list_connections(
    request: Request, response: Response, workspace_id: uuid.UUID,
    after: uuid.UUID | None = None, limit: PageSize = 50,
):
    tenant, _ = await workspace_actor(request, response, workspace_id, "organization.read")
    query = select(Connection).where(Connection.tenant_id == tenant, Connection.workspace_id == workspace_id)
    if after is not None:
        query = query.where(Connection.connection_id > after)
    async with transaction(tenant) as session:
        rows = await session.scalars(query.order_by(Connection.connection_id).limit(limit))
        return [connection_view(row) for row in rows]


@router.post("", response_model=ConnectionView)
async def register_connection(request: Request, response: Response, body: ConnectionCreate):
    tenant, _ = await workspace_actor(request, response, body.workspace_id, "organization.admin")
    async with transaction(tenant) as session:
        await session.execute(
            insert(Connection).values(tenant_id=tenant, **body.model_dump()).on_conflict_do_nothing()
        )
        row = await connection_or_404(session, tenant, body.workspace_id, body.connection_id)
        if (row.provider, row.target) != (body.provider, body.target):
            raise HTTPException(409, "Connector ID already binds a different target")
        return connection_view(row)


@router.post("/{connection_id}/disable", response_model=ConnectionView)
async def disable_connection(
    request: Request, response: Response, workspace_id: uuid.UUID, connection_id: uuid.UUID,
):
    tenant, _ = await workspace_actor(request, response, workspace_id, "organization.admin")
    async with transaction(tenant) as session:
        row = await connection_or_404(session, tenant, workspace_id, connection_id, lock=True)
        if row.disabled_at is None:
            row.disabled_at = int(time.time())
        return connection_view(row)


@router.get("/{connection_id}/grants", response_model=list[GrantView])
async def list_grants(
    request: Request, response: Response, workspace_id: uuid.UUID, connection_id: uuid.UUID,
    after: uuid.UUID | None = None, limit: PageSize = 50,
):
    tenant, _ = await workspace_actor(request, response, workspace_id, "organization.admin")
    query = scoped_grants(tenant, workspace_id, connection_id)
    if after is not None:
        query = query.where(Grant.grant_id > after)
    async with transaction(tenant) as session:
        row = await connection_or_404(session, tenant, workspace_id, connection_id)
        now = int(time.time())
        grants = await session.scalars(query.order_by(Grant.grant_id).limit(limit))
        return [grant_view(row, grant, now) for grant in grants]


@router.post("/{connection_id}/grants", response_model=GrantView)
async def put_grant(
    request: Request, response: Response, connection_id: uuid.UUID, body: GrantCreate,
):
    tenant, subject = await workspace_actor(request, response, body.workspace_id, "organization.admin")
    async with transaction(tenant) as session:
        connection = await connection_or_404(session, tenant, body.workspace_id, connection_id, lock=True)
        now = int(time.time())
        query = scoped_grants(tenant, body.workspace_id, connection_id).where(Grant.grant_id == body.grant_id)
        row = await session.scalar(query)
        operations = sorted(body.operations)
        if row is not None:
            if (row.actor_kind, row.actor_id, row.operations, row.expires_at) != (
                body.actor_kind, body.actor_id, operations, body.expires_at,
            ):
                raise HTTPException(409, "Grant ID already binds a different scope")
            return grant_view(connection, row, now)
        if connection.disabled_at is not None:
            raise HTTPException(409, "Workspace connector is disabled")
        if not now < body.expires_at <= now + MAX_GRANT_SECONDS:
            raise HTTPException(422, "Grant expiry must be within the next 30 days")
        row = Grant(
            tenant_id=tenant, connection_id=connection_id, created_by=subject,
            **body.model_dump(exclude={"operations"}), operations=operations,
        )
        session.add(row)
        return grant_view(connection, row, now)


@router.post("/{connection_id}/grants/{grant_id}/revoke", response_model=GrantView)
async def revoke_grant(
    request: Request, response: Response, workspace_id: uuid.UUID,
    connection_id: uuid.UUID, grant_id: uuid.UUID,
):
    tenant, subject = await workspace_actor(request, response, workspace_id, "organization.admin")
    async with transaction(tenant) as session:
        connection = await connection_or_404(session, tenant, workspace_id, connection_id, lock=True)
        query = scoped_grants(tenant, workspace_id, connection_id)
        row = await session.scalar(query.where(Grant.grant_id == grant_id))
        if row is None:
            raise HTTPException(404, "Workspace connector grant not found")
        now = int(time.time())
        if row.revoked_at is None:
            row.revoked_at = now
            row.revoked_by = subject
        return grant_view(connection, row, now)


@router.post("/authorize", response_model=Decision)
async def authorize_operation(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    require_grant_write(role_from_request(request))
    call = await verified_authorization(request)
    async with transaction(call.tenant_id) as session:
        connection = await find_connection(
            session, call.tenant_id, call.workspace_id, call.connection_id, lock=True,
        )
        grant = None
        if connection is not None:
            query = scoped_grants(call.tenant_id, call.workspace_id, call.connection_id)
            grant = await session.scalar(query.where(Grant.grant_id == call.grant_id))
        now = int(time.time())
        if call.issued_at < now - MAX_PAST_SECONDS:
            raise HTTPException(401, "Workspace connector attestation expired")
        decision = decide(connection, grant, call, now)
        saved = await session.scalar(
            insert(Receipt).values(
                **call.model_dump(include={
                    "tenant_id", "request_id", "workspace_id", "connection_id", "grant_id",
                    "run_id", "actor_kind", "provider", "operation",
                }),
                reason=decision.reason,
                decided_at=now,
            ).on_conflict_do_nothing().returning(Receipt.request_id)
        )
        if saved is None:
            return Decision(request_id=call.request_id, allowed=False, reason="replayed")
        return decision
