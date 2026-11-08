"""Uploaded-model admission end to end: static gate → quarantine → sandbox → verdict.

Weights never land on the plane. Quarantine/sandbox are job/state seams for a
lab host inside the workload jail. Fail closed on unexpected files, license
denials, size/hash mismatch, or any egress outside the overlay peer. LLM is
never resolved_by.

Sandbox does not pass unless the lab host reports soak completed and
egress_ok=True explicitly. Missing report or omitted flag fails closed.
Allow promotes a Hypermesh catalog row for the intended class only (still
uncertified) only after that sandbox pass. Deny never promotes. Certified
stays on the green lab scorecard path.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException

from aiuditor_service.admission_catalog import admission_catalog_id
from aiuditor_service.approvals import _safe_text, hold_approval
from aiuditor_service.credentials import credential_ref
from aiuditor_service.domain.models import (
    Admission,
    AdmissionCreate,
    AdmissionResolve,
    AdmissionSandboxReport,
    AdmissionStatus,
    AdmissionVerdict,
    ApprovalCreate,
    ApprovalWhy,
)
from aiuditor_service.hypermesh import HostLookupUnavailable, PlaneHop
from aiuditor_service.store import AdmissionStore, ApprovalStore

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
EGRESS_UNOBSERVED = "sandbox egress not observed"
SOAK_REQUIRED = "lab soak must pass before allow"
SOAK_FAILED = "lab soak failed"
LLM_RESOLVER = "llm"
PROMOTE_FAILED = "catalog promote failed"

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


def sandbox_passed(row: Admission) -> bool:
    """True only when soak completed and egress was explicitly ok."""

    return row.sandbox_soak_ok is True and row.sandbox_egress_ok is True


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
        sandbox_soak_ok=None,
        sandbox_egress_ok=None,
        sandbox_egress_reason=None,
        created_at=clock,
        resolved_at=None,
    )
    store.put(row)
    if not run_pipeline:
        return row
    return advance_admission(
        store,
        tenant_id,
        row.id,
        body=body,
        hold_for_human=bool(body.hold_for_human),
        now=clock,
    )


async def submit_admission_async(
    store: AdmissionStore,
    tenant_id: uuid.UUID,
    body: AdmissionCreate,
    *,
    plane: PlaneHop | None = None,
    approvals: ApprovalStore | None = None,
    now: datetime | None = None,
) -> Admission:
    """Submit + quarantine onto a lab host + await soak before human hold."""

    row = submit_admission(store, tenant_id, body, now=now, run_pipeline=False)
    return await advance_admission_async(
        store,
        tenant_id,
        row.id,
        body=body,
        hold_for_human=bool(body.hold_for_human),
        plane=plane,
        approvals=approvals,
        now=now,
    )


def advance_admission(
    store: AdmissionStore,
    tenant_id: uuid.UUID,
    admission_id: uuid.UUID,
    *,
    body: AdmissionCreate | None = None,
    sandbox_egress_ok: bool | None = None,
    sandbox_soak_ok: bool | None = None,
    hold_for_human: bool = False,
    now: datetime | None = None,
) -> Admission:
    """Static gate → quarantine → sandbox. Fail closed. Optional human hold.

    ``sandbox_egress_ok`` and ``sandbox_soak_ok`` default to None (not
    reported). Sandbox only advances toward a human verdict when both are
    explicitly True. False egress or failed soak denies. Missing report
    leaves the row quarantined / sandboxing — never defaults to True.
    """

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
        hold_for_human=hold_for_human,
    )
    reason = static_gate(row, gate_body)
    if reason is not None:
        return _deny(store, row, reason=reason, now=clock)

    # Quarantine: weights would land only on a lab host inside the jail.
    updates: dict[str, object] = {
        "status": AdmissionStatus.QUARANTINED,
        "reason": "lab quarantine",
    }
    if sandbox_soak_ok is not None:
        updates["sandbox_soak_ok"] = sandbox_soak_ok
    if sandbox_egress_ok is not None:
        updates["sandbox_egress_ok"] = sandbox_egress_ok
    row = row.model_copy(update=updates)
    store.put(row)

    soak_ok = row.sandbox_soak_ok
    egress_ok = row.sandbox_egress_ok

    # Fail closed: explicit False denies. Missing (None) does not pass.
    if soak_ok is False:
        return _deny(
            store,
            row,
            reason=SOAK_FAILED,
            resolved_by="sandbox",
            now=clock,
        )
    if egress_ok is False:
        return _deny(
            store,
            row,
            reason=EGRESS_DETECTED,
            resolved_by="sandbox",
            now=clock,
        )
    if soak_ok is not True or egress_ok is not True:
        # Awaiting lab soak / egress report. Do not pretend sandbox passed.
        row = row.model_copy(
            update={
                "status": AdmissionStatus.SANDBOXING,
                "reason": "awaiting lab soak",
            }
        )
        store.put(row)
        return row

    row = row.model_copy(
        update={"status": AdmissionStatus.SANDBOXING, "reason": "sandbox run"}
    )
    store.put(row)

    want_hold = hold_for_human or bool(gate_body.hold_for_human)
    if want_hold:
        row = row.model_copy(
            update={
                "status": AdmissionStatus.HOLD,
                "reason": "human hold",
            }
        )
        store.put(row)
        return row

    row = row.model_copy(
        update={
            "status": AdmissionStatus.PENDING,
            "reason": "awaiting named verdict",
        }
    )
    store.put(row)
    return row


def record_sandbox_result(
    store: AdmissionStore,
    tenant_id: uuid.UUID,
    admission_id: uuid.UUID,
    body: AdmissionSandboxReport,
    *,
    approvals: ApprovalStore | None = None,
    hold_for_human: bool = True,
    now: datetime | None = None,
) -> Admission:
    """Apply lab soak + egress observation. Omitted egress_ok fails closed."""

    row = store.get(tenant_id, admission_id)
    if row is None:
        raise HTTPException(status_code=404, detail=ADMISSION_NOT_FOUND)
    if row.verdict is not None:
        return row
    clock = _now(now)
    # Explicit True only. Missing / None is not ok.
    egress_ok = True if body.egress_ok is True else False
    reason_code = _safe_text(body.egress_reason)
    if body.egress_ok is not True and reason_code is None:
        reason_code = "egress_unobserved" if body.egress_ok is None else "egress_denied"
    row = row.model_copy(
        update={
            "sandbox_soak_ok": bool(body.soak_passed),
            "sandbox_egress_ok": egress_ok if body.egress_ok is True else False,
            "sandbox_egress_reason": (reason_code or None),
            "status": AdmissionStatus.SANDBOXING,
            "reason": "sandbox report",
        }
    )
    store.put(row)

    if not body.soak_passed:
        return _deny(
            store, row, reason=SOAK_FAILED, resolved_by="sandbox", now=clock
        )
    if body.egress_ok is not True:
        deny_reason = (
            EGRESS_UNOBSERVED if body.egress_ok is None else EGRESS_DETECTED
        )
        return _deny(
            store, row, reason=deny_reason, resolved_by="sandbox", now=clock
        )

    if hold_for_human:
        if approvals is not None:
            hold_approval(
                approvals,
                tenant_id,
                ApprovalCreate(
                    purpose="admission",
                    who=f"upload:{row.file_hash[:20]}",
                    why=ApprovalWhy.LEASE,
                    ttl_seconds=24 * 60 * 60,
                ),
                now=clock,
            )
        row = row.model_copy(
            update={"status": AdmissionStatus.HOLD, "reason": "human hold"}
        )
        store.put(row)
        return row

    row = row.model_copy(
        update={
            "status": AdmissionStatus.PENDING,
            "reason": "awaiting named verdict",
        }
    )
    store.put(row)
    return row


async def advance_admission_async(
    store: AdmissionStore,
    tenant_id: uuid.UUID,
    admission_id: uuid.UUID,
    *,
    body: AdmissionCreate | None = None,
    sandbox_egress_ok: bool | None = None,
    sandbox_soak_ok: bool | None = None,
    hold_for_human: bool = True,
    plane: PlaneHop | None = None,
    approvals: ApprovalStore | None = None,
    now: datetime | None = None,
) -> Admission:
    """Quarantine onto one lab host; human hold waits for sandbox report.

    Crawl/lab-discovered packs are not routed here — missing admission must
    not block that path. Deny never promotes. Allow still promotes an
    uncertified catalog row via ``resolve_admission_async`` only after
    sandbox pass.
    """

    row = store.get(tenant_id, admission_id)
    if row is None:
        raise HTTPException(status_code=404, detail=ADMISSION_NOT_FOUND)
    if row.lab_assignment is not None or row.verdict is not None:
        return row
    source_ref = body.source_ref if body is not None else None
    gate_body = body or AdmissionCreate(
        file_hash=row.file_hash,
        format=row.format,
        quant=row.quant,
        license=row.license,
        intended_class=row.intended_class,
        size_bytes=row.size_bytes,
        source_ref=source_ref,
        hold_for_human=hold_for_human,
    )
    # Serialize pure gate transitions with assignment/report/verdict updates.
    # The network queue hop runs after this transaction has released its lock.
    from aiuditor_service.admission_reporting import mutate_admission
    def quarantine(current):
        if current is None:
            raise HTTPException(404, ADMISSION_NOT_FOUND)
        if current.lab_assignment is not None or current.verdict is not None:
            return current
        scratch = AdmissionStore()
        scratch.put(current)
        return advance_admission(scratch, tenant_id, admission_id,
            body=gate_body.model_copy(update={"hold_for_human": False}),
            sandbox_egress_ok=sandbox_egress_ok, sandbox_soak_ok=sandbox_soak_ok,
            hold_for_human=False, now=now)
    advanced = mutate_admission(store.persist, tenant_id, admission_id, quarantine)
    if advanced.verdict is not None or advanced.lab_assignment is not None:
        return advanced

    if sandbox_passed(advanced):
        # Explicit report already present (tests / re-entry).
        return record_sandbox_result(
            store,
            tenant_id,
            advanced.id,
            AdmissionSandboxReport(
                soak_passed=True,
                egress_ok=True,
                egress_reason=advanced.sandbox_egress_reason or "overlay_publish_only",
            ),
            approvals=approvals,
            hold_for_human=hold_for_human,
            now=now,
        )

    if advanced.lab_assignment is not None:
        return advanced

    if plane is not None:
        catalog_id = admission_catalog_id(
            intended_class=advanced.intended_class, file_hash=advanced.file_hash
        )
        try:
            queued = await plane.queue_lab_soak(
                tenant_id,
                class_id=advanced.intended_class,
                catalog_id=catalog_id,
                artifact_name=catalog_id,
                file_hash=advanced.file_hash,
                quant=advanced.quant,
                runtime="nvidia",
                source_ref=(gate_body.source_ref or None),
                enqueue_pull=True,
                admission_id=advanced.id,
                size_bytes=advanced.size_bytes,
            )
            from aiuditor_service.admission_reporting import bind_assignment
            assignment = bind_assignment(advanced, queued, catalog_id)
            from aiuditor_service.admission_reporting import install_assignment
            advanced = install_assignment(store.persist, tenant_id, advanced.id, assignment)
        except HostLookupUnavailable:
            def no_ready_host(current):
                if current is None:
                    raise HTTPException(404, ADMISSION_NOT_FOUND)
                if current.lab_assignment is not None or current.verdict is not None:
                    return current
                return current.model_copy(update={"status": AdmissionStatus.HOLD,
                    "reason": "no ready lab host"})
            advanced = mutate_admission(store.persist, tenant_id, advanced.id, no_ready_host)
            if advanced.lab_assignment is not None or advanced.verdict is not None:
                return advanced
            if approvals is not None:
                hold_approval(
                    approvals,
                    tenant_id,
                    ApprovalCreate(
                        purpose="admission",
                        who=f"upload:{advanced.file_hash[:20]}",
                        why=ApprovalWhy.LEASE,
                        ttl_seconds=24 * 60 * 60,
                    ),
                    now=now,
                )
            return advanced

    # Stay SANDBOXING until the lab host reports soak + egress.
    # Do not open the allow path early.
    return advanced


def resolve_admission(
    store: AdmissionStore,
    tenant_id: uuid.UUID,
    admission_id: uuid.UUID,
    body: AdmissionResolve,
    *,
    resolved_by: str,
    now: datetime | None = None,
) -> Admission:
    """Named-human allow/deny. Deny is a violation-style record, not a lease.

    Prefer ``resolve_admission_async`` when a plane hop can promote on allow.
    Allow requires a successful lab soak with explicit egress_ok.
    """

    actor = (resolved_by or "").strip()
    if not actor or actor.lower() == LLM_RESOLVER:
        raise HTTPException(status_code=400, detail="resolved_by must be a named human")
    def apply_verdict(row):
        if row is None:
            raise HTTPException(status_code=404, detail=ADMISSION_NOT_FOUND)
        if row.verdict is not None:
            raise HTTPException(status_code=409, detail=ADMISSION_ALREADY_RESOLVED)
        if body.verdict is AdmissionVerdict.ALLOW and not sandbox_passed(row):
            raise HTTPException(status_code=409, detail=SOAK_REQUIRED)
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
        return updated
    from aiuditor_service.admission_reporting import mutate_admission
    return mutate_admission(store.persist, tenant_id, admission_id, apply_verdict)


async def resolve_admission_async(
    store: AdmissionStore,
    tenant_id: uuid.UUID,
    admission_id: uuid.UUID,
    body: AdmissionResolve,
    *,
    resolved_by: str,
    plane: PlaneHop | None = None,
    now: datetime | None = None,
) -> Admission:
    """Allow promotes a Hypermesh catalog row for that class; deny does not.

    Promote requires sandbox pass (soak + egress_ok). Certified still waits
    for a green lab scorecard.
    """

    # The recorded verdict is durable authority. An identical allow retry may
    # redeliver promotion, but must never replace its original author/reason/time.
    actor = (resolved_by or "").strip()
    if not actor or actor.lower() == LLM_RESOLVER:
        raise HTTPException(status_code=400, detail="resolved_by must be a named human")
    existing = store.get(tenant_id, admission_id)
    if existing is not None and existing.verdict is AdmissionVerdict.ALLOW:
        if (existing.status is not AdmissionStatus.ALLOWED or not existing.resolved_by
                or existing.resolved_by.lower() == LLM_RESOLVER or existing.resolved_at is None):
            raise HTTPException(status_code=409, detail=ADMISSION_ALREADY_RESOLVED)
        if body.verdict is not AdmissionVerdict.ALLOW:
            raise HTTPException(status_code=409, detail=ADMISSION_ALREADY_RESOLVED)
        if body.reason is not None and _safe_text(body.reason) != existing.reason:
            raise HTTPException(status_code=409, detail=ADMISSION_ALREADY_RESOLVED)
        if not sandbox_passed(existing):
            raise HTTPException(status_code=409, detail=SOAK_REQUIRED)
        updated = existing
    else:
        updated = resolve_admission(
            store,
            tenant_id,
            admission_id,
            body,
            resolved_by=resolved_by,
            now=now,
        )
    if updated.verdict is not AdmissionVerdict.ALLOW:
        # Deny stays the Admission DENIED / policy-deny style record.
        return updated
    if plane is None:
        return updated
    catalog_id = admission_catalog_id(
        intended_class=updated.intended_class, file_hash=updated.file_hash
    )
    try:
        await plane.promote_catalog(
            tenant_id,
            catalog_id=catalog_id,
            class_id=updated.intended_class,
            artifact_name=catalog_id,
            file_hash=updated.file_hash,
            format=updated.format,
            quant=updated.quant,
            admission_id=updated.id,
        )
    except HostLookupUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc) or PROMOTE_FAILED) from exc
    return updated
