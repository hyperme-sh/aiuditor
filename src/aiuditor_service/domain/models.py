"""Connection registry and model cert program.

Classes, devices, Path B, and scorecards stay on Hypermesh. This product
owns connections plus class envelopes, core suite membership, and
uploaded-model admission verdicts.
"""

from __future__ import annotations

from aiuditor_service.routing_contracts import RoutingPolicy

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Self
from urllib.parse import urlparse

from pydantic import BaseModel, Field, model_validator, computed_field


class ConnectionKind(StrEnum):
    HOST = "host"
    PROVIDER = "provider"


class ConnectionPurpose(StrEnum):
    LEASE = "lease"
    AUDITOR = "auditor"


class ConnectionStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    DISABLED = "disabled"


def _https_endpoint(raw: str) -> str:
    text = raw.strip()
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("endpoint must be an https URL with no userinfo")
    return text


class ConnectionCreate(BaseModel):
    """POST body. credential is write-only and never persisted or returned."""

    kind: ConnectionKind
    purpose: ConnectionPurpose
    device_id: uuid.UUID | None = None
    endpoint: str | None = Field(default=None, max_length=2048)
    credential: str | None = Field(default=None, min_length=1, max_length=4096)
    # Owning control plane. Must equal the attesting plane (or "local" when the
    # request carries no attestation); omitted means "the attesting plane".
    control_plane_id: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"
    )

    @model_validator(mode="after")
    def _kind_fields(self) -> Self:
        if self.kind is ConnectionKind.HOST:
            if self.device_id is None:
                raise ValueError("host connections require device_id")
            if self.endpoint:
                raise ValueError("host connections must not set endpoint")
            if self.credential:
                raise ValueError(
                    "host connections must not take a credential; "
                    "the device secret stays on Hypermesh"
                )
            return self
        if self.device_id is not None:
            raise ValueError("provider connections must not set device_id")
        if not self.endpoint:
            raise ValueError("provider connections require endpoint")
        self.endpoint = _https_endpoint(self.endpoint)
        return self


