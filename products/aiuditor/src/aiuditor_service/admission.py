"""Uploaded-model admission end to end: static gate → quarantine → sandbox → verdict.

Weights never land on the plane. Quarantine/sandbox are job/state seams for a
lab host inside the workload jail. Fail closed on unexpected files, license
denials, size/hash mismatch, or any egress outside the overlay peer. LLM is
never resolved_by.
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
)
from aiuditor_service.store import AdmissionStore

FILE_HASH_REQUIRED = "file_hash is required"
FORMAT_REQUIRED = "format is required"
LICENSE_REQUIRED = "license is required"
CLASS_ID_REQUIRED = "class_id is required"
ADMISSION_NOT_FOUND = "Admission not found"
ADMISSION_ALREADY_RESOLVED = "admission already has a verdict"
STATIC_GATE_FAILED = "static gate failed"
LICENSE_DENIED = "license not on allowlist"
UNEXPECTED_FILES = "unexpected files beside weights"
SIZE_MISMATCH = "declared size does not match hash pin"
FORMAT_DENIED = "format not allowed"
EGRESS_DETECTED = "sandbox egress outside overlay peer"
LLM_RESOLVER = "llm"

# Licenses we will run. Expand deliberately.
LICENSE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "apache-2.0",
        "mit",
        "bsd-3-clause",
        "bsd-2-clause",
        "llama2",
        "llama3",
        "llama3.1",
        "llama3.2",
        "llama3.3",
        "gemma",
        "qwen",
        "openrail",
        "cc-by-4.0",
    }
)

FORMAT_ALLOWLIST: frozenset[str] = frozenset({"gguf", "safetensors", "onnx"})


def _now(clock: datetime | None) -> datetime:
    stamp = clock if clock is not None else datetime.now(UTC)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp


def _deny(
    store: AdmissionStore,
    row: Admission,
    *,
    reason: str,
    resolved_by: str = "static-gate",
    now: datetime | None = None,
) -> Admission:
    clock = _now(now)
    updated = row.model_copy(
        update={
            "status": AdmissionStatus.DENIED,
            "verdict": AdmissionVerdict.DENY,
            "reason": reason[:120],
            "resolved_by": resolved_by[:256],
            "resolved_at": clock,
        }
    )
    store.put(updated)
    return updated


def static_gate(row: Admission, body: AdmissionCreate) -> str | None:
    """Return a deny reason, or None when the gate passes."""

    fmt = (row.format or "").strip().lower()
    if fmt not in FORMAT_ALLOWLIST:
        return FORMAT_DENIED
    license_name = (row.license or "").strip().lower()
    if license_name not in LICENSE_ALLOWLIST:
        return LICENSE_DENIED
    unexpected = [f for f in (body.extra_files or []) if (f or "").strip()]
    if unexpected:
        return UNEXPECTED_FILES
    if body.size_bytes is not None and body.size_bytes < 0:
        return SIZE_MISMATCH
    # Hash must look like a content pin when size is declared (format/size match hash).
    file_hash = (row.file_hash or "").strip().lower()
    if body.size_bytes is not None and body.size_bytes > 0:
        if not (
            file_hash.startswith("sha256:") and len(file_hash) >= len("sha256:") + 64
        ):
            return SIZE_MISMATCH
    return None


def submit_admission(
    store: AdmissionStore,
    tenant_id: uuid.UUID,
    body: AdmissionCreate,
    *,
    now: datetime | None = None,
    run_pipeline: bool = True,
) -> Admission:
    """Record an upload and optionally advance the fail-closed pipeline."""

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
    clock = _now(now)
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
        size_bytes=body.size_bytes,
        created_at=clock,
        resolved_at=None,
    )
    store.put(row)
    if not run_pipeline:
        return row
    return advance_admission(store, tenant_id, row.id, body=body, now=clock)


def advance_admission(
    store: AdmissionStore,
    tenant_id: uuid.UUID,
    admission_id: uuid.UUID,
    *,
    body: AdmissionCreate | None = None,
    sandbox_egress_ok: bool = True,
    hold_for_human: bool = False,
    now: datetime | None = None,
) -> Admission:
    """Static gate → quarantine → sandbox. Fail closed. Optional human hold."""

    row = store.get(tenant_id, admission_id)
    if row is None:
        raise HTTPException(status_code=404, detail=ADMISSION_NOT_FOUND)
    if row.verdict is not None:
        return row
    clock = _now(now)
    gate_body = body or AdmissionCreate(
        file_hash=row.file_hash,
        format=row.format,
        quant=row.quant,
        license=row.license,
        intended_class=row.intended_class,
        size_bytes=row.size_bytes,
    )
    reason = static_gate(row, gate_body)
    if reason is not None:
        return _deny(store, row, reason=reason, now=clock)

    # Quarantine: weights would land only on a lab host inside the jail.
    row = row.model_copy(
        update={"status": AdmissionStatus.QUARANTINED, "reason": "lab quarantine"}
    )
    store.put(row)

    # Sandbox timed sample + egress check. Fail closed on any other egress.
    if not sandbox_egress_ok:
        return _deny(
            store,
            row,
            reason=EGRESS_DETECTED,
            resolved_by="sandbox",
            now=clock,
        )
    row = row.model_copy(
        update={"status": AdmissionStatus.SANDBOXING, "reason": "sandbox run"}
    )
    store.put(row)

    if hold_for_human:
        row = row.model_copy(
            update={
                "status": AdmissionStatus.HOLD,
                "reason": "human hold",
            }
        )
        store.put(row)
        return row

    # Pipeline ready for a named human verdict — stays pending of allow/deny.
    row = row.model_copy(
        update={
            "status": AdmissionStatus.PENDING,
            "reason": "awaiting named verdict",
        }
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
    actor = (resolved_by or "").strip()
    if not actor or actor.lower() == LLM_RESOLVER:
        raise HTTPException(status_code=400, detail="resolved_by must be a named human")
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
            "resolved_by": actor[:256],
            "resolved_at": clock,
        }
    )
    store.put(updated)
    return updated
