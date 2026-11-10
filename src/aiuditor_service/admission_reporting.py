"""Authenticated, job-bound upload evidence; never human approval authority."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from aiuditor_service.domain.models import Admission, AdmissionSandboxReport, AdmissionStatus

PURPOSE = b"aiuditor.admission-sandbox.v1\n"


class Assignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol_version: Literal[1] = 1
    tenant_id: uuid.UUID
    admission_id: uuid.UUID
    job_id: uuid.UUID
    device_id: uuid.UUID
    class_id: str
    catalog_id: str
    file_hash: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    binding_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("Explicit timezone required")
        return value.astimezone(UTC)


class Report(Assignment):
    soak_passed: bool = Field(strict=True)
    egress_ok: bool | None = Field(default=None, strict=True)
    egress_reason: str | None = Field(default=None, max_length=64, pattern=r"^[a-z0-9_]+$")
    measured_at: datetime
    issued_at: datetime

    @field_validator("measured_at", "issued_at")
    @classmethod
    def aware_result(cls, value):
        return Assignment.aware(value)

    def evidence_hash(self):
        value = self.model_dump(mode="json", exclude={"issued_at"})
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def authenticated_report(request):
    # Authenticate the producer before parsing or loading any tenant state.
    key = os.environ.get("AIUDITOR_LAB_REPORT_KEY", "")
    generic = [os.environ.get(name, "") for name in
        ("AIUDITOR_INTERNAL_TOKEN", "HYPERMESH_INTERNAL_TOKEN", "AIUDITOR_MODEL_ADMISSION_TOKEN")]
    if len(key) < 32 or key in [value for value in generic if value]:
        raise HTTPException(503, "Dedicated lab reporting authority is not configured")
    raw = await request.body()
    signature = request.headers.get("X-Lab-Signature", "")
    expected = hmac.new(key.encode(), PURPOSE + raw, hashlib.sha256).hexdigest()
    if re.fullmatch(r"[a-f0-9]{64}", signature) is None or not hmac.compare_digest(signature, expected):
        raise HTTPException(401, "Authenticated lab report required")
    from pydantic import ValidationError
    try:
        report = Report.model_validate_json(raw)
    except ValidationError:
        raise HTTPException(422, "Invalid lab report") from None
    if request.headers.get("X-Tenant-ID") != str(report.tenant_id):
        raise HTTPException(403, "Lab report tenant mismatch")
    now = datetime.now(UTC)
    if not -timedelta(seconds=30) <= now - report.issued_at <= timedelta(minutes=5):
        raise HTTPException(401, "Lab report authentication expired")
    return report, now


def _apply(row, report, now):
    if row is None:
        raise HTTPException(404, "Admission not found")
    assignment = Assignment.model_validate(report.model_dump(include=set(Assignment.model_fields))).model_dump(mode="json")
    digest = report.evidence_hash()
    if row.lab_attempt_history:
        from aiuditor_service.admission_retry import validate_history
        for attempt in validate_history(row):
            previous = attempt['snapshot']
            if previous['lab_assignment'] == assignment:
                if previous['sandbox_report_sha256'] != digest:
                    raise HTTPException(409, "Lab run already binds different evidence")
                return row  # Acknowledge old immutable evidence without changing the current run.
    if row.lab_assignment != assignment:
        raise HTTPException(409, "Report does not match the assigned lab run")
    if row.sandbox_report_sha256 is not None:
        if row.sandbox_report_sha256 != digest:
            raise HTTPException(409, "Lab run already binds different evidence")
        return row
    if row.verdict is not None or row.status is not AdmissionStatus.SANDBOXING:
        raise HTTPException(409, "Admission is no longer awaiting lab evidence")
    if not report.created_at <= report.measured_at <= now + timedelta(seconds=30) or now - report.measured_at > timedelta(hours=24):
        raise HTTPException(409, "A fresh assigned lab run is required")
    # Reuse the policy reducer inside an isolated memory store. Persist only the
    # complete transition and its digest together, never its intermediate rows.
    from aiuditor_service.admission import record_sandbox_result
    from aiuditor_service.store import AdmissionStore
    scratch = AdmissionStore()
    scratch.put(row)
    accepted = record_sandbox_result(scratch, row.tenant_id, row.id,
        AdmissionSandboxReport(soak_passed=report.soak_passed,
            egress_ok=report.egress_ok, egress_reason=report.egress_reason), now=now)
    return accepted.model_copy(update={"sandbox_report_sha256": digest})


def persist_report(persist, report, now):
    """Serialize the report receipt and policy transition on the admission row."""
    return mutate_admission(persist, report.tenant_id, report.admission_id,
        lambda row: _apply(row, report, now))


def bind_assignment(row, queued, catalog_id):
    """Only the internal queue owner's response can install measurement authority."""
    from aiuditor_service.hypermesh import HostLookupUnavailable
    try:
        assignment = Assignment.model_validate(queued["lab_assignment"])
    except (KeyError, TypeError, ValueError):
        raise HostLookupUnavailable("Trusted lab assignment unavailable") from None
    if (assignment.tenant_id != row.tenant_id or assignment.admission_id != row.id
            or assignment.file_hash != row.file_hash or assignment.class_id != row.intended_class
            or assignment.catalog_id != catalog_id
            or str(assignment.job_id) != str(queued.get("soak_job_id"))
            or str(assignment.device_id) != str(queued.get("device_id"))):
        raise HostLookupUnavailable("Trusted lab assignment mismatch")
    return assignment.model_dump(mode="json")


def install_assignment(persist, tenant_id, admission_id, assignment):
    """A delayed queue retry cannot replace an assignment or accepted verdict."""
    def install(row):
        if row is None:
            raise HTTPException(404, "Admission not found")
        if row.lab_assignment is not None or row.verdict is not None:
            return row
        return row.model_copy(update={"lab_assignment": assignment,
            "status": AdmissionStatus.SANDBOXING, "reason": "lab quarantine soak queued"})
    return mutate_admission(persist, tenant_id, admission_id, install)


def mutate_admission(persist, tenant_id, admission_id, apply):
    """Verdicts share the evidence lock and read the latest complete row."""
    from aiuditor_service.persist import MemoryPersistSession, dump_for_persist, sql_row
    if isinstance(persist, MemoryPersistSession):
        with persist._lab_report_lock:
            bucket = persist.rows.setdefault("admissions", {}).setdefault(str(tenant_id), {})
            old = bucket.get(str(admission_id))
            row = apply(Admission.model_validate(old) if old else None)
            bucket[str(row.id)] = dump_for_persist(row)
            return row
    from sqlalchemy import select, text
    from aiuditor_service.models import AiuditorAdmission
    with persist._session_factory() as session, session.begin():
        session.execute(text("SELECT set_config('app.current_tenant_id', :tid, true)"), {"tid": str(tenant_id)})
        old = session.scalar(select(AiuditorAdmission).where(
            AiuditorAdmission.tenant_id == tenant_id, AiuditorAdmission.id == admission_id).with_for_update())
        row = apply(Admission.model_validate({c.name:getattr(old,c.name) for c in old.__table__.columns}) if old else None)
        for key, value in sql_row("admissions", dump_for_persist(row)).items():
            setattr(old, key, value)
        return row