class Connection(BaseModel):
    """Tenant-scoped connection row. credential_ref is a hash, never the secret."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    control_plane_id: str = Field(default="local", min_length=1, max_length=128)
    kind: ConnectionKind
    purpose: ConnectionPurpose
    device_id: uuid.UUID | None = None
    endpoint: str | None = None
    credential_ref: str | None = None
    lease_id: uuid.UUID | None = None
    status: ConnectionStatus
    created_at: datetime

    def to_event_payload(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "kind": self.kind.value,
            "purpose": self.purpose.value,
            "device_id": str(self.device_id) if self.device_id else None,
            "endpoint": self.endpoint,
            "credential_ref": self.credential_ref,
            "lease_id": str(self.lease_id) if self.lease_id else None,
            "status": self.status.value,
        }


class AllowedHost(BaseModel):
    """One host AIuditor grants for a lease. Not an allocation."""

    connection_id: uuid.UUID
    device_id: uuid.UUID
    status: ConnectionStatus
    lease_id: uuid.UUID | None = None


class AllowedSet(BaseModel):
    """Every lease host this tenant may use. allocate still picks one."""

    hosts: list[AllowedHost]


class ConnectionAssign(BaseModel):
    """Bind a purpose=lease host row to an existing Hypermesh renter lease."""

    lease_id: uuid.UUID


class ConnectionTicket(BaseModel):
    """Mint a purpose=ir ticket for a purpose=auditor host row."""

    reserved_hours: int = Field(default=24, ge=1, le=24 * 30)


class ConnectionClassify(BaseModel):
    """AIuditor-owned provider hop. Prompt is not persisted or evented."""

    messages: list[dict[str, object]] = Field(default_factory=list)
    model: str | None = Field(default=None, max_length=128)


class AuditorGrant(BaseModel):
    """Door pull key. The HMAC ticket blob stays on Hypermesh / hm_gw_."""

    connection_id: uuid.UUID
    lease_id: uuid.UUID
    purpose: str
    device_id: uuid.UUID
    exp: datetime | None = None


class ApprovalWhy(StrEnum):
    COMPLETION = "completion"
    TOOL = "tool"
    LEASE = "lease"


class ApprovalStatus(StrEnum):
    HELD = "held"
    ALLOWED = "allowed"
    DENIED = "denied"
    TIMED_OUT = "timed_out"


class ApprovalDecision(StrEnum):
    ALLOW_ONCE = "allow_once"
    ALLOW_SESSION = "allow_session"
    DENY = "deny"


class ApprovalCreate(BaseModel):
    """Held AI-use ask. No prompt body."""

    lease_id: uuid.UUID | None = None
    connection_id: uuid.UUID | None = None
    purpose: str = Field(default="renter", max_length=32)
    who: str = Field(..., min_length=1, max_length=256)
    why: ApprovalWhy = ApprovalWhy.COMPLETION
    prompt_class: str | None = Field(default=None, max_length=64)
    ttl_seconds: int = Field(default=900, ge=30, le=24 * 60 * 60)


class ApprovalResolve(BaseModel):
    """Named human resolve. LLM is never resolved_by."""

    decision: ApprovalDecision


class Approval(BaseModel):
    """Operator inbox row. Distinct from fyber.auditor and privilege_grant."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    lease_id: uuid.UUID | None = None
    connection_id: uuid.UUID | None = None
    purpose: str
    who: str
    why: ApprovalWhy
    prompt_class: str | None = None
    status: ApprovalStatus
    decision: ApprovalDecision | None = None
    resolved_by: str | None = None
    created_at: datetime
    expires_at: datetime
    resolved_at: datetime | None = None
    consumed_at: datetime | None = None

    def to_event_payload(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "lease_id": str(self.lease_id) if self.lease_id else None,
            "connection_id": str(self.connection_id) if self.connection_id else None,
            "purpose": self.purpose,
            "who": self.who,
            "why": self.why.value,
            "prompt_class": self.prompt_class,
            "status": self.status.value,
            "decision": self.decision.value if self.decision else None,
            "resolved_by": self.resolved_by,
            "consumed_at": (
                self.consumed_at.isoformat() if self.consumed_at else None
            ),
        }


class PolicyDisposition(StrEnum):
    ALLOW = "allow"
    HOLD = "hold"
    DENY = "deny"


class ApprovalPolicyCreate(BaseModel):
    """Org allow/hold/deny. No prompt body. Default renter chat is unclassified."""

    purpose: str = Field(..., min_length=1, max_length=32)
    prompt_class: str | None = Field(default=None, max_length=64)
    connection_id: uuid.UUID | None = None
    disposition: PolicyDisposition
    routing_policy: RoutingPolicy | None = None


class ApprovalPolicy(BaseModel):
    """Most specific match wins. Distinct from fyber.auditor."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    purpose: str
    prompt_class: str | None = None
    connection_id: uuid.UUID | None = None
    disposition: PolicyDisposition
    routing_policy: RoutingPolicy | None = None
    created_at: datetime

    def to_event_payload(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "purpose": self.purpose,
            "prompt_class": self.prompt_class,
            "connection_id": str(self.connection_id) if self.connection_id else None,
            "disposition": self.disposition.value,
        }


class ContractStatus(StrEnum):
    DRAFT = "draft"
    SIGNED = "signed"
    SUSPENDED = "suspended"


class ViolationKind(StrEnum):
    TERM = "term"
    CERT_FAIL = "cert_fail"
    POLICY_DENY = "policy_deny"
    ABUSE = "abuse"


class ViolationAction(StrEnum):
    SUSPEND = "suspend"
    STOP = "stop"
    REFUND = "refund"


class ContractViolationCreate(BaseModel):
    """File a violation. Dispute reads cert + audit, not an on-chain court."""

    kind: ViolationKind
    action: ViolationAction | None = None
    reason: str | None = Field(default=None, max_length=120)


class ContractViolation(BaseModel):
    enforcement_completed_at: datetime | None = None
    enforcement_attempts: int = 0

    """Attached to a signed Contract. Not a handoff package."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    contract_id: uuid.UUID
    lease_id: uuid.UUID | None = None
    kind: ViolationKind
    action: ViolationAction
    reason: str | None = None
    created_at: datetime

    def to_event_payload(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "contract_id": str(self.contract_id),
            "lease_id": str(self.lease_id) if self.lease_id else None,
            "kind": self.kind.value,
            "action": self.action.value,
            "reason": self.reason,
        }


