"""In-memory tenant-scoped store. Persistence (RLS + Alembic) is follow-up."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from aiuditor_service.domain.models import (
    Approval,
    ApprovalPolicy,
    Connection,
    ConnectionKind,
    ConnectionPurpose,
    Contract,
)


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

    def lease_for(self, tenant_id: uuid.UUID, lease_id: uuid.UUID) -> Connection | None:
        for row in self.for_tenant(tenant_id).values():
            if row.lease_id == lease_id:
                return row
        return None

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


@dataclass
class ApprovalStore:
    approvals: dict[uuid.UUID, dict[uuid.UUID, Approval]] = field(default_factory=dict)

    def for_tenant(self, tenant_id: uuid.UUID) -> dict[uuid.UUID, Approval]:
        return self.approvals.setdefault(tenant_id, {})

    def list_for(
        self,
        tenant_id: uuid.UUID,
        *,
        status: str | None = None,
        lease_id: uuid.UUID | None = None,
        who: str | None = None,
        prompt_class: str | None = None,
    ) -> list[Approval]:
        rows = list(self.for_tenant(tenant_id).values())
        if status:
            rows = [row for row in rows if row.status.value == status]
        if lease_id is not None:
            rows = [row for row in rows if row.lease_id == lease_id]
        if who is not None:
            rows = [row for row in rows if row.who == who]
        if prompt_class is not None:
            rows = [row for row in rows if row.prompt_class == prompt_class]
        rows.sort(key=lambda row: row.created_at)
        return rows

    def get(self, tenant_id: uuid.UUID, approval_id: uuid.UUID) -> Approval | None:
        return self.for_tenant(tenant_id).get(approval_id)

    def put(self, row: Approval) -> Approval:
        self.for_tenant(row.tenant_id)[row.id] = row
        return row


@dataclass
class PolicyStore:
    policies: dict[uuid.UUID, dict[uuid.UUID, ApprovalPolicy]] = field(
        default_factory=dict
    )

    def for_tenant(self, tenant_id: uuid.UUID) -> dict[uuid.UUID, ApprovalPolicy]:
        return self.policies.setdefault(tenant_id, {})

    def list_for(
        self, tenant_id: uuid.UUID, *, purpose: str | None = None
    ) -> list[ApprovalPolicy]:
        rows = list(self.for_tenant(tenant_id).values())
        if purpose:
            rows = [row for row in rows if row.purpose == purpose]
        rows.sort(key=lambda row: row.created_at)
        return rows

    def get(self, tenant_id: uuid.UUID, policy_id: uuid.UUID) -> ApprovalPolicy | None:
        return self.for_tenant(tenant_id).get(policy_id)

    def put(self, row: ApprovalPolicy) -> ApprovalPolicy:
        self.for_tenant(row.tenant_id)[row.id] = row
        return row

    def delete(self, tenant_id: uuid.UUID, policy_id: uuid.UUID) -> ApprovalPolicy | None:
        return self.for_tenant(tenant_id).pop(policy_id, None)

    def matching(
        self,
        tenant_id: uuid.UUID,
        *,
        purpose: str,
        prompt_class: str | None,
        connection_id: uuid.UUID | None,
    ) -> ApprovalPolicy | None:
        for row in self.list_for(tenant_id, purpose=purpose):
            same_class = row.prompt_class == prompt_class
            same_conn = row.connection_id == connection_id
            if same_class and same_conn:
                return row
        return None


@dataclass
class ContractStore:
    contracts: dict[uuid.UUID, dict[uuid.UUID, Contract]] = field(default_factory=dict)

    def for_tenant(self, tenant_id: uuid.UUID) -> dict[uuid.UUID, Contract]:
        return self.contracts.setdefault(tenant_id, {})

    def list_for(self, tenant_id: uuid.UUID) -> list[Contract]:
        rows = list(self.for_tenant(tenant_id).values())
        rows.sort(key=lambda row: row.created_at)
        return rows

    def get(self, tenant_id: uuid.UUID, contract_id: uuid.UUID) -> Contract | None:
        return self.for_tenant(tenant_id).get(contract_id)

    def put(self, row: Contract) -> Contract:
        self.for_tenant(row.tenant_id)[row.id] = row
        return row

    def lease_for(self, tenant_id: uuid.UUID, lease_id: uuid.UUID) -> Contract | None:
        for row in self.for_tenant(tenant_id).values():
            if row.lease_id == lease_id:
                return row
        return None
