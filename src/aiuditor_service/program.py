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
    record_sandbox_result,
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
    SuiteMemberReplace,
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
CATALOG_LLAMA_8B_NX = "llama-3.1-8b-q4-nx"

# Compact host cert seed: pack-v1 / llama-3.1-8b-q4 on public Orin classes.
CORE_SUITE_SEED: tuple[tuple[str, str, str], ...] = (
    ("agx-large", CATALOG_LLAMA_8B, PACK_ID),
    ("nx-volume", CATALOG_LLAMA_8B_NX, PACK_ID),
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
    "replace_suite_member",
    "record_sandbox_result",
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
    if_absent: bool = False,
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
    if existing is not None and if_absent:
        raise HTTPException(409, "Reference envelope already exists")
    if existing is not None:
        row = existing.model_copy(
            update={
                "lab_job_id": None, "lab_device_id": None, "lab_origin": None,
                "binding_sha256": None, "report_sha256": None, "measured_at": None,
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
    if if_absent:
        if not store.create_if_absent(row):
            raise HTTPException(409, "Reference envelope already exists")
    else:
        store.put(row)
    return row


def _put_suite_member(
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


def put_suite_member(store: SuiteStore, tenant_id: uuid.UUID, body: SuiteMemberCreate,
                     *, now: datetime | None = None) -> SuiteMember:
    with store.persist.program_bootstrap_scope(tenant_id):
        return _put_suite_member(store, tenant_id, body, now=now)


def replace_suite_member(store: SuiteStore, tenant_id: uuid.UUID, member_id: uuid.UUID,
                         body: SuiteMemberReplace) -> SuiteMember:
    """Replace one class's model atomically without adding a second workload."""
    with store.persist.program_bootstrap_scope(tenant_id):
        current = store.get(tenant_id, member_id)
        if current is None:
            raise HTTPException(404, SUITE_MEMBER_NOT_FOUND)
        if current.class_id != body.class_id:
            raise HTTPException(409, "Suite replacement must keep its hardware class")
        if current.catalog_id == body.catalog_id and current.pack_id == body.pack_id:
            return current
        if (current.catalog_id != body.expected_catalog_id
                or current.pack_id != body.expected_pack_id):
            raise HTTPException(409, "Suite changed; review its current configuration")
        duplicate = store.for_class_catalog(tenant_id, body.class_id, body.catalog_id)
        if duplicate is not None and duplicate.id != member_id:
            raise HTTPException(409, "Catalog is already a member of this class suite")
        return store.put(current.model_copy(update={"catalog_id":body.catalog_id,"pack_id":body.pack_id}))


def seed_core_suite(
    store: SuiteStore,
    tenant_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> list[SuiteMember]:
    """Ensure pack-v1 membership for each class-specific 8B catalog entry."""

    clock = _now(now)
    rows: list[SuiteMember] = []
    with store.persist.program_bootstrap_scope(tenant_id):
        for class_id, catalog_id, pack_id in CORE_SUITE_SEED:
            existing = store.list_for(tenant_id, class_id=class_id)
            if existing:
                # Generic seeding may initialize documented defaults, but must
                # never modify or add workloads to an operator-managed class.
                rows.extend(existing)
                continue
            rows.append(_put_suite_member(store, tenant_id, SuiteMemberCreate(
                class_id=class_id, catalog_id=catalog_id, pack_id=pack_id,
            ), now=clock))
    return rows
