"""Admission allow requires a successful lab soak + explicit egress_ok."""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from aiuditor_service.admission import (
    EGRESS_DETECTED,
    EGRESS_UNOBSERVED,
    SOAK_FAILED,
    SOAK_REQUIRED,
    record_sandbox_result,
    resolve_admission,
    resolve_admission_async,
    submit_admission,
    submit_admission_async,
)
from aiuditor_service.admission_catalog import admission_catalog_id
from aiuditor_service.domain.models import (
    AdmissionCreate,
    AdmissionResolve,
    AdmissionSandboxReport,
    AdmissionStatus,
    AdmissionVerdict,
)
from aiuditor_service.hypermesh import FakePlaneHop
from aiuditor_service.store import AdmissionStore, ApprovalStore


def test_admission_catalog_id_is_class_scoped():
    key = admission_catalog_id(
        intended_class="agx-large",
        file_hash="sha256:" + ("ab" * 32),
    )
    assert key.startswith("upload-agx-large-")
    assert len(key) <= 64


def _passed_row(store: AdmissionStore, tenant: uuid.UUID, file_hash: str):
    row = submit_admission(
        store,
        tenant,
        AdmissionCreate(
            file_hash=file_hash,
            format="gguf",
            license="mit",
            intended_class="agx-large",
            size_bytes=1024,
        ),
        run_pipeline=False,
    )
    return record_sandbox_result(
        store,
        tenant,
        row.id,
        AdmissionSandboxReport(
            soak_passed=True,
            egress_ok=True,
            egress_reason="overlay_publish_only",
        ),
        hold_for_human=True,
    )


