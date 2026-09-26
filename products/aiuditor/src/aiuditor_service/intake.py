"""Daily metadata intake: crawl → fit-filter → queue lab soaks when needed.

Control-plane job. Does not download weights. Identical hashes are not re-soaked.
Re-soak when file hash, quant, or runtime pin changes, or the envelope is older
than the retention window.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from aiuditor_service.crawl import ArtifactMeta, MetadataSource
from aiuditor_service.domain.models import (
    ClassEnvelope,
    CrawlCandidate,
    CrawlCandidateStatus,
)
from aiuditor_service.fit import classes_that_fit
from aiuditor_service.store import CandidateStore, EnvelopeStore

DEFAULT_RUNTIME = "nvidia"
DEFAULT_RETENTION = timedelta(days=30)


class LabSoakHop(Protocol):
    async def queue_lab_soak(
        self,
        tenant_id: uuid.UUID,
        *,
        class_id: str,
        catalog_id: str,
        artifact_name: str | None = None,
        file_hash: str | None = None,
        quant: str | None = None,
        runtime: str | None = None,
        source_ref: str | None = None,
        enqueue_pull: bool = True,
        size_bytes: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]: ...


@dataclass(frozen=True)
class IntakeReport:
    crawled: int
    stored: int
    dropped_no_fit: int
    queued: int
    skipped_fresh: int
    errors: tuple[str, ...] = ()


def _error_text(exc: BaseException) -> str:
    """Operator-facing failure. HTTP status stays; headers and tokens do not."""

    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        detail = getattr(exc, "detail", None)
        text = detail if detail is not None else exc
        return f"HTTP {status}: {text}"
    return str(exc)


def _now(clock: datetime | None) -> datetime:
    stamp = clock if clock is not None else datetime.now(UTC)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp


def needs_resoaking(
    envelope: ClassEnvelope | None,
    *,
    file_hash: str | None,
    quant: str | None,
    runtime: str,
    now: datetime,
    retention: timedelta = DEFAULT_RETENTION,
) -> bool:
    """Identical hash/quant/runtime within retention → do not re-run."""

    if envelope is None:
        return True
    if (file_hash or "").strip() and envelope.file_hash != (file_hash or "").strip():
        return True
    if (quant or None) != (envelope.quant or None):
        return True
    if (runtime or "").strip() != (envelope.runtime or "").strip():
        return True
    updated = envelope.updated_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return updated < (now - retention)


def upsert_candidate(
    store: CandidateStore,
    tenant_id: uuid.UUID,
    meta: ArtifactMeta,
    *,
    class_id: str,
    now: datetime | None = None,
) -> CrawlCandidate:
    clock = _now(now)
    existing = store.for_source(
        tenant_id, meta.repo, meta.revision, meta.file_name, class_id
    )
    status = CrawlCandidateStatus.FIT
    if existing is not None:
        row = existing.model_copy(
            update={
                "size_bytes": meta.size_bytes,
                "license": meta.license,
                "quant": meta.quant,
                "file_hash": meta.file_hash,
                "catalog_id": meta.catalog_id,
                "status": status,
                "updated_at": clock,
            }
        )
    else:
        row = CrawlCandidate(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            repo=meta.repo[:256],
            revision=meta.revision[:128],
            file_name=meta.file_name[:256],
            size_bytes=meta.size_bytes,
            license=meta.license,
            quant=meta.quant,
            file_hash=meta.file_hash,
            publisher=meta.publisher[:64],
            catalog_id=meta.catalog_id,
            class_id=class_id,
            status=status,
            soak_job_id=None,
            created_at=clock,
            updated_at=clock,
        )
    store.put(row)
    return row


async def _consume_artifact(
    *,
    tenant_id: uuid.UUID,
    meta: ArtifactMeta,
    candidates: CandidateStore,
    envelopes: EnvelopeStore,
    lab_hop: LabSoakHop | None,
    soak_classes: tuple[str, ...],
    runtime: str,
    retention: timedelta,
    clock: datetime,
):
    """Yield counter events for one artifact. Puts stay on the caller's transaction."""

    fits = classes_that_fit(meta.size_bytes, class_ids=soak_classes)
    if not fits:
        yield "dropped", None
        drop_class = soak_classes[0]
        existing = candidates.for_source(
            tenant_id, meta.repo, meta.revision, meta.file_name, drop_class
        )
        if existing is None:
            candidates.put(
                CrawlCandidate(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    repo=meta.repo[:256],
                    revision=meta.revision[:128],
                    file_name=meta.file_name[:256],
                    size_bytes=meta.size_bytes,
                    license=meta.license,
                    quant=meta.quant,
                    file_hash=meta.file_hash,
                    publisher=meta.publisher[:64],
                    catalog_id=meta.catalog_id,
                    class_id=drop_class,
                    status=CrawlCandidateStatus.DROPPED,
                    soak_job_id=None,
                    created_at=clock,
                    updated_at=clock,
                )
            )
            yield "stored", None
        else:
            candidates.put(
                existing.model_copy(
                    update={
                        "status": CrawlCandidateStatus.DROPPED,
                        "size_bytes": meta.size_bytes,
                        "updated_at": clock,
                    }
                )
            )
        return

    for class_id in fits:
        row = upsert_candidate(
            candidates, tenant_id, meta, class_id=class_id, now=clock
        )
        yield "stored", None
        envelope = envelopes.for_class_catalog(tenant_id, class_id, row.catalog_id)
        if not needs_resoaking(
            envelope,
            file_hash=row.file_hash,
            quant=row.quant,
            runtime=runtime,
            now=clock,
            retention=retention,
        ):
            yield "skipped", None
            candidates.put(
                row.model_copy(
                    update={
                        "status": CrawlCandidateStatus.SKIPPED,
                        "updated_at": clock,
                    }
                )
            )
            continue
        if lab_hop is None:
            yield "error", f"no lab hop for {row.catalog_id}/{class_id}"
            continue
        try:
            queued_body = await lab_hop.queue_lab_soak(
                tenant_id,
                class_id=class_id,
                catalog_id=row.catalog_id,
                artifact_name=meta.file_name,
                file_hash=row.file_hash,
                quant=row.quant,
                runtime=runtime,
                source_ref=meta.source_ref,
                enqueue_pull=True,
                size_bytes=meta.size_bytes,
            )
        except Exception as exc:  # noqa: BLE001 — this class fails; other classes continue
            yield "error", f"{row.catalog_id}/{class_id}: {_error_text(exc)}"
            continue
        soak_id = queued_body.get("soak_job_id")
        candidates.put(
            row.model_copy(
                update={
                    "status": CrawlCandidateStatus.QUEUED,
                    "soak_job_id": str(soak_id) if soak_id else None,
                    "updated_at": clock,
                }
            )
        )
        yield "queued", None


