"""AIuditor Contract. Spine §D only. Not a handoff package.

parties, meters, rail, until, dispute tip. 1.x rail is Stripe.
Prompt bodies are not stored. Tables + RLS are Alembic 0025. Puts write
through PersistSession (SQL when DATABASE_URL is real).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException

from aiuditor_service.approvals import _safe_text, resolver_subject
from aiuditor_service.domain.models import (
    Contract,
    ContractBind,
    ContractCreate,
    ContractFulfill,
    ContractParty,
    ContractStatus,
)
from aiuditor_service.store import ContractStore

CONTRACT_NOT_FOUND = "Contract not found"
CONTRACT_NOT_DRAFT = "only a draft contract can be signed"
CONTRACT_NOT_SIGNED = "only a signed contract can bind a lease"
CONTRACT_ALREADY_BOUND = "contract already cites a lease"
LEASE_ALREADY_BOUND = "lease already cites a contract"
CONTRACT_UNTIL_PAST = "until must be in the future"
CONTRACT_PARTIES = "at least two parties with a role and subject are required"
CONTRACT_METER = "meters must be a non-empty subset of the spine E vocabulary"
CONTRACT_RAIL = "1.x rail is stripe"
CONTRACT_PARTY_ROLE = "party role must be renter, host, auditor, or billing"
CHECKOUT_SIGNER = "checkout"

SPINE_METERS = frozenset(
    {
        "wall_time",
        "token_digest_count",
        "prompt_digest_count",
        "capacity_block",
    }
)
SPINE_RAIL = "stripe"
PARTY_ROLES = frozenset({"renter", "host", "auditor", "billing"})


def _now(clock: datetime | None) -> datetime:
    stamp = clock if clock is not None else datetime.now(UTC)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp


def _parties(raw: list[ContractParty]) -> list[ContractParty]:
    cleaned: list[ContractParty] = []
    for party in raw:
        role = (party.role or "").strip().lower()
        if role not in PARTY_ROLES:
            raise HTTPException(status_code=400, detail=CONTRACT_PARTY_ROLE)
        subject = _safe_text(party.subject)
        if subject is None:
            raise HTTPException(status_code=400, detail=CONTRACT_PARTIES)
        cleaned.append(ContractParty(role=role, subject=subject))
    if len(cleaned) < 2:
        raise HTTPException(status_code=400, detail=CONTRACT_PARTIES)
    return cleaned


def _meters(raw: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in raw:
        name = (item or "").strip().lower()
        if name not in SPINE_METERS:
            raise HTTPException(status_code=400, detail=CONTRACT_METER)
        if name in seen:
            continue
        seen.add(name)
        cleaned.append(name)
    if not cleaned:
        raise HTTPException(status_code=400, detail=CONTRACT_METER)
    return cleaned


def put_contract(store, tenant_id, body, *, now=None) -> Contract:
    if body.lease_id is not None:
        raise HTTPException(400, "Drafts cannot claim lease IDs; use signed owner binding")
    return _put_contract(store, tenant_id, body, now=now)


def _put_contract(
    store: ContractStore,
    tenant_id: uuid.UUID,
    body: ContractCreate,
    *,
    now: datetime | None = None,
) -> Contract:
    clock = _now(now)
    until = body.until
    if until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    if until <= clock:
        raise HTTPException(status_code=400, detail=CONTRACT_UNTIL_PAST)
    rail = (body.rail or SPINE_RAIL).strip().lower()
    if rail != SPINE_RAIL:
        raise HTTPException(status_code=400, detail=CONTRACT_RAIL)
    row = Contract(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        parties=_parties(body.parties),
        meters=_meters(body.meters),
        rail=rail,
        until=until,
        dispute_tip=_safe_text(body.dispute_tip),
        connection_id=body.connection_id,
        lease_id=body.lease_id,
        status=ContractStatus.DRAFT,
        created_at=clock,
    )
    store.put(row)
    return row


def sign_contract(
    store: ContractStore,
    tenant_id: uuid.UUID,
    contract_id: uuid.UUID,
    *,
    signed_by: str,
    now: datetime | None = None,
) -> Contract:
    clock = _now(now)
    row = store.get(tenant_id, contract_id)
    if row is None:
        raise HTTPException(status_code=404, detail=CONTRACT_NOT_FOUND)
    if row.status is not ContractStatus.DRAFT:
        raise HTTPException(status_code=409, detail=CONTRACT_NOT_DRAFT)
    if row.until <= clock:
        raise HTTPException(status_code=400, detail=CONTRACT_UNTIL_PAST)
    updated = row.model_copy(
        update={
            "status": ContractStatus.SIGNED,
            "signed_by": signed_by,
            "signed_at": clock,
        }
    )
    store.put(updated)
    return updated


def bind_contract(
    store: ContractStore, tenant_id: uuid.UUID, contract_id: uuid.UUID, body: ContractBind
) -> Contract:
    with store.persist.checkout_contract_scope(tenant_id, body.lease_id, contract_id):
        return _bind_contract(store, tenant_id, contract_id, body)


def _bind_contract(
    store: ContractStore,
    tenant_id: uuid.UUID,
    contract_id: uuid.UUID,
    body: ContractBind,
) -> Contract:
    row = store.get(tenant_id, contract_id)
    if row is None:
        raise HTTPException(status_code=404, detail=CONTRACT_NOT_FOUND)
    if row.status is not ContractStatus.SIGNED:
        raise HTTPException(status_code=409, detail=CONTRACT_NOT_SIGNED)
    if row.lease_id is not None:
        raise HTTPException(status_code=409, detail=CONTRACT_ALREADY_BOUND)
    existing = store.lease_for(tenant_id, body.lease_id)
    if existing is not None:
        raise HTTPException(status_code=409, detail=LEASE_ALREADY_BOUND)
    updated = row.model_copy(update={"lease_id": body.lease_id})
    store.put(updated)
    return updated


def _checkout_matches(row: Contract, body: ContractFulfill, clock: datetime) -> None:
    wanted = {("renter", body.renter_subject), ("host", body.host_subject)}
    actual = {(party.role, party.subject) for party in row.parties if party.role in {"renter", "host"}}
    if wanted != actual or set(row.meters) != set(_meters(body.meters)) or row.rail != SPINE_RAIL:
        raise HTTPException(409, "Checkout contract does not match lease authority")
    if row.status is not ContractStatus.SIGNED:
        raise HTTPException(409, CONTRACT_NOT_SIGNED)
    if row.until <= clock:
        raise HTTPException(409, CONTRACT_UNTIL_PAST)
    # Allow bounded checkout/clock skew; ticket expiry still uses the exact
    # signed deadline, so this tolerance grants no extra access time.
    if row.until < clock + timedelta(hours=body.reserved_hours) - timedelta(minutes=5):
        raise HTTPException(409, "Signed authority does not cover the reserved lease window")


def fulfill_checkout_contract(
    store: ContractStore, tenant_id: uuid.UUID, body: ContractFulfill,
    *, now: datetime | None = None,
) -> Contract:
    # The owner commits before the HTTP route acknowledges. A lost reply can
    # replay by tenant/lease without duplicate grants or cross-tenant SQL.
    with store.persist.checkout_contract_scope(tenant_id, body.lease_id, body.contract_id):
        return _fulfill_checkout_contract(store, tenant_id, body, now=now)


def _fulfill_checkout_contract(
    store: ContractStore,
    tenant_id: uuid.UUID,
    body: ContractFulfill,
    *,
    now: datetime | None = None,
) -> Contract:
    """Mint or bind a signed Contract for lease checkout / first motion.

    Internal only. A named human still uses draft + sign. This path is
    the auto-sign the HMAC ticket waits on.
    """

    clock = _now(now)
    existing = store.lease_for(tenant_id, body.lease_id)
    if existing is not None:
        if body.contract_id is not None and body.contract_id != existing.id:
            raise HTTPException(409, LEASE_ALREADY_BOUND)
        _checkout_matches(existing, body, clock)
        if existing.signed_by == CHECKOUT_SIGNER and existing.until - existing.created_at != timedelta(hours=body.reserved_hours):
            raise HTTPException(409, "Checkout replay changes reserved duration")
        if existing.signed_by == CHECKOUT_SIGNER and (existing.connection_id != body.connection_id or existing.dispute_tip != _safe_text(body.dispute_tip)):
            raise HTTPException(409, "Checkout replay changes contract terms")
        return existing
    if body.contract_id is not None:
        named = store.get(tenant_id, body.contract_id)
        if named is None:
            raise HTTPException(404, CONTRACT_NOT_FOUND)
        _checkout_matches(named, body, clock)
        return _bind_contract(
            store, tenant_id, body.contract_id, ContractBind(lease_id=body.lease_id)
        )
    hours = max(1, min(int(body.reserved_hours), 24 * 30))
    until = clock + timedelta(hours=hours)
    row = _put_contract(
        store,
        tenant_id,
        ContractCreate(
            parties=[
                ContractParty(role="renter", subject=body.renter_subject),
                ContractParty(role="host", subject=body.host_subject),
            ],
            meters=body.meters,
            rail=SPINE_RAIL,
            until=until,
            dispute_tip=body.dispute_tip,
            connection_id=body.connection_id,
            lease_id=body.lease_id,
        ),
        now=clock,
    )
    return sign_contract(
        store, tenant_id, row.id, signed_by=CHECKOUT_SIGNER, now=clock
    )


def require_signed_grant(
    store: ContractStore,
    tenant_id: uuid.UUID,
    contract_id: uuid.UUID,
) -> Contract:
    """Signed spine §D grant a sibling product may cite. Not a catalog row."""

    row = store.get(tenant_id, contract_id)
    if row is None:
        raise HTTPException(status_code=404, detail=CONTRACT_NOT_FOUND)
    if row.status is not ContractStatus.SIGNED:
        raise HTTPException(status_code=409, detail=CONTRACT_NOT_SIGNED)
    return row


async def require_contract_signer(request) -> str:
    """Named human. Never internal. Never an LLM name."""

    return await resolver_subject(request)
