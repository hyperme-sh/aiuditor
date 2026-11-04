"""Tenant-scoped grant export. Derived copy, not the SoT.

Contracts, violations, and approvals only. Prompt bodies, HMAC tickets,
and raw credentials stay off the file. IR receipts stay on the door.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from aiuditor_service.approvals import expire_due
from aiuditor_service.domain.models import GrantExport
from aiuditor_service.store import ApprovalStore, ContractStore, ViolationStore

EXPORT_BODIES_REFUSED = "export must not include prompt bodies or tickets"
_BANNED = frozenset(
    {"prompt", "ticket", "credential", "messages", "hmac", "completion", "content"}
)


def _now(clock: datetime | None) -> datetime:
    stamp = clock if clock is not None else datetime.now(UTC)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp


def _reject_bodies(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in _BANNED:
                raise HTTPException(status_code=500, detail=EXPORT_BODIES_REFUSED)
            _reject_bodies(item)
        return
    if isinstance(value, list):
        for item in value:
            _reject_bodies(item)


def build_grant_export(
    *,
    tenant_id: uuid.UUID,
    contracts: ContractStore,
    violations: ViolationStore,
    approvals: ApprovalStore,
    now: datetime | None = None,
) -> GrantExport:
    expire_due(approvals, tenant_id, now=_now(now))
    payload = GrantExport(
        tenant_id=tenant_id,
        exported_at=_now(now),
        contracts=[row.to_event_payload() for row in contracts.list_for(tenant_id)],
        violations=[row.to_event_payload() for row in violations.list_for(tenant_id)],
        approvals=[row.to_event_payload() for row in approvals.list_for(tenant_id)],
    )
    _reject_bodies(payload.model_dump())
    return payload
