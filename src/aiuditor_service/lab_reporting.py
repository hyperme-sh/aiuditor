"""Purpose-authenticated lab receipts. Only the measurement owner signs reports."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aiuditor_service.domain.models import ClassEnvelope, ClassEnvelopeCreate

PURPOSE = b"aiuditor.lab-envelope.v1\n"
MAX_AGE = timedelta(hours=24)


class Measurement(ClassEnvelopeCreate):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    file_hash: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    fit: bool = Field(default=True, strict=True)

    @model_validator(mode="after")
    def timed_success(self):
        if self.fit and (self.decode_tok_s is None or self.decode_tok_s <= 0):
            raise ValueError("A successful lab report requires a positive measured rate")
        return self


class LabEnvelopeReport(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    protocol_version: Literal[1] = 1
    tenant_id: uuid.UUID
    origin: Literal["path_b", "qualification"]
    job_id: uuid.UUID
    device_id: uuid.UUID
    binding_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    measured_at: datetime
    issued_at: datetime
    envelope: Measurement
    mode: Literal["create", "replace"] = "create"
    expected_revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @field_validator("measured_at", "issued_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("An explicit timezone is required")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def conditional(self):
        if (self.mode == "replace") != (self.expected_revision is not None):
            raise ValueError("Replacement requires an expected revision")
        return self

    def evidence_hash(self):
        # Transport time and CAS intent can change on retry; evidence cannot.
        value = self.model_dump(mode="json", exclude={"issued_at", "mode", "expected_revision"})
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


async def authenticate(request: Request, report: LabEnvelopeReport):
    key = os.environ.get("AIUDITOR_LAB_REPORT_KEY", "")
    generic = [os.environ.get(name, "") for name in
               ("AIUDITOR_INTERNAL_TOKEN", "HYPERMESH_INTERNAL_TOKEN", "AIUDITOR_MODEL_ADMISSION_TOKEN")]
    if len(key) < 32 or key in [token for token in generic if token]:
        raise HTTPException(503, "Dedicated lab reporting authority is not configured")
    signature = request.headers.get("X-Lab-Signature", "")
    expected = hmac.new(key.encode(), PURPOSE + await request.body(), hashlib.sha256).hexdigest()
    if re.fullmatch(r"[a-f0-9]{64}", signature) is None or not hmac.compare_digest(expected, signature):
        raise HTTPException(401, "Authenticated lab report required")
    if request.headers.get("X-Tenant-ID") != str(report.tenant_id):
        raise HTTPException(403, "Lab report tenant mismatch")
    now = datetime.now(UTC)
    if not -timedelta(seconds=30) <= now - report.issued_at <= timedelta(minutes=5):
        raise HTTPException(401, "Lab report authentication expired")
    none_match, match = request.headers.get("If-None-Match"), request.headers.get("If-Match")
    if none_match not in (None, "*") or (none_match is not None and match is not None):
        raise HTTPException(400, "Invalid envelope preconditions")
    if report.mode == "create":
        if match is not None:
            raise HTTPException(400, "Creation cannot supply If-Match")
    elif request.url.path.endswith("/initial") or none_match is not None or match != f'"{report.expected_revision}"':
        raise HTTPException(412, "Replacement requires its signed If-Match revision")
    return now


def _new_row(report, existing, now):
    if report.measured_at > now + timedelta(seconds=30) or report.measured_at < now - MAX_AGE:
        raise HTTPException(409, "A fresh completed lab run is required")
    if report.mode == "create" and existing is not None:
        raise HTTPException(409, "Reference envelope already exists")
    if report.mode == "replace" and (existing is None or existing.revision != report.expected_revision):
        raise HTTPException(412, "Envelope revision changed")
    if existing is not None and existing.measurement_verified and report.measured_at <= existing.measured_at:
        raise HTTPException(409, "A newer lab measurement is required")
    if existing is not None and existing.context is not None and report.origin == "path_b":
        raise HTTPException(409, "A context-qualified baseline requires a complete qualification run")
    return ClassEnvelope(
        id=existing.id if existing else uuid.uuid4(), tenant_id=report.tenant_id,
        **report.envelope.model_dump(), created_at=existing.created_at if existing else now,
        # Freshness is observation time, never the time an old result is retried.
        updated_at=report.measured_at, measured_at=report.measured_at,
        lab_origin=report.origin, lab_job_id=report.job_id, lab_device_id=report.device_id,
        binding_sha256=report.binding_sha256, report_sha256=report.evidence_hash(),
    )


def _replay(receipt, digest):
    if receipt["payload_sha256"] != digest:
        raise HTTPException(409, "Lab run already binds different evidence")
    return ClassEnvelope.model_validate(receipt["accepted_envelope"]), False


def persist_report(persist, report, now):
    """Serialize each job and target; receipt + envelope commit atomically."""
    from aiuditor_service.persist import MemoryPersistSession, dump_for_persist, sql_row
    digest = report.evidence_hash()
    if isinstance(persist, MemoryPersistSession):
        with persist._lab_report_lock:
            tid = str(report.tenant_id)
            key = f"{report.origin}:{report.job_id}"
            receipts = persist.rows.setdefault("envelope_reports", {}).setdefault(tid, {})
            if key in receipts:
                return _replay(receipts[key], digest)
            bucket = persist.rows.setdefault("envelopes", {}).setdefault(tid, {})
            existing = next((ClassEnvelope.model_validate(r) for r in bucket.values()
                             if r["class_id"] == report.envelope.class_id and r["catalog_id"] == report.envelope.catalog_id), None)
            row = _new_row(report, existing, now)
            accepted = row.model_dump(mode="json")
            bucket[str(row.id)] = dump_for_persist(row)
            receipts[key] = {"payload_sha256": digest, "accepted_envelope": accepted}
            return row, True

    from sqlalchemy import select, text
    from aiuditor_service.models import AiuditorEnvelope, AiuditorEnvelopeReport
    with persist._session_factory() as session, session.begin():
        session.execute(text("SELECT set_config('app.current_tenant_id', :tid, true)"), {"tid": str(report.tenant_id)})
        for key in (f"lab-report:{report.tenant_id}:{report.origin}:{report.job_id}",
                    f"lab-envelope:{report.tenant_id}:{report.envelope.class_id}:{report.envelope.catalog_id}"):
            session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": key})
        receipt = session.get(AiuditorEnvelopeReport, (report.tenant_id, report.origin, report.job_id))
        if receipt is not None:
            return _replay({"payload_sha256": receipt.payload_sha256, "accepted_envelope": receipt.accepted_envelope}, digest)
        old = session.scalar(select(AiuditorEnvelope).where(
            AiuditorEnvelope.tenant_id == report.tenant_id,
            AiuditorEnvelope.class_id == report.envelope.class_id,
            AiuditorEnvelope.catalog_id == report.envelope.catalog_id).with_for_update())
        existing = ClassEnvelope.model_validate({c.name: getattr(old, c.name) for c in old.__table__.columns}) if old else None
        row = _new_row(report, existing, now)
        values = sql_row("envelopes", dump_for_persist(row))
        if old:
            for key, value in values.items():
                setattr(old, key, value)
        else:
            session.add(AiuditorEnvelope(**values))
        session.add(AiuditorEnvelopeReport(tenant_id=report.tenant_id, origin=report.origin,
            job_id=report.job_id, payload_sha256=digest,
            accepted_envelope=row.model_dump(mode="json"), received_at=now))
        return row, True
