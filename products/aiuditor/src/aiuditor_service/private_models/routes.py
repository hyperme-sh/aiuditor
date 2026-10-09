"""Scoped private admissions: trusted lab evidence plus live named-human approval."""

import os
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from aiuditor_service.admission import LICENSE_ALLOWLIST
from aiuditor_service.models import AiuditorPrivateModelAdmission
from aiuditor_service.private_models.contracts import (
    AdmissionCreate,
    SandboxEvidence,
    Verdict,
    canonical_hash,
)
from aiuditor_service.role import require_grant_write, role_from_request
from aiuditor_service.tenant import bearer_secret, require_tenant_id
from controlplane_identity.security.tenant_session_jwt import (
    is_smoke_session,
    try_decode_tenant_session_jwt,
)
from controlplane_tenancy.db_session import get_db_session, get_session_maker
from controlplane_tenancy.tenant_guc import set_current_tenant_id_guc
from controlplane_tenancy.tenant_membership import get_active_tenant_role_member
from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

router = APIRouter(prefix="/aiuditor", tags=["private-model-admission"])


def internal_tenant(request):
    require_grant_write(role_from_request(request))
    expected = os.environ.get("AIUDITOR_MODEL_ADMISSION_TOKEN", "")
    presented = request.headers.get("X-Model-Admission-Token", "")
    if len(expected) < 32 or not secrets.compare_digest(
        expected.encode(), presented.encode()
    ):
        raise HTTPException(401, "Private admission service credential required")
    try:
        return uuid.UUID(request.headers["X-Tenant-ID"])
    except (KeyError, ValueError):
        raise HTTPException(400, "Tenant cross-check required") from None


def human(request):
    require_grant_write(role_from_request(request))
    claims = try_decode_tenant_session_jwt(bearer_secret(request))
    if (
        not claims
        or claims.get("azp") != "controlplane-frontend"
        or is_smoke_session(claims)
    ):
        raise HTTPException(401, "Named human tenant session required")
    # AMR is copied from verified IdP claims into the signed tenant session.
    # Missing or machine-only authentication evidence cannot grant human authority.
    methods = claims.get("amr")
    if not isinstance(methods, list) or not any(
        isinstance(method, str) and method in {"pwd", "otp", "mfa", "webauthn", "hwk"}
        for method in methods
    ):
        raise HTTPException(403, "Verified interactive authentication is required")
    subject = claims.get("sub")
    if (
        not isinstance(subject, str)
        or not subject.strip()
        or len(subject) > 512
    ):
        raise HTTPException(403, "A named human must resolve admission")
    tenant = require_tenant_id(request)
    if str(tenant) != str(claims.get("tenant_id")):
        raise HTTPException(403, "Session tenant mismatch")
    return tenant, subject


@asynccontextmanager
async def transaction(tenant, response):
    response.headers["Cache-Control"] = "no-store"
    try:
        request_session = get_db_session()
    except RuntimeError:
        request_session = None
    if request_session is not None:
        try:
            await set_current_tenant_id_guc(request_session, tenant)
            yield request_session
            await request_session.commit()
            await set_current_tenant_id_guc(request_session, tenant)
        except SQLAlchemyError:
            await request_session.rollback()
            raise HTTPException(503, "Private admission state unavailable") from None
        except BaseException:
            await request_session.rollback()
            raise
        return
    async with get_session_maker()() as session:
        try:
            await set_current_tenant_id_guc(session, tenant)
            yield session
            await session.commit()
        except SQLAlchemyError:
            await session.rollback()
            raise HTTPException(503, "Private admission state unavailable") from None
        except BaseException:
            await session.rollback()
            raise


async def approver(session, tenant, subject):
    for role in ("auditor", "admin"):
        if (
            await get_active_tenant_role_member(
                session, tenant, keycloak_sub=subject, role=role
            )
            is not None
        ):
            return
    raise HTTPException(403, "An active auditor or admin membership is required")


async def admission(session, tenant, admission_id):
    row = await session.scalar(
        select(AiuditorPrivateModelAdmission)
        .where(
            AiuditorPrivateModelAdmission.tenant_id == tenant,
            AiuditorPrivateModelAdmission.id == admission_id,
        )
        .with_for_update()
    )
    if row is None:
        raise HTTPException(404, "Private admission not found")
    return row


def view(row):
    return {
        "admission_id": row.id,
        "target": row.target,
        "target_sha256": row.target_sha256,
        "lab_job_id": row.lab_job_id,
        "lab_device_id": row.lab_device_id,
        "status": row.status,
        "evidence": row.evidence,
        "audit": row.audit,
        "created_at": row.created_at,
    }


