"""Org allow/hold/deny. Default renter chat is unclassified.

Most specific match wins: connection + class, then connection, then
class, then purpose. A miss is not a deny — the door applies the
purpose default (renter/service allow, IR uses the taxonomy).
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException

from aiuditor_service.domain.models import (
    ApprovalPolicy,
    ApprovalPolicyCreate,
    PolicyDisposition,
)
from aiuditor_service.store import PolicyStore

POLICY_NOT_FOUND = "Policy not found"
POLICY_EXISTS = "policy already exists for this purpose, class, and connection"
POLICY_PURPOSE = "purpose must be renter, ir, or service"
POLICY_CLASS = "prompt_class must be a 1-64 character identifier using letters, digits, _, . or -"
_CLASS_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", re.ASCII)
POLICY_PURPOSES = frozenset({"renter", "ir", "service"})


def _score(
    row: ApprovalPolicy,
    *,
    purpose: str,
    prompt_class: str | None,
    connection_id: uuid.UUID | None,
) -> int:
    if row.purpose != purpose:
        return 0
    if row.connection_id is not None and row.connection_id != connection_id:
        return 0
    if row.prompt_class is not None and row.prompt_class != prompt_class:
        return 0
    score = 1
    if row.prompt_class is not None:
        score += 2
    if row.connection_id is not None:
        score += 4
    return score


def evaluate_policy(
    store: PolicyStore,
    tenant_id: uuid.UUID,
    *,
    purpose: str,
    prompt_class: str | None = None,
    connection_id: uuid.UUID | None = None,
) -> PolicyDisposition | None:
    """Return the winning disposition, or None when no rule matches."""

    best: ApprovalPolicy | None = None
    best_score = 0
    for row in store.list_for(tenant_id, purpose=purpose):
        score = _score(
            row,
            purpose=purpose,
            prompt_class=prompt_class,
            connection_id=connection_id,
        )
        if score > best_score:
            best = row
            best_score = score
    return best.disposition if best is not None else None


def put_policy(
    store: PolicyStore,
    tenant_id: uuid.UUID,
    body: ApprovalPolicyCreate,
    *,
    now: datetime | None = None,
) -> ApprovalPolicy:
    purpose = (body.purpose or "").strip().lower()
    if purpose not in POLICY_PURPOSES:
        raise HTTPException(status_code=400, detail=POLICY_PURPOSE)
    # Only an omitted/null class intentionally creates a wildcard policy.
    # Content redaction must never broaden a supplied authorization selector.
    prompt_class = body.prompt_class
    if prompt_class is not None and _CLASS_IDENTIFIER.fullmatch(prompt_class) is None:
        raise HTTPException(status_code=400, detail=POLICY_CLASS)
    existing = store.matching(
        tenant_id,
        purpose=purpose,
        prompt_class=prompt_class,
        connection_id=body.connection_id,
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail=POLICY_EXISTS)
    clock = now if now is not None else datetime.now(UTC)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=UTC)
    row = ApprovalPolicy(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        purpose=purpose,
        prompt_class=prompt_class,
        connection_id=body.connection_id,
        disposition=body.disposition,
        created_at=clock,
    )
    store.put(row)
    return row