async def run_intake(
    *,
    tenant_id: uuid.UUID,
    source: MetadataSource,
    candidates: CandidateStore,
    envelopes: EnvelopeStore,
    lab_hop: LabSoakHop | None,
    class_ids: tuple[str, ...] | None = None,
    runtime: str = DEFAULT_RUNTIME,
    retention: timedelta = DEFAULT_RETENTION,
    now: datetime | None = None,
) -> IntakeReport:
    clock = _now(now)
    artifacts = await source.list_artifacts()
    stored = 0
    dropped = 0
    queued = 0
    skipped = 0
    errors: list[str] = []
    soak_classes = class_ids or ("agx-large", "nx-volume")

    for meta in artifacts:
        try:
            with candidates.persist.candidate_scope():
                _intake_one = _consume_artifact(
                    tenant_id=tenant_id,
                    meta=meta,
                    candidates=candidates,
                    envelopes=envelopes,
                    lab_hop=lab_hop,
                    soak_classes=soak_classes,
                    runtime=runtime,
                    retention=retention,
                    clock=clock,
                )
                async for kind, payload in _intake_one:
                    if kind == "dropped":
                        dropped += 1
                    elif kind == "stored":
                        stored += 1
                    elif kind == "skipped":
                        skipped += 1
                    elif kind == "queued":
                        queued += 1
                    elif kind == "error":
                        errors.append(str(payload))
        except Exception as exc:  # noqa: BLE001 — one candidate must not abort the crawl
            errors.append(f"{meta.source_ref}: {_error_text(exc)}")

    return IntakeReport(
        crawled=len(artifacts),
        stored=stored,
        dropped_no_fit=dropped,
        queued=queued,
        skipped_fresh=skipped,
        errors=tuple(errors),
    )


def resolve_crawl_every_s() -> float:
    raw = (os.environ.get("AIUDITOR_CRAWL_EVERY") or "").strip()
    if not raw:
        return 86400.0
    try:
        return max(60.0, float(raw))
    except ValueError:
        return 86400.0


# Same UUID as hypermesh_service.seed.MARKETPLACE_TENANT_ID. The test and
# local writers crawl that tenant. Do not invent another one.
MARKETPLACE_TENANT_ID = uuid.UUID("8f8e8d8c-7b6a-4a59-8c3d-2e1f0a9b8c7d")


def resolve_crawl_tenant() -> uuid.UUID | None:
    raw = (os.environ.get("AIUDITOR_CRAWL_TENANT_ID") or "").strip()
    if not raw:
        return MARKETPLACE_TENANT_ID
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None
