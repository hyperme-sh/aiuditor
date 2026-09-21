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
PLANE_HOP_REQUIRED = "HYPERMESH_SERVICE_URL is required to assign a lease or mint a ticket"
PLANE_HOP_FAILED = "hypermesh plane hop failed"
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
    path_b_state: str | None = None
    schedule_hold: bool = False
    sell_state: str | None = None
    class_id: str | None = None
    loaded_catalog_id: str | None = None


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
        path_b_state=str(body["path_b_state"]) if body.get("path_b_state") else None,
        schedule_hold=bool(body.get("schedule_hold") or False),
        sell_state=str(body["sell_state"]) if body.get("sell_state") else None,
        class_id=str(body["class_id"]).strip() if body.get("class_id") else None,
        loaded_catalog_id=(
            str(body["loaded_catalog_id"]).strip()
            if body.get("loaded_catalog_id")
            else None
        ),
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
        path_b_state: str | None = "green",
        schedule_hold: bool = False,
        sell_state: str | None = None,
        class_id: str | None = None,
        loaded_catalog_id: str | None = None,
    ) -> HostView:
        view = HostView(
            id=device_id,
            enrolled=enrolled,
            role=role,
            tenant_id=tenant_id,
            path_b_state=path_b_state,
            schedule_hold=schedule_hold,
            sell_state=sell_state,
            class_id=class_id,
            loaded_catalog_id=loaded_catalog_id,
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


@dataclass(frozen=True)
class LeaseView:
    """Lock fields from GET /leases/{id}. No checkout URL or ticket blob."""

    id: uuid.UUID
    device_id: uuid.UUID | None
    purpose: str
    status: str
    tenant_id: uuid.UUID | None = None
    contract_id: uuid.UUID | None = None


@dataclass(frozen=True)
class IssuedGrant:
    """Minted purpose=ir ticket metadata. HMAC blob stays on Hypermesh."""

    lease_id: uuid.UUID
    device_id: uuid.UUID
    purpose: str
    exp: str | None = None


@dataclass(frozen=True)
class OfferedLease:
    """Checkout session for a catalog host row. HMAC stays on Hypermesh."""

    lease_id: uuid.UUID
    device_id: uuid.UUID
    purpose: str
    checkout_url: str | None = None
    contract_id: uuid.UUID | None = None


class PlaneHop(Protocol):
    async def get_lease(
        self,
        tenant_id: uuid.UUID,
        lease_id: uuid.UUID,
        *,
        headers: dict[str, str] | None = None,
    ) -> LeaseView | None: ...

    async def mint_auditor_ticket(
        self,
        tenant_id: uuid.UUID,
        device_id: uuid.UUID,
        *,
        reserved_hours: int,
        headers: dict[str, str] | None = None,
    ) -> IssuedGrant: ...

    async def apply_violation(
        self,
        tenant_id: uuid.UUID,
        lease_id: uuid.UUID,
        *,
        action: str,
        reason: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None: ...

    async def offer_lease(
        self,
        tenant_id: uuid.UUID,
        *,
        kind: str,
        device_id: uuid.UUID,
        reserved_hours: int,
        contract_id: uuid.UUID,
        success_url: str,
        cancel_url: str,
        class_id: str | None = None,
        catalog_id: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> OfferedLease: ...


class ClosedPlaneHop:
    async def get_lease(
        self,
        tenant_id: uuid.UUID,
        lease_id: uuid.UUID,
        *,
        headers: dict[str, str] | None = None,
    ) -> LeaseView | None:
        raise HostLookupUnavailable(PLANE_HOP_REQUIRED)

    async def mint_auditor_ticket(
        self,
        tenant_id: uuid.UUID,
        device_id: uuid.UUID,
        *,
        reserved_hours: int,
        headers: dict[str, str] | None = None,
    ) -> IssuedGrant:
        raise HostLookupUnavailable(PLANE_HOP_REQUIRED)

    async def apply_violation(
        self,
        tenant_id: uuid.UUID,
        lease_id: uuid.UUID,
        *,
        action: str,
        reason: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        raise HostLookupUnavailable(PLANE_HOP_REQUIRED)

    async def offer_lease(
        self,
        tenant_id: uuid.UUID,
        *,
        kind: str,
        device_id: uuid.UUID,
        reserved_hours: int,
        contract_id: uuid.UUID,
        success_url: str,
        cancel_url: str,
        class_id: str | None = None,
        catalog_id: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> OfferedLease:
        raise HostLookupUnavailable(PLANE_HOP_REQUIRED)


class HttpPlaneHop:
    def __init__(self, base_url: str, *, timeout_s: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s

    def _hop(self, tenant_id: uuid.UUID, headers: dict[str, str] | None) -> dict[str, str]:
        hop = {**(headers or {}), "X-Tenant-ID": str(tenant_id)}
        hop["X-Tenant-ID"] = str(tenant_id)
        return hop

    async def get_lease(
        self,
        tenant_id: uuid.UUID,
        lease_id: uuid.UUID,
        *,
        headers: dict[str, str] | None = None,
    ) -> LeaseView | None:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    f"{self._base}/api/v1/hypermesh/leases/{lease_id}",
                    headers=self._hop(tenant_id, headers),
                )
        except httpx.HTTPError as exc:
            raise HostLookupUnavailable(PLANE_HOP_FAILED) from exc
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise HostLookupUnavailable(PLANE_HOP_FAILED)
        try:
            body = response.json()
        except ValueError as exc:
            raise HostLookupUnavailable(PLANE_HOP_FAILED) from exc
        return _lease_from_body(body, lease_id=lease_id)

    async def mint_auditor_ticket(
        self,
        tenant_id: uuid.UUID,
        device_id: uuid.UUID,
        *,
        reserved_hours: int,
        headers: dict[str, str] | None = None,
    ) -> IssuedGrant:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base}/api/v1/hypermesh/auditor-tickets",
                    headers=self._hop(tenant_id, headers),
                    json={"device_id": str(device_id), "reserved_hours": reserved_hours},
                )
        except httpx.HTTPError as exc:
            raise HostLookupUnavailable(PLANE_HOP_FAILED) from exc
        if response.status_code >= 400:
            detail = _http_detail(response) or PLANE_HOP_FAILED
            raise HostLookupUnavailable(detail)
        try:
            body = response.json()
        except ValueError as exc:
            raise HostLookupUnavailable(PLANE_HOP_FAILED) from exc
        if not isinstance(body, dict):
            raise HostLookupUnavailable(PLANE_HOP_FAILED)
        try:
            return IssuedGrant(
                lease_id=uuid.UUID(str(body["lease_id"])),
                device_id=uuid.UUID(str(body.get("device_id") or device_id)),
                purpose=str(body.get("purpose") or "ir"),
                exp=str(body["exp"]) if body.get("exp") else None,
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise HostLookupUnavailable(PLANE_HOP_FAILED) from exc

    async def apply_violation(
        self,
        tenant_id: uuid.UUID,
        lease_id: uuid.UUID,
        *,
        action: str,
        reason: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        hop = self._hop(tenant_id, headers)
        token = (os.environ.get("HYPERMESH_INTERNAL_TOKEN") or "").strip()
        if token:
            hop["X-Internal-Token"] = token
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base}/api/v1/hypermesh/internal/lease-violate",
                    headers=hop,
                    json={
                        "lease_id": str(lease_id),
                        "action": action,
                        "reason": reason,
                    },
                )
        except httpx.HTTPError as exc:
            raise HostLookupUnavailable(PLANE_HOP_FAILED) from exc
        if response.status_code >= 400:
            raise HostLookupUnavailable(_http_detail(response) or PLANE_HOP_FAILED)

    async def offer_lease(
        self,
        tenant_id: uuid.UUID,
        *,
        kind: str,
        device_id: uuid.UUID,
        reserved_hours: int,
        contract_id: uuid.UUID,
        success_url: str,
        cancel_url: str,
        class_id: str | None = None,
        catalog_id: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> OfferedLease:
        payload: dict[str, object] = {
            "kind": kind,
            "device_id": str(device_id),
            "reserved_hours": reserved_hours,
            "contract_id": str(contract_id),
            "success_url": success_url,
            "cancel_url": cancel_url,
            "purpose": "renter",
        }
        if class_id:
            payload["class_id"] = class_id
        if catalog_id:
            payload["catalog_id"] = catalog_id
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base}/api/v1/hypermesh/leases",
                    headers=self._hop(tenant_id, headers),
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise HostLookupUnavailable(PLANE_HOP_FAILED) from exc
        if response.status_code >= 400:
            detail = _http_detail(response) or PLANE_HOP_FAILED
            raise HostLookupUnavailable(detail)
        try:
            body = response.json()
        except ValueError as exc:
            raise HostLookupUnavailable(PLANE_HOP_FAILED) from exc
        if not isinstance(body, dict):
            raise HostLookupUnavailable(PLANE_HOP_FAILED)
        try:
            raw_id = body.get("id") or body.get("lease_id")
            raw_device = body.get("device_id") or device_id
            raw_cid = body.get("contract_id") or contract_id
            return OfferedLease(
                lease_id=uuid.UUID(str(raw_id)),
                device_id=uuid.UUID(str(raw_device)),
                purpose=str(body.get("purpose") or "renter"),
                checkout_url=str(body["checkout_url"]) if body.get("checkout_url") else None,
                contract_id=uuid.UUID(str(raw_cid)) if raw_cid else None,
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise HostLookupUnavailable(PLANE_HOP_FAILED) from exc


def _http_detail(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    if isinstance(body, dict) and body.get("detail"):
        return str(body["detail"])
    return None


def _lease_from_body(body: object, *, lease_id: uuid.UUID) -> LeaseView:
    if not isinstance(body, dict):
        raise HostLookupUnavailable(PLANE_HOP_FAILED)
    raw_device = body.get("device_id")
    raw_tenant = body.get("tenant_id")
    try:
        device_id = uuid.UUID(str(raw_device)) if raw_device else None
        tenant_id = uuid.UUID(str(raw_tenant)) if raw_tenant else None
        parsed_id = uuid.UUID(str(body.get("id") or lease_id))
        raw_cid = body.get("contract_id")
        contract_id = uuid.UUID(str(raw_cid)) if raw_cid else None
    except (ValueError, AttributeError, TypeError) as exc:
        raise HostLookupUnavailable(PLANE_HOP_FAILED) from exc
    return LeaseView(
        id=parsed_id,
        device_id=device_id,
        purpose=str(body.get("purpose") or "renter"),
        status=str(body.get("status") or ""),
        tenant_id=tenant_id,
        contract_id=contract_id,
    )


@dataclass
class FakePlaneHop:
    leases: dict[uuid.UUID, LeaseView] = field(default_factory=dict)
    grants: dict[uuid.UUID, IssuedGrant] = field(default_factory=dict)
    violations: list[dict[str, object]] = field(default_factory=list)
    offers: list[dict[str, object]] = field(default_factory=list)
    mint_error: str | None = None
    violate_error: str | None = None
    offer_error: str | None = None

    def add_lease(
        self,
        lease_id: uuid.UUID,
        *,
        device_id: uuid.UUID,
        purpose: str = "renter",
        status: str = "active",
        tenant_id: uuid.UUID | None = None,
        contract_id: uuid.UUID | None = None,
    ) -> LeaseView:
        view = LeaseView(
            id=lease_id,
            device_id=device_id,
            purpose=purpose,
            status=status,
            tenant_id=tenant_id,
            contract_id=contract_id,
        )
        self.leases[lease_id] = view
        return view

    async def get_lease(
        self,
        tenant_id: uuid.UUID,
        lease_id: uuid.UUID,
        *,
        headers: dict[str, str] | None = None,
    ) -> LeaseView | None:
        view = self.leases.get(lease_id)
        if view is None:
            return None
        if view.tenant_id is not None and view.tenant_id != tenant_id:
            return None
        return view

    async def mint_auditor_ticket(
        self,
        tenant_id: uuid.UUID,
        device_id: uuid.UUID,
        *,
        reserved_hours: int,
        headers: dict[str, str] | None = None,
    ) -> IssuedGrant:
        del tenant_id, reserved_hours, headers
        if self.mint_error:
            raise HostLookupUnavailable(self.mint_error)
        existing = self.grants.get(device_id)
        if existing is not None:
            raise HostLookupUnavailable("device already has an auditor ticket")
        grant = IssuedGrant(
            lease_id=uuid.uuid4(),
            device_id=device_id,
            purpose="ir",
            exp="2026-09-21T16:00:00+00:00",
        )
        self.grants[device_id] = grant
        return grant

    async def apply_violation(
        self,
        tenant_id: uuid.UUID,
        lease_id: uuid.UUID,
        *,
        action: str,
        reason: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        del headers
        if self.violate_error:
            raise HostLookupUnavailable(self.violate_error)
        self.violations.append(
            {
                "tenant_id": tenant_id,
                "lease_id": lease_id,
                "action": action,
                "reason": reason,
            }
        )
        view = self.leases.get(lease_id)
        if view is not None:
            status = {
                "suspend": "suspended",
                "stop": "failed",
                "refund": "refunded",
            }.get(action, view.status)
            self.leases[lease_id] = LeaseView(
                id=view.id,
                device_id=view.device_id,
                purpose=view.purpose,
                status=status,
                tenant_id=view.tenant_id,
                contract_id=view.contract_id,
            )

    async def offer_lease(
        self,
        tenant_id: uuid.UUID,
        *,
        kind: str,
        device_id: uuid.UUID,
        reserved_hours: int,
        contract_id: uuid.UUID,
        success_url: str,
        cancel_url: str,
        class_id: str | None = None,
        catalog_id: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> OfferedLease:
        del headers
        if self.offer_error:
            raise HostLookupUnavailable(self.offer_error)
        for view in self.leases.values():
            if view.device_id == device_id and view.purpose in {"renter", "service"}:
                if view.status not in {"ended", "failed", "refunded"}:
                    raise HostLookupUnavailable("Device already has a live lease")
        lease_id = uuid.uuid4()
        self.add_lease(
            lease_id,
            device_id=device_id,
            purpose="renter",
            status="offered",
            tenant_id=tenant_id,
            contract_id=contract_id,
        )
        offered = OfferedLease(
            lease_id=lease_id,
            device_id=device_id,
            purpose="renter",
            checkout_url="https://checkout.test/session",
            contract_id=contract_id,
        )
        self.offers.append(
            {
                "tenant_id": tenant_id,
                "kind": kind,
                "device_id": device_id,
                "reserved_hours": reserved_hours,
                "contract_id": contract_id,
                "success_url": success_url,
                "cancel_url": cancel_url,
                "class_id": class_id,
                "catalog_id": catalog_id,
            }
        )
        return offered


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


def resolve_plane_hop(
    hop: PlaneHop | None = None,
    *,
    base_url: str | None = None,
) -> PlaneHop:
    if hop is not None:
        return hop
    raw = os.environ.get("HYPERMESH_SERVICE_URL") if base_url is None else base_url
    text = (raw or "").strip()
    if not text:
        return ClosedPlaneHop()
    return HttpPlaneHop(text)
