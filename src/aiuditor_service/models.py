"""products/aiuditor SQLAlchemy models; table names and schema preserved."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from sqlalchemy.orm import Mapped, mapped_column

from controlplane_db.base import Base


import controlplane_db.models as _platform_models  # noqa: F401; register FK targets

class AiuditorConnection(Base):
    """AIuditor connection row. Hash only. Device secret stays on Hypermesh."""

    control_plane_id: Mapped[str] = mapped_column(String(128), nullable=False, server_default="local")

    __tablename__ = "aiuditor_connections"
    __table_args__ = (
        Index("ix_aiuditor_connections_tenant", "tenant_id"),
        Index("ix_aiuditor_connections_tenant_lease", "tenant_id", "lease_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    device_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    endpoint: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    credential_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AiuditorApproval(Base):
    """AI-use hold. No prompt body. Not fyber.auditor."""

    __tablename__ = "aiuditor_approvals"
    __table_args__ = (Index("ix_aiuditor_approvals_tenant", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    lease_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    connection_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    who: Mapped[str] = mapped_column(String(256), nullable=False)
    why: Mapped[str] = mapped_column(String(16), nullable=False)
    prompt_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AiuditorPolicy(Base):
    """Org allow/hold/deny. No prompt body."""

    __tablename__ = "aiuditor_policies"
    __table_args__ = (Index("ix_aiuditor_policies_tenant", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    connection_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    disposition: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AiuditorContract(Base):
    """Spine §D grant. Not a handoff package. Not a chain SoT."""

    __tablename__ = "aiuditor_contracts"
    __table_args__ = (
        Index("ix_aiuditor_contracts_tenant", "tenant_id"),
        Index("ix_aiuditor_contracts_tenant_lease", "tenant_id", "lease_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    parties: Mapped[list] = mapped_column(JSONB, nullable=False)
    meters: Mapped[list] = mapped_column(JSONB, nullable=False)
    rail: Mapped[str] = mapped_column(String(32), nullable=False)
    until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dispute_tip: Mapped[str | None] = mapped_column(String(120), nullable=True)
    connection_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    lease_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    signed_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AiuditorViolation(Base):
    """Violation on a signed Contract. Dispute reads cert + audit."""

    __tablename__ = "aiuditor_violations"
    __table_args__ = (
        Index("ix_aiuditor_violations_tenant", "tenant_id"),
        Index("ix_aiuditor_violations_tenant_contract", "tenant_id", "contract_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    lease_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AiuditorCatalog(Base):
    """AIuditor catalog row. Not a Hypermesh class or catalog. Not TURN."""

    __tablename__ = "aiuditor_catalog"
    __table_args__ = (Index("ix_aiuditor_catalog_tenant", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    backend: Mapped[str] = mapped_column(String(32), nullable=False)
    class_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    catalog_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    connection_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    meters: Mapped[list] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AiuditorEnvelope(Base):
    """Lab class envelope. One artifact per class_id. Not a Hypermesh scorecard."""

    __tablename__ = "aiuditor_envelopes"
    __table_args__ = (
        Index("ix_aiuditor_envelopes_tenant", "tenant_id"),
        Index("ix_aiuditor_envelopes_tenant_class", "tenant_id", "class_id"),
        UniqueConstraint(
            "tenant_id",
            "class_id",
            "catalog_id",
            name="uq_aiuditor_envelope_class_catalog",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    class_id: Mapped[str] = mapped_column(String(64), nullable=False)
    catalog_id: Mapped[str] = mapped_column(String(64), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    quant: Mapped[str | None] = mapped_column(String(32), nullable=True)
    runtime: Mapped[str] = mapped_column(String(64), nullable=False)
    context: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    decode_tok_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    thermal_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    lab_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    lab_device_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    lab_origin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    binding_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    report_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    measured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AiuditorEnvelopeReport(Base):
    """Immutable accepted evidence receipt; not a cross-owner job foreign key."""
    __tablename__ = "aiuditor_envelope_reports"
    __table_args__ = (CheckConstraint("origin IN ('path_b','qualification')", name="ck_lab_report_origin"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    origin: Mapped[str] = mapped_column(String(32), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    accepted_envelope: Mapped[dict] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AiuditorSuiteMember(Base):
    """Core cert suite membership. Compact-cert jobs read this list."""

    __tablename__ = "aiuditor_suite_members"
    __table_args__ = (
        Index("ix_aiuditor_suite_members_tenant", "tenant_id"),
        Index("ix_aiuditor_suite_members_tenant_class", "tenant_id", "class_id"),
        UniqueConstraint(
            "tenant_id",
            "class_id",
            "catalog_id",
            name="uq_aiuditor_suite_class_catalog",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    class_id: Mapped[str] = mapped_column(String(64), nullable=False)
    catalog_id: Mapped[str] = mapped_column(String(64), nullable=False)
    pack_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AiuditorAdmission(Base):
    """Uploaded-model admission. Quarantine/sandbox stubs stay pending."""

    __tablename__ = "aiuditor_admissions"
    __table_args__ = (
        Index("ix_aiuditor_admissions_tenant", "tenant_id"),
        Index("ix_aiuditor_admissions_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    file_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    format: Mapped[str] = mapped_column(String(32), nullable=False)
    quant: Mapped[str | None] = mapped_column(String(32), nullable=True)
    license: Mapped[str] = mapped_column(String(64), nullable=False)
    intended_class: Mapped[str] = mapped_column(String(64), nullable=False)
    credential_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sandbox_soak_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sandbox_egress_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sandbox_egress_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AiuditorCandidate(Base):
    """Daily crawl metadata pin. Weights never stored here."""

    __tablename__ = "aiuditor_candidates"
    __table_args__ = (
        Index("ix_aiuditor_candidates_tenant", "tenant_id"),
        Index("ix_aiuditor_candidates_tenant_class", "tenant_id", "class_id"),
        Index("ix_aiuditor_candidates_tenant_status", "tenant_id", "status"),
        UniqueConstraint(
            "tenant_id",
            "repo",
            "revision",
            "file_name",
            "class_id",
            name="uq_aiuditor_candidate_source_class",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    repo: Mapped[str] = mapped_column(String(256), nullable=False)
    revision: Mapped[str] = mapped_column(String(128), nullable=False)
    file_name: Mapped[str] = mapped_column(String(256), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    license: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quant: Mapped[str | None] = mapped_column(String(32), nullable=True)
    file_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    publisher: Mapped[str] = mapped_column(String(64), nullable=False)
    catalog_id: Mapped[str] = mapped_column(String(64), nullable=False)
    class_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    soak_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AiuditorMachineIdentity(Base):
    """Credential authority state, isolated for every attesting control plane."""
    __tablename__ = "aiuditor_machine_identities"
    authority_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    control_plane_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    state: Mapped[dict] = mapped_column(JSONB, nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class AiuditorPrivateModelAdmission(Base):
    """Private scoped verdicts never enter the public catalog promotion path."""

    __tablename__ = "aiuditor_private_model_admissions"
    __table_args__ = (
        Index("ix_aud_private_admission_tenant", "tenant_id", "created_at"),
        CheckConstraint("status IN ('sandboxing', 'hold', 'failed', 'allowed', 'denied', 'revoked')", name="ck_aud_private_admission_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    owner_sub: Mapped[str] = mapped_column(String(512), nullable=False)
    target: Mapped[dict] = mapped_column(JSONB, nullable=False)
    target_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    lab_job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    lab_device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    audit: Mapped[list[dict]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AiuditorCrawlState(Base):
    """Tenant crawl cadence; no artifact data, tokens or exception text."""

    __tablename__ = "aiuditor_crawl_state"
    __table_args__ = (CheckConstraint("error_count >= 0", name="ck_aiuditor_crawl_errors"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
