"""Model cert program: class envelopes, core suite, admission verdicts.

Hypermesh stays the measurement and lease store. AIuditor owns which
artifacts were soaked per class, which pack ids form the compact host
cert, and whether an uploaded model is admitted. Quarantine / sandbox
are stubs that fail closed (pending until a named verdict).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException

from aiuditor_service.approvals import _safe_text
from aiuditor_service.credentials import credential_ref
from aiuditor_service.domain.models import (
    Admission,
    AdmissionCreate,
    AdmissionResolve,
    AdmissionStatus,
    AdmissionVerdict,
    ClassEnvelope,
    ClassEnvelopeCreate,
    SuiteMember,
    SuiteMemberCreate,
)
from aiuditor_service.store import AdmissionStore, EnvelopeStore, SuiteStore

ENVELOPE_NOT_FOUND = "Envelope not found"
SUITE_MEMBER_NOT_FOUND = "Suite member not found"
ADMISSION_NOT_FOUND = "Admission not found"
ADMISSION_ALREADY_RESOLVED = "admission already has a verdict"
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


def submit_admission(
    store: AdmissionStore,
    tenant_id: uuid.UUID,
    body: AdmissionCreate,
    *,
    now: datetime | None = None,
) -> Admission:
    """Record an upload. Stays pending — quarantine/sandbox stubs fail closed."""

    file_hash = (body.file_hash or "").strip()
    fmt = _safe_text(body.format)
    license_name = _safe_text(body.license)
    intended = _safe_text(body.intended_class)
    if not file_hash:
        raise HTTPException(status_code=400, detail=FILE_HASH_REQUIRED)
    if fmt is None:
        raise HTTPException(status_code=400, detail=FORMAT_REQUIRED)
    if license_name is None:
        raise HTTPException(status_code=400, detail=LICENSE_REQUIRED)
    if intended is None:
        raise HTTPException(status_code=400, detail=CLASS_ID_REQUIRED)
    cred = None
    if body.credential:
        cred = credential_ref(body.credential)
    row = Admission(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        file_hash=file_hash[:256],
        format=fmt,
        quant=_safe_text(body.quant),
        license=license_name,
        intended_class=intended,
        credential_ref=cred,
        status=AdmissionStatus.PENDING,
        verdict=None,
        reason=None,
        resolved_by=None,
        created_at=_now(now),
        resolved_at=None,
    )
    store.put(row)
    return row


def resolve_admission(
    store: AdmissionStore,
    tenant_id: uuid.UUID,
    admission_id: uuid.UUID,
    body: AdmissionResolve,
    *,
    resolved_by: str,
    now: datetime | None = None,
) -> Admission:
    row = store.get(tenant_id, admission_id)
    if row is None:
        raise HTTPException(status_code=404, detail=ADMISSION_NOT_FOUND)
    if row.verdict is not None:
        raise HTTPException(status_code=409, detail=ADMISSION_ALREADY_RESOLVED)
    clock = _now(now)
    status = (
        AdmissionStatus.ALLOWED
        if body.verdict is AdmissionVerdict.ALLOW
        else AdmissionStatus.DENIED
    )
    updated = row.model_copy(
        update={
            "status": status,
            "verdict": body.verdict,
            "reason": _safe_text(body.reason),
            "resolved_by": resolved_by,
            "resolved_at": clock,
        }
    )
    store.put(updated)
    return updated