class ContractParty(BaseModel):
    """Who binds. Not a prompt body."""

    role: str = Field(..., min_length=1, max_length=32)
    subject: str = Field(..., min_length=1, max_length=256)


class ContractCreate(BaseModel):
    """Spine §D fields only. 1.x rail is Stripe. No prompt body."""

    parties: list[ContractParty] = Field(..., min_length=2, max_length=8)
    meters: list[str] = Field(..., min_length=1, max_length=8)
    rail: str = Field(default="stripe", max_length=32)
    until: datetime
    dispute_tip: str | None = Field(default=None, max_length=120)
    connection_id: uuid.UUID | None = None
    lease_id: uuid.UUID | None = None


class ContractBind(BaseModel):
    """Cite a Hypermesh lease. Does not mint a ticket."""

    lease_id: uuid.UUID


class ContractFulfill(BaseModel):
    """Auto-sign on Checkout. Internal only. Not a named-human sign path."""

    lease_id: uuid.UUID
    contract_id: uuid.UUID | None = None
    reserved_hours: int = Field(default=1, ge=1, le=24 * 30)
    renter_subject: str = Field(..., min_length=1, max_length=256)
    host_subject: str = Field(..., min_length=1, max_length=256)
    meters: list[str] = Field(
        default_factory=lambda: ["wall_time", "capacity_block"],
        min_length=1,
        max_length=8,
    )
    dispute_tip: str | None = Field(default=None, max_length=120)
    connection_id: uuid.UUID | None = None


