"""Violation machine for a signed Contract. Not an on-chain court.

Kinds: term, cert-fail, policy-deny, abuse. Actions: suspend, stop, refund.
Dispute reads cert + audit logs. Handoff stays out of Phase 5.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException

from aiuditor_service.approvals import _safe_text
from aiuditor_service.contracts import CONTRACT_NOT_FOUND
from aiuditor_service.domain.models import (
    ApprovalStatus,
    Contract,
    ContractStatus,
    ContractViolation,
    ContractViolationCreate,
    ViolationAction,
    ViolationKind,
)
from aiuditor_service.hypermesh import HostLookup, HostView, LeaseView, PlaneHop
from aiuditor_service.store import ApprovalStore, ContractStore, ViolationStore

CONTRACT_NOT_SIGNED = "only a signed contract can take a violation"
VIOLATION_ACTION = "action must be suspend, stop, or refund"
DENY_BUDGET = 3

_DEFAULT_ACTION = {
    ViolationKind.TERM: ViolationAction.REFUND,
    ViolationKind.CERT_FAIL: ViolationAction.REFUND,
    ViolationKind.POLICY_DENY: ViolationAction.SUSPEND,
    ViolationKind.ABUSE: ViolationAction.STOP,
}

_UNPAID_SELL = frozenset({"paused", "off"})


def _now(clock: datetime | None) -> datetime:
    stamp = clock if clock is not None else datetime.now(UTC)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp


def default_action(kind: ViolationKind) -> ViolationAction:
    return _DEFAULT_ACTION[kind]


def deny_count(approvals: ApprovalStore, tenant_id: uuid.UUID, lease_id: uuid.UUID) -> int:
    return len(
        approvals.list_for(tenant_id, lease_id=lease_id, status=ApprovalStatus.DENIED.value)
    )


def host_unpaid(host: HostView | None) -> bool:
    if host is None:
        return False
    if host.schedule_hold:
        return True
    sell = (host.sell_state or "").strip().lower()
    return sell in _UNPAID_SELL


def cert_failed(host: HostView | None) -> bool:
    if host is None:
        return False
    state = (host.path_b_state or "").strip().lower()
    return state in {"suspended", "never-run"}


def evaluate_kind(
    contract: Contract,
    *,
    now: datetime | None = None,
    host: HostView | None = None,
    lease: LeaseView | None = None,
    denied: int = 0,
) -> ViolationKind | None:
    clock = _now(now)
    until = contract.until
    if until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    if until <= clock:
        return ViolationKind.TERM
    if cert_failed(host):
        return ViolationKind.CERT_FAIL
    if denied >= DENY_BUDGET:
        return ViolationKind.POLICY_DENY
    if host_unpaid(host) and lease is not None and lease.status in {"active", "starting"}:
        return ViolationKind.ABUSE
    return None


def put_violation(
    store: ViolationStore,
    contracts: ContractStore,
    tenant_id: uuid.UUID,
    contract_id: uuid.UUID,
    body: ContractViolationCreate,
    *,
    now: datetime | None = None,
) -> tuple[ContractViolation, Contract]:
    with store.persist.checkout_contract_scope(tenant_id, contract_id, contract_id):
        return _put_violation(store, contracts, tenant_id, contract_id, body, now=now)


def _put_violation(store, contracts, tenant_id, contract_id, body, *, now=None):
    clock = _now(now)
    row = contracts.get(tenant_id, contract_id)
    if row is None:
        raise HTTPException(status_code=404, detail=CONTRACT_NOT_FOUND)
    if row.status is ContractStatus.DRAFT:
        raise HTTPException(status_code=409, detail=CONTRACT_NOT_SIGNED)
    action = body.action or default_action(body.kind)
    if action not in ViolationAction:
        raise HTTPException(status_code=400, detail=VIOLATION_ACTION)
    reason = _safe_text(body.reason)
    # One contract decision with the same kind/action/reason has one stable ID.
    import json
    operation = uuid.uuid5(contract_id, json.dumps([str(tenant_id), body.kind.value, action.value, reason]))
    previous = store.for_tenant(tenant_id).get(operation)
    if previous is not None:
        updated = row.model_copy(update={"status": ContractStatus.SUSPENDED})
        contracts.put(updated)
        return previous, updated
    filed = ContractViolation(
        id=operation,
        tenant_id=tenant_id,
        contract_id=contract_id,
        lease_id=row.lease_id,
        kind=body.kind,
        action=action,
        reason=reason,
        created_at=clock,
    )
    store.put(filed)
    updated = row.model_copy(update={"status": ContractStatus.SUSPENDED})
    contracts.put(updated)
    return filed, updated
