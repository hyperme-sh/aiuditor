"""AIuditor approval inbox. Not fyber.auditor. Not privilege_grant.

Hold an AI-use ask until a named human resolves. LLM is never resolved_by.
Prompt bodies are not stored. Timeout is a deny.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request, status

from controlplane_identity.internal_token import request_has_internal_token

from aiuditor_service.domain.models import (
    Approval,
    ApprovalCreate,
    ApprovalDecision,
    ApprovalStatus,
)
from aiuditor_service.store import ApprovalStore
from aiuditor_service.tenant import (
    AUDITOR_ACTOR_REQUIRED,
    HEADER_REQUIRED,
    INVALID_HEADER,
    bearer_secret,
    require_auditor_actor,
)

APPROVAL_NOT_FOUND = "Approval not found"
APPROVAL_NOT_HELD = "only a held approval can be resolved"
APPROVAL_NOT_ALLOW_ONCE = "only an unused allow_once can be consumed"
APPROVAL_ALREADY_CONSUMED = "allow_once already consumed"
LLM_NEVER_RESOLVES = "LLM is never resolved_by"
NAMED_HUMAN_REQUIRED = "named human JWT required to resolve"
INTERNAL_REQUIRED = "internal token required to consume"

_SAFE_MAX = 120
_FORBIDDEN = ("prompt", "message", "messages", "completion", "content")
_LLM_SUBJECTS = frozenset(
    {"llm", "model", "assistant", "gpt", "claude", "ai", "bot"}
)
_TTL_DEFAULT = 900
_TTL_MAX = 24 * 60 * 60


def _safe_text(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    if any(token in text.lower() for token in _FORBIDDEN):
        return None
    return text[:_SAFE_MAX]


def _internal_token() -> str:
    return (os.environ.get("AIUDITOR_INTERNAL_TOKEN") or "").strip()


def require_hold_actor(request: Request) -> uuid.UUID:
    """Door/internal may file a hold. A renter key cannot."""

    if request_has_internal_token(request, _internal_token()):
        header = request.headers.get("X-Tenant-ID")
        if not header:
            raise HTTPException(status_code=400, detail=HEADER_REQUIRED)
        try:
            parsed = uuid.UUID(str(header))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=INVALID_HEADER) from exc
        request.state.tenant_id = parsed
        return parsed
    return require_auditor_actor(request)


def require_internal_actor(request: Request) -> uuid.UUID:
    """Door consume only. A named human resolves; they do not consume."""

    if request_has_internal_token(request, _internal_token()):
        return require_hold_actor(request)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=INTERNAL_REQUIRED,
    )


def resolver_subject(request: Request) -> str:
    """Human or IR/service exchange sub. Never internal. Never an LLM name."""

    if request_has_internal_token(request, _internal_token()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=NAMED_HUMAN_REQUIRED,
        )
    require_auditor_actor(request)
    secret = bearer_secret(request)
    if secret is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=AUDITOR_ACTOR_REQUIRED,
        )
    claims = _actor_claims(secret)
    sub = str((claims or {}).get("sub") or "").strip()
    if not sub or sub.lower() in _LLM_SUBJECTS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=LLM_NEVER_RESOLVES,
        )
    return sub[:256]


def _actor_claims(secret: str) -> dict[str, object] | None:
    try:
        from controlplane_identity.security.tenant_session_jwt import (
            try_decode_tenant_session_jwt,
        )
    except Exception:
        try_decode_tenant_session_jwt = None
    if try_decode_tenant_session_jwt is not None:
        session = try_decode_tenant_session_jwt(secret)
        if session is not None:
            return session
    try:
        from controlplane_identity.security.api_key_exchange_jwt import (
            try_decode_api_key_session_jwt,
        )
    except Exception:
        return None
    return try_decode_api_key_session_jwt(secret)


def expire_due(
    store: ApprovalStore,
    tenant_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> list[Approval]:
    clock = now if now is not None else datetime.now(UTC)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=UTC)
    expired: list[Approval] = []
    for row in list(store.for_tenant(tenant_id).values()):
        exp = row.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        if row.status is not ApprovalStatus.HELD or exp > clock:
            continue
        timed = row.model_copy(
            update={
                "status": ApprovalStatus.TIMED_OUT,
                "decision": ApprovalDecision.DENY,
                "resolved_by": None,
                "resolved_at": clock,
            }
        )
        store.put(timed)
        expired.append(timed)
    return expired


def _is_stale(row: Approval, clock: datetime) -> bool:
    if row.status not in {ApprovalStatus.DENIED, ApprovalStatus.TIMED_OUT}:
        return False
    exp = row.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=UTC)
    return exp <= clock


def latest_for(
    store: ApprovalStore,
    tenant_id: uuid.UUID,
    *,
    lease_id: uuid.UUID,
    who: str,
    prompt_class: str | None = None,
    now: datetime | None = None,
) -> Approval | None:
    """Newest row the door can honor for this lease + who.

    Exact class wins. An unused allow_session on the same lease covers any
    class. Denied/timed_out rows past expires_at are stale so the door can
    file a new hold.
    """

    clock = now if now is not None else datetime.now(UTC)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=UTC)
    expire_due(store, tenant_id, now=clock)
    safe_who = _safe_text(who)
    if safe_who is None:
        return None
    safe_class = _safe_text(prompt_class)
    rows = [
        row
        for row in store.list_for(tenant_id, lease_id=lease_id, who=safe_who)
        if not _is_stale(row, clock)
    ]
    if not rows:
        return None
    exact = [row for row in rows if row.prompt_class == safe_class]
    sessions = [
        row
        for row in rows
        if row.status is ApprovalStatus.ALLOWED
        and row.decision is ApprovalDecision.ALLOW_SESSION
    ]
    if exact:
        newest = exact[-1]
        if (
            newest.status is ApprovalStatus.ALLOWED
            and newest.decision is ApprovalDecision.ALLOW_ONCE
            and newest.consumed_at is not None
            and sessions
        ):
            return sessions[-1]
        return newest
    if sessions:
        return sessions[-1]
    return None


def hold_approval(
    store: ApprovalStore,
    tenant_id: uuid.UUID,
    body: ApprovalCreate,
    *,
    now: datetime | None = None,
) -> Approval:
    clock = now if now is not None else datetime.now(UTC)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=UTC)
    who = _safe_text(body.who)
    if who is None:
        raise HTTPException(status_code=400, detail="who is required")
    prompt_class = _safe_text(body.prompt_class)
    expire_due(store, tenant_id, now=clock)
    if body.lease_id is not None:
        existing = latest_for(
            store,
            tenant_id,
            lease_id=body.lease_id,
            who=who,
            prompt_class=prompt_class,
            now=clock,
        )
        if existing is not None and existing.status is ApprovalStatus.HELD:
            return existing
    ttl = max(30, min(int(body.ttl_seconds), _TTL_MAX))
    row = Approval(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        lease_id=body.lease_id,
        connection_id=body.connection_id,
        purpose=(body.purpose or "renter").strip().lower() or "renter",
        who=who,
        why=body.why,
        prompt_class=prompt_class,
        status=ApprovalStatus.HELD,
        created_at=clock,
        expires_at=clock + timedelta(seconds=ttl or _TTL_DEFAULT),
    )
    store.put(row)
    return row


def resolve_approval(
    store: ApprovalStore,
    tenant_id: uuid.UUID,
    approval_id: uuid.UUID,
    *,
    decision: ApprovalDecision,
    resolved_by: str,
    now: datetime | None = None,
) -> Approval:
    expire_due(store, tenant_id, now=now)
    row = store.get(tenant_id, approval_id)
    if row is None:
        raise HTTPException(status_code=404, detail=APPROVAL_NOT_FOUND)
    if row.status is not ApprovalStatus.HELD:
        raise HTTPException(status_code=409, detail=APPROVAL_NOT_HELD)
    clock = now if now is not None else datetime.now(UTC)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=UTC)
    status_after = (
        ApprovalStatus.DENIED
        if decision is ApprovalDecision.DENY
        else ApprovalStatus.ALLOWED
    )
    updated = row.model_copy(
        update={
            "status": status_after,
            "decision": decision,
            "resolved_by": resolved_by,
            "resolved_at": clock,
        }
    )
    store.put(updated)
    return updated


def consume_allow_once(
    store: ApprovalStore,
    tenant_id: uuid.UUID,
    approval_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> Approval:
    expire_due(store, tenant_id, now=now)
    row = store.get(tenant_id, approval_id)
    if row is None:
        raise HTTPException(status_code=404, detail=APPROVAL_NOT_FOUND)
    if row.consumed_at is not None:
        raise HTTPException(status_code=409, detail=APPROVAL_ALREADY_CONSUMED)
    if (
        row.status is not ApprovalStatus.ALLOWED
        or row.decision is not ApprovalDecision.ALLOW_ONCE
    ):
        raise HTTPException(status_code=409, detail=APPROVAL_NOT_ALLOW_ONCE)
    clock = now if now is not None else datetime.now(UTC)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=UTC)
    updated = row.model_copy(update={"consumed_at": clock})
    store.put(updated)
    return updated