class Contract(BaseModel):
    """Portable grant object. Not a handoff package. Not fyber.auditor."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    parties: list[ContractParty]
    meters: list[str]
    rail: str
    until: datetime
    dispute_tip: str | None = None
    connection_id: uuid.UUID | None = None
    lease_id: uuid.UUID | None = None
    status: ContractStatus
    signed_by: str | None = None
    created_at: datetime
    signed_at: datetime | None = None

    def to_event_payload(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "parties": [party.model_dump() for party in self.parties],
            "meters": list(self.meters),
            "rail": self.rail,
            "until": self.until.isoformat(),
            "dispute_tip": self.dispute_tip,
            "connection_id": str(self.connection_id) if self.connection_id else None,
            "lease_id": str(self.lease_id) if self.lease_id else None,
            "status": self.status.value,
            "signed_by": self.signed_by,
        }


class GrantExport(BaseModel):
    """Operator artifact. Derived copy, not the grant SoT. No prompt bodies."""

    tenant_id: uuid.UUID
    exported_at: datetime
    contracts: list[dict[str, object]]
    violations: list[dict[str, object]]
    approvals: list[dict[str, object]]


class AiuditorMeta(BaseModel):
    """Deployment role a sibling or Hypermesh can check before honoring a grant."""

    role: str
    writer: str
    parent: bool = False


class HydrateSnapshot(BaseModel):
    """Witness pull from a parent AIuditor. Not a handoff package. No tickets."""

    tenant_id: uuid.UUID
    role: str
    writer: str
    exported_at: datetime
    connections: list[dict[str, object]]
    policies: list[dict[str, object]]
    contracts: list[dict[str, object]]
    violations: list[dict[str, object]]
    approvals: list[dict[str, object]]
    catalog: list[dict[str, object]]
    envelopes: list[dict[str, object]] = Field(default_factory=list)
    suite_members: list[dict[str, object]] = Field(default_factory=list)
    admissions: list[dict[str, object]] = Field(default_factory=list)


class ClassEnvelopeCreate(BaseModel):
    """Lab-soak measurement for one artifact on one hardware class.

    Written from a lab host of that class. Hypermesh still stamps the catalog
    row; this is the program envelope a witness hydrates.
    """

    class_id: str = Field(..., min_length=1, max_length=64)
    catalog_id: str = Field(..., min_length=1, max_length=64)
    file_hash: str = Field(..., min_length=1, max_length=256)
    quant: str | None = Field(default=None, max_length=32)
    runtime: str = Field(..., min_length=1, max_length=64)
    context: int | None = Field(default=None, ge=0, le=1_000_000)
    fit: bool = True
    decode_tok_s: float | None = Field(default=None, ge=0)
    thermal_c: float | None = None


class ClassEnvelope(BaseModel):
    """One row per artifact per class_id. Not a Hypermesh scorecard."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    class_id: str
    catalog_id: str
    file_hash: str
    quant: str | None = None
    runtime: str
    context: int | None = None
    fit: bool
    decode_tok_s: float | None = None
    thermal_c: float | None = None
    created_at: datetime
    updated_at: datetime

    lab_job_id: uuid.UUID | None = None
    lab_device_id: uuid.UUID | None = None
    lab_origin: str | None = None
    binding_sha256: str | None = None
    report_sha256: str | None = None
    measured_at: datetime | None = None

    @computed_field
    @property
    def measurement_verified(self) -> bool:
        return all((self.lab_job_id, self.lab_device_id, self.lab_origin,
                    self.binding_sha256, self.report_sha256, self.measured_at))

    @computed_field
    @property
    def revision(self) -> str:
        if self.report_sha256:
            return self.report_sha256
        import hashlib, json
        data = self.model_dump(mode="json", exclude={"revision", "measurement_verified"})
        return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def to_event_payload(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "class_id": self.class_id,
            "catalog_id": self.catalog_id,
            "file_hash": self.file_hash,
            "quant": self.quant,
            "runtime": self.runtime,
            "context": self.context,
            "fit": self.fit,
            "decode_tok_s": self.decode_tok_s,
            "thermal_c": self.thermal_c,
        }


class SuiteMemberCreate(BaseModel):
    """Catalog/pack id that forms the compact host cert for a class."""

    class_id: str = Field(..., min_length=1, max_length=64)
    catalog_id: str = Field(..., min_length=1, max_length=64)
    pack_id: str = Field(default="pack-v1", min_length=1, max_length=64)


class SuiteMemberReplace(SuiteMemberCreate):
    """Compare-and-set program configuration; never measurement authority."""
    model_config = {"extra": "forbid", "str_strip_whitespace": True}
    expected_catalog_id: str = Field(..., min_length=1, max_length=64)
    expected_pack_id: str = Field(..., min_length=1, max_length=64)


