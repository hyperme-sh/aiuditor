"""Daily crawl fit-filter, re-soak rules, and admission pipeline."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from aiuditor_service.admission import (
    EGRESS_DETECTED,
    LICENSE_DENIED,
    UNEXPECTED_FILES,
    advance_admission,
    resolve_admission,
    submit_admission,
)
from aiuditor_service.crawl import ArtifactMeta, StaticMetadataSource
from aiuditor_service.domain.models import (
    AdmissionCreate,
    AdmissionResolve,
    AdmissionStatus,
    AdmissionVerdict,
    ClassEnvelopeCreate,
)
from aiuditor_service.fit import classes_that_fit, fits_class
from aiuditor_service.hypermesh import FakePlaneHop
from aiuditor_service.intake import needs_resoaking, run_intake
from aiuditor_service.program import put_envelope
from aiuditor_service.store import AdmissionStore, CandidateStore, EnvelopeStore

GIB = 1024**3


def test_fit_filter_drops_70b_on_nx_keeps_on_agx():
    # ~40GB Q4-ish file
    size = int(40 * GIB)
    assert fits_class("nx-volume", size) is not None
    assert fits_class("nx-volume", size).fits is False
    assert fits_class("agx-large", size).fits is True
    assert "agx-large" in classes_that_fit(size)
    assert "nx-volume" not in classes_that_fit(size)


def test_identical_hash_within_retention_skips_resoaking():
    store = EnvelopeStore()
    tenant = uuid.uuid4()
    env = put_envelope(
        store,
        tenant,
        ClassEnvelopeCreate(
            class_id="agx-large",
            catalog_id="hf-x",
            file_hash="sha256:" + ("ab" * 32),
            quant="Q4_K_M",
            runtime="nvidia",
        ),
        now=datetime.now(UTC),
    )
    assert (
        needs_resoaking(
            env,
            file_hash=env.file_hash,
            quant=env.quant,
            runtime="nvidia",
            now=datetime.now(UTC),
            retention=timedelta(days=30),
        )
        is False
    )
    assert (
        needs_resoaking(
            env,
            file_hash="sha256:" + ("cd" * 32),
            quant=env.quant,
            runtime="nvidia",
            now=datetime.now(UTC),
        )
        is True
    )


@pytest.mark.asyncio
async def test_intake_queues_fit_artifacts_and_drops_too_large():
    tenant = uuid.uuid4()
    hop = FakePlaneHop()
    source = StaticMetadataSource(
        [
            ArtifactMeta(
                repo="bartowski/Llama-3.1-8B-Instruct-GGUF",
                revision="main",
                file_name="Llama-3.1-8B-Instruct-Q4_K_M.gguf",
                size_bytes=int(4.5 * GIB),
                license="llama3",
                quant="Q4_K_M",
                file_hash="sha256:" + ("11" * 32),
                publisher="bartowski",
            ),
            ArtifactMeta(
                repo="bartowski/Huge-400B-GGUF",
                revision="main",
                file_name="Huge-400B-Q4.gguf",
                size_bytes=int(200 * GIB),
                license="apache-2.0",
                quant="Q4",
                file_hash="sha256:" + ("22" * 32),
                publisher="bartowski",
            ),
        ]
    )
    report = await run_intake(
        tenant_id=tenant,
        source=source,
        candidates=CandidateStore(),
        envelopes=EnvelopeStore(),
        lab_hop=hop,
        class_ids=("agx-large", "nx-volume"),
    )
    assert report.crawled == 2
    assert report.dropped_no_fit == 1
    assert report.queued >= 1
    assert hop.soaks
    assert all(s["class_id"] in {"agx-large", "nx-volume"} for s in hop.soaks)


def test_admission_static_gate_denies_bad_license_and_extra_files():
    store = AdmissionStore()
    tenant = uuid.uuid4()
    denied = submit_admission(
        store,
        tenant,
        AdmissionCreate(
            file_hash="sha256:" + ("aa" * 32),
            format="gguf",
            license="proprietary-secret",
            intended_class="agx-large",
        ),
    )
    assert denied.status is AdmissionStatus.DENIED
    assert denied.reason == LICENSE_DENIED

    denied2 = submit_admission(
        store,
        tenant,
        AdmissionCreate(
            file_hash="sha256:" + ("bb" * 32),
            format="gguf",
            license="llama3",
            intended_class="agx-large",
            extra_files=["surprise.bin"],
        ),
    )
    assert denied2.status is AdmissionStatus.DENIED
    assert denied2.reason == UNEXPECTED_FILES


def test_admission_sandbox_egress_fail_closed_and_llm_cannot_resolve():
    store = AdmissionStore()
    tenant = uuid.uuid4()
    row = submit_admission(
        store,
        tenant,
        AdmissionCreate(
            file_hash="sha256:" + ("cc" * 32),
            format="gguf",
            license="mit",
            intended_class="agx-large",
            size_bytes=1024,
        ),
        run_pipeline=False,
    )
    denied = advance_admission(
        store,
        tenant,
        row.id,
        sandbox_egress_ok=False,
    )
    assert denied.status is AdmissionStatus.DENIED
    assert denied.reason == EGRESS_DETECTED

    pending = submit_admission(
        store,
        tenant,
        AdmissionCreate(
            file_hash="sha256:" + ("dd" * 32),
            format="gguf",
            license="mit",
            intended_class="agx-large",
            size_bytes=1024,
        ),
    )
    assert pending.status is AdmissionStatus.PENDING
    with pytest.raises(HTTPException) as exc:
        resolve_admission(
            store,
            tenant,
            pending.id,
            AdmissionResolve(verdict=AdmissionVerdict.ALLOW),
            resolved_by="llm",
        )
    assert exc.value.status_code == 400


def test_admission_hold_for_human_before_verdict():
    store = AdmissionStore()
    tenant = uuid.uuid4()
    row = submit_admission(
        store,
        tenant,
        AdmissionCreate(
            file_hash="sha256:" + ("ee" * 32),
            format="gguf",
            license="apache-2.0",
            intended_class="agx-large",
        ),
        run_pipeline=False,
    )
    held = advance_admission(store, tenant, row.id, hold_for_human=True)
    assert held.status is AdmissionStatus.HOLD
    allowed = resolve_admission(
        store,
        tenant,
        held.id,
        AdmissionResolve(verdict=AdmissionVerdict.ALLOW, reason="lab green"),
        resolved_by="11111111-1111-1111-1111-111111111111",
    )
    assert allowed.status is AdmissionStatus.ALLOWED
    assert allowed.resolved_by.startswith("1111")
