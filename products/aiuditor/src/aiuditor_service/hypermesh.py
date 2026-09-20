"""Look up an enrolled Hypermesh host. Devices stay on Hypermesh.

AIuditor stores a device_id assignment only. The standing WG peer and
device secret stay on Hypermesh / the door.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import Protocol

import httpx

HOST_LOOKUP_REQUIRED = "HYPERMESH_SERVICE_URL is required to assign a host"
HOST_LOOKUP_FAILED = "hypermesh host lookup failed"
HOST_ROLE = "host"


class HostLookupUnavailable(Exception):
    """Hypermesh is unset or the hop failed. Assignment fail-closes."""


@dataclass(frozen=True)
class HostView:
    """Lock fields only. No serial, cert, or device secret."""

    id: uuid.UUID
    enrolled: bool
    role: str
    tenant_id: uuid.UUID | None = None


class HostLookup(Protocol):
    async def get(
        self,
        tenant_id: uuid.UUID,
        device_id: uuid.UUID,
        *,
        headers: dict[str, str] | None = None,
    ) -> HostView | None: ...


class ClosedHostLookup:
    """Fail closed when the Hypermesh hop is not configured."""

    async def get(
        self,
        tenant_id: uuid.UUID,
        device_id: uuid.UUID,
        *,
        headers: dict[str, str] | None = None,
    ) -> HostView | None:
        raise HostLookupUnavailable(HOST_LOOKUP_REQUIRED)


class HttpHostLookup:
    """GET /api/v1/hypermesh/devices/{id} with the operator's bound JWT."""

    def __init__(self, base_url: str, *, timeout_s: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s

    async def get(
        self,
        tenant_id: uuid.UUID,
        device_id: uuid.UUID,
        *,
        headers: dict[str, str] | None = None,
    ) -> HostView | None:
        hop = {
            "X-Tenant-ID": str(tenant_id),
            **(headers or {}),
        }
        hop["X-Tenant-ID"] = str(tenant_id)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    f"{self._base}/api/v1/hypermesh/devices/{device_id}",
                    headers=hop,
                )
        except httpx.HTTPError as exc:
            raise HostLookupUnavailable(HOST_LOOKUP_FAILED) from exc
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise HostLookupUnavailable(HOST_LOOKUP_FAILED)
        try:
            body = response.json()
        except ValueError as exc:
            raise HostLookupUnavailable(HOST_LOOKUP_FAILED) from exc
        return _view_from_device(body, device_id=device_id)


def _view_from_device(body: object, *, device_id: uuid.UUID) -> HostView:
    if not isinstance(body, dict):
        raise HostLookupUnavailable(HOST_LOOKUP_FAILED)
    raw_id = body.get("id") or device_id
    raw_tenant = body.get("tenant_id")
    try:
        parsed_id = uuid.UUID(str(raw_id))
        parsed_tenant = uuid.UUID(str(raw_tenant)) if raw_tenant else None
    except (ValueError, AttributeError, TypeError) as exc:
        raise HostLookupUnavailable(HOST_LOOKUP_FAILED) from exc
    return HostView(
        id=parsed_id,
        enrolled=bool(body.get("enrolled")),
        role=str(body.get("role") or "").strip() or HOST_ROLE,
        tenant_id=parsed_tenant,
    )


@dataclass
class FakeHostLookup:
    """In-process host table for tests. Not a Hypermesh device store."""

    by_id: dict[uuid.UUID, HostView] = field(default_factory=dict)

    def enroll(
        self,
        device_id: uuid.UUID,
        *,
        tenant_id: uuid.UUID | None = None,
        enrolled: bool = True,
        role: str = HOST_ROLE,
    ) -> HostView:
        view = HostView(
            id=device_id,
            enrolled=enrolled,
            role=role,
            tenant_id=tenant_id,
        )
        self.by_id[device_id] = view
        return view

    async def get(
        self,
        tenant_id: uuid.UUID,
        device_id: uuid.UUID,
        *,
        headers: dict[str, str] | None = None,
    ) -> HostView | None:
        view = self.by_id.get(device_id)
        if view is None:
            return None
        if view.tenant_id is not None and view.tenant_id != tenant_id:
            return None
        return view


def resolve_host_lookup(
    lookup: HostLookup | None = None,
    *,
    base_url: str | None = None,
) -> HostLookup:
    if lookup is not None:
        return lookup
    raw = os.environ.get("HYPERMESH_SERVICE_URL") if base_url is None else base_url
    text = (raw or "").strip()
    if not text:
        return ClosedHostLookup()
    return HttpHostLookup(text)