class SuiteMember(BaseModel):
    """Core cert suite membership. Compact-cert jobs read this list."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    class_id: str
    catalog_id: str
    pack_id: str
    created_at: datetime

    def to_event_payload(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "class_id": self.class_id,
            "catalog_id": self.catalog_id,
            "pack_id": self.pack_id,
        }


class AdmissionStatus(StrEnum):
    PENDING = "pending"
    QUARANTINED = "quarantined"
    SANDBOXING = "sandboxing"
    HOLD = "hold"
    ALLOWED = "allowed"
    DENIED = "denied"


class AdmissionVerdict(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class AdmissionCreate(BaseModel):
    """Uploaded-model submit. Credential is write-only; only the hash is stored.

    Static gate / quarantine / sandbox run on submit. Fail closed — never
    auto-allow. A named human (or approval inbox) still issues the verdict.
    """

    file_hash: str = Field(..., min_length=1, max_length=256)
    format: str = Field(..., min_length=1, max_length=32)
    quant: str | None = Field(default=None, max_length=32)
    license: str = Field(..., min_length=1, max_length=64)
    intended_class: str = Field(..., min_length=1, max_length=64)
    credential: str | None = Field(default=None, min_length=1, max_length=4096)
    size_bytes: int | None = Field(default=None, ge=0)
    source_ref: str | None = Field(
        default=None,
        max_length=512,
        description="Trusted fetch pin for lab quarantine pull (repo@rev/file).",
    )
    extra_files: list[str] | None = Field(
        default=None,
        description="Any unexpected files beside the weights. Non-empty → deny.",
    )
    hold_for_human: bool = Field(
        default=True,
        description="First tester uploads go to the approval inbox. LLM never resolves.",
    )


class AdmissionQuarantineRetry(BaseModel):
    """Retry scheduling only; persisted artifact metadata cannot be replaced."""
    model_config = {"extra": "forbid"}
    source_ref: str | None = Field(default=None, max_length=512)


class AdmissionResolve(BaseModel):
    """Allow or deny an uploaded artifact. LLM is never the resolver."""

    verdict: AdmissionVerdict
    reason: str | None = Field(default=None, max_length=120)


class AdmissionSandboxReport(BaseModel):
    """Lab host soak + egress observation. Omitted egress_ok fails closed."""

    soak_passed: bool
    egress_ok: bool | None = Field(
        default=None,
        description="Explicit True required. Missing/None fails closed.",
    )
    egress_reason: str | None = Field(default=None, max_length=64)


class Admission(BaseModel):
    """Uploaded-model admission row. Deny is a policy record, not a lease."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    file_hash: str
    format: str
    quant: str | None = None
    license: str
    intended_class: str
    credential_ref: str | None = None
    status: AdmissionStatus
    verdict: AdmissionVerdict | None = None
    reason: str | None = None
    resolved_by: str | None = None
    size_bytes: int | None = None
    sandbox_soak_ok: bool | None = None
    sandbox_egress_ok: bool | None = None
    sandbox_egress_reason: str | None = None
    lab_assignment: dict | None = None
    sandbox_report_sha256: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None

    def to_event_payload(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "file_hash": self.file_hash,
            "format": self.format,
            "quant": self.quant,
            "license": self.license,
            "intended_class": self.intended_class,
            "credential_ref": self.credential_ref,
            "status": self.status.value,
            "verdict": self.verdict.value if self.verdict else None,
            "reason": self.reason,
            "resolved_by": self.resolved_by,
            "size_bytes": self.size_bytes,
            "sandbox_soak_ok": self.sandbox_soak_ok,
            "sandbox_egress_ok": self.sandbox_egress_ok,
            "sandbox_egress_reason": self.sandbox_egress_reason,
        }


class CrawlCandidateStatus(StrEnum):
    FIT = "fit"
    DROPPED = "dropped"
    QUEUED = "queued"
    SKIPPED = "skipped"


class CrawlCandidate(BaseModel):
    """HF / GGUF publisher metadata pin. Weights are never stored here."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    repo: str
    revision: str
    file_name: str
    size_bytes: int
    license: str | None = None
    quant: str | None = None
    file_hash: str | None = None
    publisher: str
    catalog_id: str
    class_id: str
    status: CrawlCandidateStatus
    soak_job_id: str | None = None
    created_at: datetime
    updated_at: datetime

    def to_event_payload(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "repo": self.repo,
            "revision": self.revision,
            "file_name": self.file_name,
            "size_bytes": self.size_bytes,
            "license": self.license,
            "quant": self.quant,
            "file_hash": self.file_hash,
            "publisher": self.publisher,
            "catalog_id": self.catalog_id,
            "class_id": self.class_id,
            "status": self.status.value,
            "soak_job_id": self.soak_job_id,
        }


class CrawlRunResult(BaseModel):
    crawled: int
    stored: int
    dropped_no_fit: int
    queued: int
    skipped_fresh: int
    errors: list[str] = Field(default_factory=list)


class ContractGrant(BaseModel):
    """Signed grant a sibling product may cite. Not a TURN/oracle/payment row."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    status: ContractStatus
    rail: str
    meters: list[str]
    until: datetime
    writer: str
    connection_id: uuid.UUID | None = None
    lease_id: uuid.UUID | None = None

    @classmethod
    def from_contract(cls, row: Contract, *, writer: str) -> Self:
        return cls(
            id=row.id,
            tenant_id=row.tenant_id,
            status=row.status,
            rail=row.rail,
            meters=list(row.meters),
            until=row.until,
            writer=writer,
            connection_id=row.connection_id,
            lease_id=row.lease_id,
        )


