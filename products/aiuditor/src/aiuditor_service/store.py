"""Tenant-scoped grant stores with write-through persist.

Tables + RLS live in controlplane-db 0025 (aiuditor_*). Tests keep
MemoryPersistSession. Production hydrates and puts through SqlPersistSession.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from aiuditor_service.domain.models import (
    Approval,
    ApprovalPolicy,
    CatalogService,
    Connection,
    ConnectionKind,
    ConnectionPurpose,
    Contract,
    ContractViolation,
)
from aiuditor_service.persist import (
    MemoryPersistSession,
    PersistSession,
    dump_for_persist,
    row_from_payload,
)


def _reload(
    bucket: dict,
    persist: PersistSession,
    table: str,
    tenant_id: uuid.UUID,
    *,
    hydrating: bool,
) -> None:
    if hydrating:
        return
    loaded = {}
    for payload in persist.load_rows(table, tenant_id).values():
        row = row_from_payload(table, payload)
        loaded[row.id] = row
    bucket.clear()
    bucket.update(loaded)


def _write(
    persist: PersistSession,
    table: str,
    row,
    *,
    hydrating: bool,
) -> None:
    if hydrating:
        return
    persist.upsert(table, row.tenant_id, row.id, dump_for_persist(row))


@dataclass
class ConnectionStore:
    connections: dict[uuid.UUID, dict[uuid.UUID, Connection]] = field(default_factory=dict)
    persist: PersistSession = field(default_factory=MemoryPersistSession)
    _hydrating: bool = False

    def for_tenant(self, tenant_id: uuid.UUID) -> dict[uuid.UUID, Connection]:
        bucket = self.connections.setdefault(tenant_id, {})
        _reload(bucket, self.persist, "connections", tenant_id, hydrating=self._hydrating)
        return bucket

    def list_for(self, tenant_id: uuid.UUID) -> list[Connection]:
        return list(self.for_tenant(tenant_id).values())

    def get(self, tenant_id: uuid.UUID, connection_id: uuid.UUID) -> Connection | None:
        return self.for_tenant(tenant_id).get(connection_id)

    def put(self, row: Connection) -> Connection:
        _write(self.persist, "connections", row, hydrating=self._hydrating)
        self.connections.setdefault(row.tenant_id, {})[row.id] = row
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
    persist: PersistSession = field(default_factory=MemoryPersistSession)
    _hydrating: bool = False

    def for_tenant(self, tenant_id: uuid.UUID) -> dict[uuid.UUID, Approval]:
        bucket = self.approvals.setdefault(tenant_id, {})
        _reload(bucket, self.persist, "approvals", tenant_id, hydrating=self._hydrating)
        return bucket

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
        _write(self.persist, "approvals", row, hydrating=self._hydrating)
        self.approvals.setdefault(row.tenant_id, {})[row.id] = row
        return row


@dataclass
class PolicyStore:
    policies: dict[uuid.UUID, dict[uuid.UUID, ApprovalPolicy]] = field(
        default_factory=dict
    )
    persist: PersistSession = field(default_factory=MemoryPersistSession)
    _hydrating: bool = False

    def for_tenant(self, tenant_id: uuid.UUID) -> dict[uuid.UUID, ApprovalPolicy]:
        bucket = self.policies.setdefault(tenant_id, {})
        _reload(bucket, self.persist, "policies", tenant_id, hydrating=self._hydrating)
        return bucket

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
        _write(self.persist, "policies", row, hydrating=self._hydrating)
        self.policies.setdefault(row.tenant_id, {})[row.id] = row
        return row

    def delete(self, tenant_id: uuid.UUID, policy_id: uuid.UUID) -> ApprovalPolicy | None:
        row = self.for_tenant(tenant_id).pop(policy_id, None)
        if row is not None and not self._hydrating:
            self.persist.delete("policies", tenant_id, policy_id)
        return row

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
    persist: PersistSession = field(default_factory=MemoryPersistSession)
    _hydrating: bool = False

    def for_tenant(self, tenant_id: uuid.UUID) -> dict[uuid.UUID, Contract]:
        bucket = self.contracts.setdefault(tenant_id, {})
        _reload(bucket, self.persist, "contracts", tenant_id, hydrating=self._hydrating)
        return bucket

    def list_for(self, tenant_id: uuid.UUID) -> list[Contract]:
        rows = list(self.for_tenant(tenant_id).values())
        rows.sort(key=lambda row: row.created_at)
        return rows

    def get(self, tenant_id: uuid.UUID, contract_id: uuid.UUID) -> Contract | None:
        return self.for_tenant(tenant_id).get(contract_id)

    def put(self, row: Contract) -> Contract:
        _write(self.persist, "contracts", row, hydrating=self._hydrating)
        self.contracts.setdefault(row.tenant_id, {})[row.id] = row
        return row

    def lease_for(self, tenant_id: uuid.UUID, lease_id: uuid.UUID) -> Contract | None:
        for row in self.for_tenant(tenant_id).values():
            if row.lease_id == lease_id:
                return row
        return None


@dataclass
class ViolationStore:
    violations: dict[uuid.UUID, dict[uuid.UUID, ContractViolation]] = field(
        default_factory=dict
    )
    persist: PersistSession = field(default_factory=MemoryPersistSession)
    _hydrating: bool = False

    def for_tenant(self, tenant_id: uuid.UUID) -> dict[uuid.UUID, ContractViolation]:
        bucket = self.violations.setdefault(tenant_id, {})
        _reload(bucket, self.persist, "violations", tenant_id, hydrating=self._hydrating)
        return bucket

    def list_for(
        self, tenant_id: uuid.UUID, *, contract_id: uuid.UUID | None = None
    ) -> list[ContractViolation]:
        rows = list(self.for_tenant(tenant_id).values())
        if contract_id is not None:
            rows = [row for row in rows if row.contract_id == contract_id]
        rows.sort(key=lambda row: row.created_at)
        return rows

    def put(self, row: ContractViolation) -> ContractViolation:
        _write(self.persist, "violations", row, hydrating=self._hydrating)
        self.violations.setdefault(row.tenant_id, {})[row.id] = row
        return row


@dataclass
class CatalogStore:
    services: dict[uuid.UUID, dict[uuid.UUID, CatalogService]] = field(
        default_factory=dict
    )
    persist: PersistSession = field(default_factory=MemoryPersistSession)
    _hydrating: bool = False

    def for_tenant(self, tenant_id: uuid.UUID) -> dict[uuid.UUID, CatalogService]:
        bucket = self.services.setdefault(tenant_id, {})
        _reload(bucket, self.persist, "catalog", tenant_id, hydrating=self._hydrating)
        return bucket

    def list_for(self, tenant_id: uuid.UUID) -> list[CatalogService]:
        rows = list(self.for_tenant(tenant_id).values())
        rows.sort(key=lambda row: row.created_at)
        return rows

    def get(self, tenant_id: uuid.UUID, service_id: uuid.UUID) -> CatalogService | None:
        return self.for_tenant(tenant_id).get(service_id)

    def put(self, row: CatalogService) -> CatalogService:
        _write(self.persist, "catalog", row, hydrating=self._hydrating)
        self.services.setdefault(row.tenant_id, {})[row.id] = row
        return row


@dataclass
class StoreBundle:
    persist: PersistSession
    connection_store: ConnectionStore
    approval_store: ApprovalStore
    policy_store: PolicyStore
    contract_store: ContractStore
    violation_store: ViolationStore
    catalog_store: CatalogStore


def new_stores(persist: PersistSession | None = None) -> StoreBundle:
    session = persist or MemoryPersistSession()
    bundle = StoreBundle(
        persist=session,
        connection_store=ConnectionStore(persist=session),
        approval_store=ApprovalStore(persist=session),
        policy_store=PolicyStore(persist=session),
        contract_store=ContractStore(persist=session),
        violation_store=ViolationStore(persist=session),
        catalog_store=CatalogStore(persist=session),
    )
    session.hydrate(bundle)
    return bundle