@router.post("/internal/private-model-admissions")
async def create(request: Request, response: Response, body: AdmissionCreate):
    tenant = internal_tenant(request)
    if body.target.license.strip().lower() not in LICENSE_ALLOWLIST:
        raise HTTPException(422, "License not on admission allowlist")
    async with transaction(tenant, response) as session:
        # Serialize an idempotent create even when the caller retries concurrently.
        from sqlalchemy import text

        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"private-admission:{tenant}:{body.admission_id}"},
        )
        existing = await session.get(AiuditorPrivateModelAdmission, body.admission_id)
        target = body.target.model_dump(mode="json")
        if existing is not None:
            if (
                existing.tenant_id != tenant
                or existing.target != target
                or existing.lab_job_id != body.lab_job_id
                or existing.lab_device_id != body.lab_device_id
            ):
                raise HTTPException(409, "Admission ID already binds another target")
            return view(existing)
        row = AiuditorPrivateModelAdmission(
            id=body.admission_id,
            tenant_id=tenant,
            owner_sub=body.target.owner_sub,
            target=target,
            target_sha256=canonical_hash(target),
            lab_job_id=body.lab_job_id,
            lab_device_id=body.lab_device_id,
            status="sandboxing",
            evidence=None,
            audit=[],
            created_at=datetime.now(UTC),
        )
        session.add(row)
        await session.flush()
        return view(row)


@router.get("/internal/private-model-admissions/{admission_id}")
async def internal_read(request: Request, response: Response, admission_id: uuid.UUID):
    tenant = internal_tenant(request)
    async with transaction(tenant, response) as session:
        return view(await admission(session, tenant, admission_id))


@router.post("/internal/private-model-admissions/{admission_id}/sandbox")
async def sandbox(
    request: Request, response: Response, admission_id: uuid.UUID, body: SandboxEvidence
):
    tenant = internal_tenant(request)
    async with transaction(tenant, response) as session:
        row = await admission(session, tenant, admission_id)
        target = row.target
        if (
            body.target_sha256,
            body.lab_job_id,
            body.lab_device_id,
            body.observed_class_id,
            body.file_sha256,
            body.backend,
            body.runtime_image_digest,
            body.configuration_sha256,
        ) != (
            row.target_sha256,
            row.lab_job_id,
            row.lab_device_id,
            target["class_id"],
            target["file_sha256"],
            target["backend"],
            target["runtime_image_digest"],
            target["configuration_sha256"],
        ):
            raise HTTPException(
                409, "Sandbox evidence does not match the complete scoped target"
            )
        evidence = body.model_dump(mode="json")
        if row.evidence is not None:
            if row.evidence != evidence:
                raise HTTPException(409, "Sandbox evidence already recorded")
            return view(row)
        if row.status != "sandboxing":
            raise HTTPException(409, "Admission is no longer sandboxing")
        row.evidence = evidence
        row.status = (
            "hold"
            if body.soak_passed and body.egress_ok is True and body.egress_reason
            else "failed"
        )
        return view(row)


@router.get("/private-model-admissions")
async def list_holds(request: Request, response: Response):
    tenant, subject = human(request)
    async with transaction(tenant, response) as session:
        await approver(session, tenant, subject)
        rows = await session.scalars(
            select(AiuditorPrivateModelAdmission)
            .where(AiuditorPrivateModelAdmission.tenant_id == tenant)
            .order_by(AiuditorPrivateModelAdmission.created_at.desc())
            .limit(100)
        )
        return [view(row) for row in rows]


@router.post("/private-model-admissions/{admission_id}/verdict")
async def verdict(
    request: Request, response: Response, admission_id: uuid.UUID, body: Verdict
):
    tenant, subject = human(request)
    async with transaction(tenant, response) as session:
        await approver(session, tenant, subject)
        row = await admission(session, tenant, admission_id)
        if row.status in {"allowed", "denied", "revoked"}:
            raise HTTPException(409, "Private admission already resolved")
        if body.verdict == "allow" and (
            row.status != "hold"
            or not row.evidence
            or row.evidence.get("soak_passed") is not True
            or row.evidence.get("egress_ok") is not True
        ):
            raise HTTPException(
                409, "Scoped soak and explicit egress evidence required"
            )
        row.status = "allowed" if body.verdict == "allow" else "denied"
        row.audit = [
            *row.audit,
            {
                "action": body.verdict,
                "reason": body.reason,
                "resolved_by": subject,
                "at": datetime.now(UTC).isoformat(),
            },
        ]
        return view(row)


@router.post("/private-model-admissions/{admission_id}/revoke")
async def revoke(request: Request, response: Response, admission_id: uuid.UUID):
    tenant, subject = human(request)
    async with transaction(tenant, response) as session:
        await approver(session, tenant, subject)
        row = await admission(session, tenant, admission_id)
        if row.status == "revoked":
            return view(row)
        if row.status != "allowed":
            raise HTTPException(409, "Only an allowed admission can be revoked")
        row.status = "revoked"
        row.audit = [
            *row.audit,
            {
                "action": "revoke",
                "resolved_by": subject,
                "at": datetime.now(UTC).isoformat(),
            },
        ]
        return view(row)
