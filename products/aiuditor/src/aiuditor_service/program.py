"""Model cert program: class envelopes, core suite, admission verdicts.

Hypermesh stays the measurement and lease store. AIuditor owns which
artifacts were soaked per class, which pack ids form the compact host
cert, and whether an uploaded model is admitted.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException

from aiuditor_service.admission import (
    ADMISSION_ALREADY_RESOLVED,
    ADMISSION_NOT_FOUND,
    advance_admission,
    advance_admission_async,
    resolve_admission,
    resolve_admission_async,
    submit_admission,
    submit_admission_async,
)
from aiuditor_service.approvals import _safe_text
from aiuditor_service.domain.models import (
    ClassEnvelope,
    ClassEnvelopeCreate,
    SuiteMember,
    SuiteMemberCreate,
)
from aiuditor_service.store import EnvelopeStore, SuiteStore

ENVELOPE_NOT_FOUND = "Envelope not found"
SUITE_MEMBER_NOT_FOUND = "Suite member not found"
CLASS_ID_REQUIRED = "class_id is required"
CATALOG_ID_REQUIRED = "catalog_id is required"
FILE_HASH_REQUIRED = "file_hash is required"
RUNTIME_REQUIRED = "runtime is required"
FORMAT_REQUIRED = "format is required"
LICENSE_REQUIRED = "license is required"
PACK_ID = "pack-v1"
CATALOG_LLAMA_8B = "llama-3.1-8b-q4"

# Compact host cert seed: pack-v1 / llama-3.1-8b-q4 on public Orin classes.
CORE_SUITE_SEED: tuple[tuple[str, str, str], ...] = (
    ("agx-large", CATALOG_LLAMA_8B, PACK_ID),
    ("nx-volume", CATALOG_LLAMA_8B, PACK_ID),
)

__all__ = [
    "ADMISSION_ALREADY_RESOLVED",
    "ADMISSION_NOT_FOUND",
    "CATALOG_ID_REQUIRED",
    "CATALOG_LLAMA_8B",
    "CLASS_ID_REQUIRED",
    "CORE_SUITE_SEED",
    "ENVELOPE_NOT_FOUND",
    "FILE_HASH_REQUIRED",
    "FORMAT_REQUIRED",
    "LICENSE_REQUIRED",
    "PACK_ID",
    "RUNTIME_REQUIRED",
    "SUITE_MEMBER_NOT_FOUND",
    "advance_admission",
    "advance_admission_async",
    "put_envelope",
    "put_suite_member",
    "resolve_admission",
    "resolve_admission_async",
    "seed_core_suite",
    "submit_admission",
    "submit_admission_async",
]


def _now(clock: datetime | None) -> datetime:
    stamp = clock if clock is not None else datetime.now(UTC)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp


def put_envelope(
    store: EnvelopeStore,
    tenant_id: uuid.UUID,
    body: ClassEnvelopeCreate,
    *,
    now: datetime | None = None,
) -> ClassEnvelope:
    class_id = _safe_text(body.class_id)
    catalog_id = _safe_text(body.catalog_id)
    file_hash = (body.file_hash or "").strip()
    runtime = _safe_text(body.runtime)
    if class_id is None:
        raise HTTPException(status_code=400, detail=CLASS_ID_REQUIRED)
    if catalog_id is None:
        raise HTTPException(status_code=400, detail=CATALOG_ID_REQUIRED)
    if not file_hash:
        raise HTTPException(status_code=400, detail=FILE_HASH_REQUIRED)
    if runtime is None:
        raise HTTPException(status_code=400, detail=RUNTIME_REQUIRED)
    clock = _now(now)
    existing = store.for_class_catalog(tenant_id, class_id, catalog_id)
    if existing is not None:
        row = existing.model_copy(
            update={
                "file_hash": file_hash[:256],
                "quant": _safe_text(body.quant),
                "runtime": runtime,
                "context": body.context,
                "fit": bool(body.fit),
                "decode_tok_s": body.decode_tok_s,
                "thermal_c": body.thermal_c,
                "updated_at": clock,
            }
        )
    else:
        row = ClassEnvelope(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            class_id=class_id,
            catalog_id=catalog_id,
            file_hash=file_hash[:256],
            quant=_safe_text(body.quant),
            runtime=runtime,
            context=body.context,
            fit=bool(body.fit),
            decode_tok_s=body.decode_tok_s,
            thermal_c=body.thermal_c,
            created_at=clock,
            updated_at=clock,
        )
    store.put(row)
    return row


def put_suite_member(
    store: SuiteStore,
    tenant_id: uuid.UUID,
    body: SuiteMemberCreate,
    *,
    now: datetime | None = None,
) -> SuiteMember:
    class_id = _safe_text(body.class_id)
    catalog_id = _safe_text(body.catalog_id)
    pack_id = _safe_text(body.pack_id) or PACK_ID
    if class_id is None:
        raise HTTPException(status_code=400, detail=CLASS_ID_REQUIRED)
    if catalog_id is None:
        raise HTTPException(status_code=400, detail=CATALOG_ID_REQUIRED)
    existing = store.for_class_catalog(tenant_id, class_id, catalog_id)
    if existing is not None:
        row = existing.model_copy(update={"pack_id": pack_id})
        store.put(row)
        return row
    row = SuiteMember(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        class_id=class_id,
        catalog_id=catalog_id,
        pack_id=pack_id,
        created_at=_now(now),
    )
    store.put(row)
    return row


def seed_core_suite(
    store: SuiteStore,
    tenant_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> list[SuiteMember]:
    """Ensure pack-v1 / llama-3.1-8b-q4 membership for agx-large and nx-volume."""

    clock = _now(now)
    rows: list[SuiteMember] = []
    for class_id, catalog_id, pack_id in CORE_SUITE_SEED:
        rows.append(
            put_suite_member(
                store,
                tenant_id,
                SuiteMemberCreate(
                    class_id=class_id, catalog_id=catalog_id, pack_id=pack_id
                ),
                now=clock,
            )
        )
    return rows