class ServiceBackend(StrEnum):
    """AIuditor catalog backend. Not a Hypermesh class or catalog row."""

    HYPERMESH_CLASS = "hypermesh_class"
    HYPERMESH_CATALOG = "hypermesh_catalog"
    PROVIDER = "provider"


class ServiceCreate(BaseModel):
    """Name a leasable backend. TURN / oracles / payments stay siblings."""

    title: str = Field(..., min_length=1, max_length=120)
    backend: ServiceBackend
    class_id: str | None = Field(default=None, max_length=64)
    catalog_id: str | None = Field(default=None, max_length=64)
    connection_id: uuid.UUID | None = None
    meters: list[str] = Field(
        default_factory=lambda: ["wall_time", "capacity_block"],
        min_length=1,
        max_length=8,
    )

    @model_validator(mode="after")
    def _one_backend(self) -> Self:
        if self.backend is ServiceBackend.HYPERMESH_CLASS:
            if not (self.class_id or "").strip():
                raise ValueError("hypermesh_class services require class_id")
            if self.catalog_id or self.connection_id:
                raise ValueError("hypermesh_class services take class_id only")
            self.class_id = self.class_id.strip()
            return self
        if self.backend is ServiceBackend.HYPERMESH_CATALOG:
            if not (self.catalog_id or "").strip():
                raise ValueError("hypermesh_catalog services require catalog_id")
            if self.class_id or self.connection_id:
                raise ValueError("hypermesh_catalog services take catalog_id only")
            self.catalog_id = self.catalog_id.strip()
            return self
        if self.connection_id is None:
            raise ValueError("provider services require connection_id")
        if self.class_id or self.catalog_id:
            raise ValueError("provider services take connection_id only")
        return self


class CatalogService(BaseModel):
    """Tenant-scoped leasable service. Classes stay on Hypermesh."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    title: str
    backend: ServiceBackend
    class_id: str | None = None
    catalog_id: str | None = None
    connection_id: uuid.UUID | None = None
    meters: list[str]
    created_at: datetime

    def to_event_payload(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "title": self.title,
            "backend": self.backend.value,
            "class_id": self.class_id,
            "catalog_id": self.catalog_id,
            "connection_id": str(self.connection_id) if self.connection_id else None,
            "meters": list(self.meters),
        }


class ServiceAllocate(BaseModel):
    """Pick a live connection and mint a Contract. HMAC stays on Hypermesh."""

    renter: str = Field(..., min_length=1, max_length=256)
    reserved_hours: int = Field(default=1, ge=1, le=24 * 30)


class ServiceAllocation(BaseModel):
    """Catalog grant. The provider hop has no ticket; a host grant mints one."""

    service_id: uuid.UUID
    contract: Contract
    connection_id: uuid.UUID
    backend: ServiceBackend
    lease_id: uuid.UUID | None = None
    purpose: str = "lease"
    exp: datetime | None = None
    # Set when this grant opened a new lease: the pending USDC payment the
    # renter settles before the lease starts. Pay it in USDC, or with
    # POST /api/v1/payments/usdc/lease-payments/{id}/test-credit where offered.
    billing_record_id: uuid.UUID | None = None
    payment_amount_atoms: int | None = None
    payment_test_credit_available: bool = False

    def to_event_payload(self) -> dict[str, object]:
        return {
            "service_id": str(self.service_id),
            "contract_id": str(self.contract.id),
            "connection_id": str(self.connection_id),
            "backend": self.backend.value,
            "lease_id": str(self.lease_id) if self.lease_id else None,
            "purpose": self.purpose,
        }
