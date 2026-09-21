"""Parent AIuditor hop. Witness pulls; it does not fulfill.

GET /hydrate on the parent is the snapshot. POST /hydrate/pull on the
witness writes it locally. Prompt bodies and HMAC tickets stay off the
wire. Handoff (spine D) is not this path.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import httpx
from fastapi import HTTPException

from aiuditor_service.domain.models import HydrateSnapshot
from aiuditor_service.export import _reject_bodies
from aiuditor_service.persist import row_from_payload
from aiuditor_service.role import AiuditorRole, writer_for
from aiuditor_service.store import StoreBundle

PARENT_REQUIRED = "AIUDITOR_PARENT_URL is required to pull hydrate"
PARENT_HOP_FAILED = "parent AIuditor hydrate failed"


class ParentUnavailable(Exception):
    """Parent URL unset or the hop failed. Witness pull fail-closes."""


class ParentHop(Protocol):
    async def hydrate(
        self,
        tenant_id: uuid.UUID,
        *,
        headers: dict[str, str] | None = None,
    ) -> HydrateSnapshot: ...


class ClosedParentHop:
    async def hydrate(
        self,
        tenant_id: uuid.UUID,
        *,
        headers: dict[str, str] | None = None,
    ) -> HydrateSnapshot:
        raise ParentUnavailable(PARENT_REQUIRED)


class HttpParentHop:
    def __init__(
        self,
        base_url: str,
        *,
        internal_token: str = "",
        timeout_s: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = internal_token
        self._timeout = timeout_s
        self._transport = transport

    async def hydrate(
        self,
        tenant_id: uuid.UUID,
        *,
        headers: dict[str, str] | None = None,
    ) -> HydrateSnapshot:
        hop = {**(headers or {}), "X-Tenant-ID": str(tenant_id)}
        hop["X-Tenant-ID"] = str(tenant_id)
        if self._token:
            hop["X-Internal-Token"] = self._token
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.get(
                    f"{self._base}/api/v1/aiuditor/hydrate",
                    headers=hop,
                )
        except httpx.HTTPError as exc:
            raise ParentUnavailable(PARENT_HOP_FAILED) from exc
        if response.status_code >= 400:
            raise ParentUnavailable(PARENT_HOP_FAILED)
        try:
            return HydrateSnapshot.model_validate(response.json())
        except (ValueError, TypeError) as exc:
            raise ParentUnavailable(PARENT_HOP_FAILED) from exc


@dataclass
class FakeParentHop:
    snapshot: HydrateSnapshot | None = None
    calls: list[uuid.UUID] = field(default_factory=list)
    error: str | None = None

    async def hydrate(
        self,
        tenant_id: uuid.UUID,
        *,
        headers: dict[str, str] | None = None,
    ) -> HydrateSnapshot:
        del headers
        self.calls.append(tenant_id)
        if self.error:
            raise ParentUnavailable(self.error)
        if self.snapshot is None or self.snapshot.tenant_id != tenant_id:
            raise ParentUnavailable(PARENT_HOP_FAILED)
        return self.snapshot


def resolve_parent_hop(
    hop: ParentHop | None = None,
    *,
    base_url: str | None = None,
) -> ParentHop:
    if hop is not None:
        return hop
    raw = os.environ.get("AIUDITOR_PARENT_URL") if base_url is None else base_url
    text = (raw or "").strip()
    if not text:
        return ClosedParentHop()
    token = (os.environ.get("AIUDITOR_PARENT_TOKEN") or os.environ.get("AIUDITOR_INTERNAL_TOKEN") or "").strip()
    return HttpParentHop(text, internal_token=token)


def _now(clock: datetime | None) -> datetime:
    stamp = clock if clock is not None else datetime.now(UTC)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp


def _row_dump(row) -> dict[str, object]:
    data = row.model_dump(mode="json")
    data.pop("credential", None)
    return data


def build_hydrate(
    *,
    tenant_id: uuid.UUID,
    role: AiuditorRole,
    stores: StoreBundle,
    now: datetime | None = None,
) -> HydrateSnapshot:
    payload = HydrateSnapshot(
        tenant_id=tenant_id,
        role=role.value,
        writer=writer_for(role),
        exported_at=_now(now),
        connections=[
            _row_dump(row) for row in stores.connection_store.list_for(tenant_id)
        ],
        policies=[_row_dump(row) for row in stores.policy_store.list_for(tenant_id)],
        contracts=[_row_dump(row) for row in stores.contract_store.list_for(tenant_id)],
        violations=[_row_dump(row) for row in stores.violation_store.list_for(tenant_id)],
        approvals=[_row_dump(row) for row in stores.approval_store.list_for(tenant_id)],
        catalog=[_row_dump(row) for row in stores.catalog_store.list_for(tenant_id)],
    )
    _reject_bodies(payload.model_dump(mode="json"))
    return payload


def apply_hydrate(stores: StoreBundle, snapshot: HydrateSnapshot) -> HydrateSnapshot:
    _reject_bodies(snapshot.model_dump(mode="json"))
    if snapshot.tenant_id is None:
        raise HTTPException(status_code=400, detail=PARENT_HOP_FAILED)
    mapping = (
        ("connections", snapshot.connections, stores.connection_store),
        ("policies", snapshot.policies, stores.policy_store),
        ("contracts", snapshot.contracts, stores.contract_store),
        ("violations", snapshot.violations, stores.violation_store),
        ("approvals", snapshot.approvals, stores.approval_store),
        ("catalog", snapshot.catalog, stores.catalog_store),
    )
    for table, rows, store in mapping:
        for payload in rows:
            if not isinstance(payload, dict):
                raise HTTPException(status_code=400, detail=PARENT_HOP_FAILED)
            store.put(row_from_payload(table, payload))
    return snapshot
