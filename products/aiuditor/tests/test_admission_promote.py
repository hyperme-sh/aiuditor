"""Admission allow promotes a class-scoped catalog row; deny does not."""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from aiuditor_service.admission import resolve_admission, resolve_admission_async, submit_admission
from aiuditor_service.admission_catalog import admission_catalog_id
from aiuditor_service.domain.models import (
    AdmissionCreate,
    AdmissionResolve,
    AdmissionStatus,
    AdmissionVerdict,
)
from aiuditor_service.hypermesh import FakePlaneHop
from aiuditor_service.store import AdmissionStore


def test_admission_catalog_id_is_class_scoped():
    key = admission_catalog_id(
        intended_class="agx-large",
        file_hash="sha256:" + ("ab" * 32),
    )
    assert key.startswith("upload-agx-large-")
    assert len(key) <= 64


@pytest.mark.asyncio
async def test_allow_promotes_catalog_deny_does_not():
    store = AdmissionStore()
    tenant = uuid.uuid4()
    hop = FakePlaneHop()
    file_hash = "sha256:" + ("11" * 32)
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
    )
    allowed = await resolve_admission_async(
        store,
        tenant,
        row.id,
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

    denied_row = submit_admission(
        store,
        tenant,
        AdmissionCreate(
            file_hash="sha256:" + ("22" * 32),
            format="gguf",
            license="mit",
            intended_class="agx-large",
            size_bytes=1024,
        ),
    )
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
    row = submit_admission(
        store,
        tenant,
        AdmissionCreate(
            file_hash="sha256:" + ("33" * 32),
            format="gguf",
            license="mit",
            intended_class="agx-large",
            size_bytes=1024,
        ),
    )
    with pytest.raises(HTTPException) as exc:
        resolve_admission(
            store,
            tenant,
            row.id,
            AdmissionResolve(verdict=AdmissionVerdict.ALLOW),
            resolved_by="llm",
        )
    assert exc.value.status_code == 400
