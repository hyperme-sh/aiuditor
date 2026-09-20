"""In-memory tenant-scoped store. Persistence (RLS + Alembic) is follow-up."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from aiuditor_service.domain.models import Connection, ConnectionKind, ConnectionPurpose


@dataclass
class ConnectionStore:
    connections: dict[uuid.UUID, dict[uuid.UUID, Connection]] = field(default_factory=dict)

    def for_tenant(self, tenant_id: uuid.UUID) -> dict[uuid.UUID, Connection]:
        return self.connections.setdefault(tenant_id, {})

    def list_for(self, tenant_id: uuid.UUID) -> list[Connection]:
        return list(self.for_tenant(tenant_id).values())

    def get(self, tenant_id: uuid.UUID, connection_id: uuid.UUID) -> Connection | None:
        return self.for_tenant(tenant_id).get(connection_id)

    def put(self, row: Connection) -> Connection:
        self.for_tenant(row.tenant_id)[row.id] = row
        return row

    def host_for(
        self,
        tenant_id: uuid.UUID,
        device_id: uuid.UUID,
        purpose: ConnectionPurpose,
    ) -> Connection | None:
        for row in self.for_tenant(tenant_id).values():
            if (
                row.kind is ConnectionKind.HOST
                and row.device_id == device_id
                and row.purpose is purpose
            ):
                return row
        return None