@pytest.mark.asyncio
async def test_allow_before_soak_is_rejected():
    store = AdmissionStore()
    tenant = uuid.uuid4()
    hop = FakePlaneHop()
    row = await submit_admission_async(
        store,
        tenant,
        AdmissionCreate(
            file_hash="sha256:" + ("11" * 32),
            format="gguf",
            license="mit",
            intended_class="agx-large",
            size_bytes=1024,
            source_ref="bartowski/Llama@main/model.gguf",
        ),
        plane=hop,
        approvals=ApprovalStore(),
    )
    assert row.status is AdmissionStatus.SANDBOXING
    assert hop.soaks
    assert hop.soaks[0]["admission_id"] == str(row.id)
    with pytest.raises(HTTPException) as exc:
        await resolve_admission_async(
            store,
            tenant,
            row.id,
            AdmissionResolve(verdict=AdmissionVerdict.ALLOW, reason="too early"),
            resolved_by="11111111-1111-1111-1111-111111111111",
            plane=hop,
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == SOAK_REQUIRED
    assert hop.promotions == []


@pytest.mark.asyncio
async def test_soak_fail_or_egress_not_ok_blocks_promote():
    store = AdmissionStore()
    tenant = uuid.uuid4()
    hop = FakePlaneHop()

    soak_fail = submit_admission(
        store,
        tenant,
        AdmissionCreate(
            file_hash="sha256:" + ("22" * 32),
            format="gguf",
            license="mit",
            intended_class="agx-large",
            size_bytes=1024,
        ),
        run_pipeline=False,
    )
    denied_soak = record_sandbox_result(
        store,
        tenant,
        soak_fail.id,
        AdmissionSandboxReport(soak_passed=False, egress_ok=True),
    )
    assert denied_soak.status is AdmissionStatus.DENIED
    assert denied_soak.reason == SOAK_FAILED

    egress_bad = submit_admission(
        store,
        tenant,
        AdmissionCreate(
            file_hash="sha256:" + ("33" * 32),
            format="gguf",
            license="mit",
            intended_class="agx-large",
            size_bytes=1024,
        ),
        run_pipeline=False,
    )
    denied_egress = record_sandbox_result(
        store,
        tenant,
        egress_bad.id,
        AdmissionSandboxReport(
            soak_passed=True, egress_ok=False, egress_reason="egress_unexpected"
        ),
    )
    assert denied_egress.status is AdmissionStatus.DENIED
    assert denied_egress.reason == EGRESS_DETECTED

    unobserved = submit_admission(
        store,
        tenant,
        AdmissionCreate(
            file_hash="sha256:" + ("44" * 32),
            format="gguf",
            license="mit",
            intended_class="agx-large",
            size_bytes=1024,
        ),
        run_pipeline=False,
    )
    denied_missing = record_sandbox_result(
        store,
        tenant,
        unobserved.id,
        AdmissionSandboxReport(soak_passed=True),  # egress_ok omitted
    )
    assert denied_missing.status is AdmissionStatus.DENIED
    assert denied_missing.reason == EGRESS_UNOBSERVED
    assert hop.promotions == []


@pytest.mark.asyncio
async def test_explicit_egress_ok_and_human_allow_promotes_uncertified():
    store = AdmissionStore()
    tenant = uuid.uuid4()
    hop = FakePlaneHop()
    held = _passed_row(store, tenant, "sha256:" + ("55" * 32))
    assert held.status is AdmissionStatus.HOLD
    assert held.sandbox_soak_ok is True
    assert held.sandbox_egress_ok is True

    allowed = await resolve_admission_async(
        store,
        tenant,
        held.id,
        AdmissionResolve(verdict=AdmissionVerdict.ALLOW, reason="lab green"),
        resolved_by="11111111-1111-1111-1111-111111111111",
        plane=hop,
    )
    assert allowed.status is AdmissionStatus.ALLOWED
    assert len(hop.promotions) == 1
    assert hop.promotions[0]["class_id"] == "agx-large"
    assert hop.promotions[0]["origin"] == "upload"
    assert hop.promotions[0]["certified"] is False
    assert hop.promotions[0]["admission_id"] == str(allowed.id)

    denied_row = _passed_row(store, tenant, "sha256:" + ("66" * 32))
    denied = await resolve_admission_async(
        store,
        tenant,
        denied_row.id,
        AdmissionResolve(verdict=AdmissionVerdict.DENY, reason="policy deny"),
        resolved_by="11111111-1111-1111-1111-111111111111",
        plane=hop,
    )
    assert denied.status is AdmissionStatus.DENIED
    assert denied.verdict is AdmissionVerdict.DENY
    assert len(hop.promotions) == 1  # deny never promotes


def test_llm_still_cannot_resolve_admission():
    store = AdmissionStore()
    tenant = uuid.uuid4()
    row = _passed_row(store, tenant, "sha256:" + ("77" * 32))
    with pytest.raises(HTTPException) as exc:
        resolve_admission(
            store,
            tenant,
            row.id,
            AdmissionResolve(verdict=AdmissionVerdict.ALLOW),
            resolved_by="llm",
        )
    assert exc.value.status_code == 400


class _IdempotentPromotionPlane(FakePlaneHop):
    """Owner's tenant/catalog upsert contract; attempts are not unique effects."""
    def __init__(self):
        super().__init__()
        self.attempts = []
        self.catalog_rows = {}
        self.fail_before = False
        self.lose_response = False

    async def promote_catalog(self, tenant_id, **body):
        key = (tenant_id, body['catalog_id'])
        self.attempts.append((tenant_id, body.copy()))
        if self.fail_before:
            from aiuditor_service.hypermesh import HostLookupUnavailable
            raise HostLookupUnavailable('upstream unavailable')
        existing = self.catalog_rows.get(key)
        if existing is None:
            existing = await super().promote_catalog(tenant_id, **body)
            self.catalog_rows[key] = existing
        else:
            assert existing['admission_id'] == str(body['admission_id'])
            assert existing['file_hash'] == body['file_hash']
        if self.lose_response:
            from aiuditor_service.hypermesh import HostLookupUnavailable
            raise HostLookupUnavailable('response lost after upsert')
        return existing


@pytest.mark.asyncio
@pytest.mark.parametrize('lost_response', [False, True])
async def test_promotion_recovers_after_restart_without_changing_verdict(lost_response):
    from aiuditor_service.persist import MemoryPersistSession
    tenant = uuid.uuid4()
    persist = MemoryPersistSession()
    store = AdmissionStore(persist=persist)
    row = _passed_row(store, tenant, 'sha256:' + '88' * 32)
    plane = _IdempotentPromotionPlane()
    plane.fail_before = not lost_response
    plane.lose_response = lost_response
    body = AdmissionResolve(verdict=AdmissionVerdict.ALLOW, reason='original lab verdict')
    with pytest.raises(HTTPException) as exc:
        await resolve_admission_async(store, tenant, row.id, body,
            resolved_by='original-auditor', plane=plane)
    assert exc.value.status_code == 502
    original = store.get(tenant, row.id)
    assert original.verdict is AdmissionVerdict.ALLOW
    plane.fail_before = plane.lose_response = False
    restarted = AdmissionStore(persist=persist)
    for _ in range(2):
        retried = await resolve_admission_async(restarted, tenant, row.id,
            AdmissionResolve(verdict=AdmissionVerdict.ALLOW),
            resolved_by='another-current-auditor', plane=plane)
        assert retried == original
    assert len(plane.catalog_rows) == len(plane.promotions) == 1
    assert len(plane.attempts) == 3
    assert {body['admission_id'] for _, body in plane.attempts} == {row.id}
    assert len({body['catalog_id'] for _, body in plane.attempts}) == 1
    assert {tid for tid, _ in plane.attempts} == {tenant}


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['deny', 'reason', 'llm', 'tenant', 'soak'])
async def test_promotion_retry_refuses_authority_changes(change):
    store = AdmissionStore()
    tenant = uuid.uuid4()
    row = _passed_row(store, tenant, 'sha256:' + '99' * 32)
    original = resolve_admission(store, tenant, row.id,
        AdmissionResolve(verdict=AdmissionVerdict.ALLOW, reason='original'), resolved_by='original-auditor')
    plane = _IdempotentPromotionPlane()
    body = AdmissionResolve(verdict=AdmissionVerdict.DENY if change == 'deny' else AdmissionVerdict.ALLOW,
                            reason='changed' if change == 'reason' else None)
    if change == 'soak':
        original = original.model_copy(update={'sandbox_egress_ok': False})
        store.put(original)
    with pytest.raises(HTTPException):
        await resolve_admission_async(store, uuid.uuid4() if change == 'tenant' else tenant,
            row.id, body, resolved_by='llm' if change == 'llm' else 'current-auditor', plane=plane)
    assert store.get(tenant, row.id) == original
    assert plane.attempts == []


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid_metadata', [
    {'status': AdmissionStatus.DENIED}, {'resolved_by': None},
    {'resolved_by': 'llm'}, {'resolved_at': None},
])
async def test_promotion_retry_refuses_incomplete_original_authority(invalid_metadata):
    store = AdmissionStore()
    tenant = uuid.uuid4()
    row = _passed_row(store, tenant, 'sha256:' + 'aa' * 32)
    row = resolve_admission(store, tenant, row.id, AdmissionResolve(verdict=AdmissionVerdict.ALLOW),
                            resolved_by='original-auditor')
    row = row.model_copy(update=invalid_metadata)
    store.put(row)
    plane = _IdempotentPromotionPlane()
    with pytest.raises(HTTPException) as failure:
        await resolve_admission_async(store, tenant, row.id,
            AdmissionResolve(verdict=AdmissionVerdict.ALLOW), resolved_by='current-auditor', plane=plane)
    assert failure.value.status_code == 409
    assert plane.attempts == []
    assert store.get(tenant, row.id) == row
