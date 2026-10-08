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

from aiuditor_service.role import ROLE_HEADER, resolve_role

HOST_LOOKUP_REQUIRED = "HYPERMESH_SERVICE_URL is required to assign a host"
HOST_LOOKUP_FAILED = "hypermesh host lookup failed"
PLANE_HOP_REQUIRED = "HYPERMESH_SERVICE_URL is required to assign a lease or mint a ticket"
PLANE_HOP_FAILED = "hypermesh plane hop failed"
HOST_ROLE = "host"


class HostLookupUnavailable(Exception):
    """Hypermesh is unset or the hop failed. Assignment fail-closes."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = message


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
    """Fields from GET /leases/{id}. No payment detail or ticket blob."""

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
    """Offered lease for a catalog host row. HMAC stays on Hypermesh."""

    lease_id: uuid.UUID
    device_id: uuid.UUID
    purpose: str
    contract_id: uuid.UUID | None = None
    # Pending USDC payment Hypermesh opened for this lease.
    billing_record_id: uuid.UUID | None = None
    payment_amount_atoms: int | None = None
    payment_test_credit_available: bool = False


def _optional_uuid(raw: object) -> uuid.UUID | None:
    return uuid.UUID(str(raw)) if raw else None


def _optional_int(raw: object) -> int | None:
    if raw is None or isinstance(raw, bool):
        return None
    return int(raw)


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
        class_id: str | None = None,
        catalog_id: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> OfferedLease: ...

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
        admission_id: uuid.UUID | None = None,
        size_bytes: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]: ...

    async def promote_catalog(
        self,
        tenant_id: uuid.UUID,
        *,
        catalog_id: str,
        class_id: str,
        artifact_name: str,
        file_hash: str,
        admission_id: uuid.UUID,
        format: str | None = None,
        quant: str | None = None,
        runtime: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]: ...


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
        class_id: str | None = None,
        catalog_id: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> OfferedLease:
        raise HostLookupUnavailable(PLANE_HOP_REQUIRED)

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
        admission_id: uuid.UUID | None = None,
        size_bytes: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        raise HostLookupUnavailable(PLANE_HOP_REQUIRED)

    async def promote_catalog(
        self,
        tenant_id: uuid.UUID,
        *,
        catalog_id: str,
        class_id: str,
        artifact_name: str,
        file_hash: str,
        admission_id: uuid.UUID,
        format: str | None = None,
        quant: str | None = None,
        runtime: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        raise HostLookupUnavailable(PLANE_HOP_REQUIRED)


class HttpPlaneHop:
    def __init__(
        self, base_url: str, *, timeout_s: float = 10.0, role: str = "global"
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s
        self._role = role

    def _hop(self, tenant_id: uuid.UUID, headers: dict[str, str] | None) -> dict[str, str]:
        hop = {**(headers or {}), "X-Tenant-ID": str(tenant_id)}
        hop["X-Tenant-ID"] = str(tenant_id)
        hop.setdefault(ROLE_HEADER, self._role)
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
        class_id: str | None = None,
        catalog_id: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> OfferedLease:
        payload: dict[str, object] = {
            "kind": kind,
            "device_id": str(device_id),
            "reserved_hours": reserved_hours,
            "contract_id": str(contract_id),
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
                contract_id=uuid.UUID(str(raw_cid)) if raw_cid else None,
                billing_record_id=_optional_uuid(body.get("billing_record_id")),
                payment_amount_atoms=_optional_int(body.get("payment_amount_atoms")),
                payment_test_credit_available=body.get("payment_test_credit_available") is True,
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise HostLookupUnavailable(PLANE_HOP_FAILED) from exc

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
        admission_id: uuid.UUID | None = None,
        size_bytes: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "class_id": class_id,
            "catalog_id": catalog_id,
            "enqueue_pull": enqueue_pull,
        }
        if artifact_name:
            payload["artifact_name"] = artifact_name
        if file_hash:
            payload["file_hash"] = file_hash
        if quant:
            payload["quant"] = quant
        if runtime:
            payload["runtime"] = runtime
        if source_ref:
            payload["source_ref"] = source_ref
        if admission_id is not None:
            payload["admission_id"] = str(admission_id)
        if size_bytes is not None:
            payload["size_bytes"] = size_bytes
        hop = self._hop(tenant_id, headers)
        token = (os.environ.get("HYPERMESH_INTERNAL_TOKEN") or "").strip()
        if token:
            hop["X-Internal-Token"] = token
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base}/api/v1/hypermesh/lab/soaks",
                    headers=hop,
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise HostLookupUnavailable(PLANE_HOP_FAILED) from exc
        if response.status_code >= 400:
            detail = _http_detail(response) or PLANE_HOP_FAILED
            raise HostLookupUnavailable(detail, status_code=response.status_code)
        try:
            body = response.json()
        except ValueError as exc:
            raise HostLookupUnavailable(PLANE_HOP_FAILED) from exc
        if not isinstance(body, dict):
            raise HostLookupUnavailable(PLANE_HOP_FAILED)
        return body

    async def promote_catalog(
        self,
        tenant_id: uuid.UUID,
        *,
        catalog_id: str,
        class_id: str,
        artifact_name: str,
        file_hash: str,
        admission_id: uuid.UUID,
        format: str | None = None,
        quant: str | None = None,
        runtime: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "catalog_id": catalog_id,
            "class_id": class_id,
            "artifact_name": artifact_name,
            "file_hash": file_hash,
            "admission_id": str(admission_id),
        }
        if format:
            payload["format"] = format
        if quant:
            payload["quant"] = quant
        if runtime:
            payload["runtime"] = runtime
        hop = self._hop(tenant_id, headers)
        token = (os.environ.get("HYPERMESH_INTERNAL_TOKEN") or "").strip()
        if token:
            hop["X-Internal-Token"] = token
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base}/api/v1/hypermesh/catalog/promote",
                    headers=hop,
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise HostLookupUnavailable(PLANE_HOP_FAILED) from exc
        if response.status_code >= 400:
            raise HostLookupUnavailable(_http_detail(response) or PLANE_HOP_FAILED)
        try:
            body = response.json()
        except ValueError as exc:
            raise HostLookupUnavailable(PLANE_HOP_FAILED) from exc
        if not isinstance(body, dict):
            raise HostLookupUnavailable(PLANE_HOP_FAILED)
        return body


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
    soaks: list[dict[str, object]] = field(default_factory=list)
    promotions: list[dict[str, object]] = field(default_factory=list)
    mint_error: str | None = None
    violate_error: str | None = None
    offer_error: str | None = None
    offer_amount_atoms: int | None = 1_000_000
    offer_test_credit_available: bool = False
    soak_error: str | None = None
    promote_error: str | None = None

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
            contract_id=contract_id,
            billing_record_id=uuid.uuid4(),
            payment_amount_atoms=self.offer_amount_atoms,
            payment_test_credit_available=self.offer_test_credit_available,
        )
        self.offers.append(
            {
                "tenant_id": tenant_id,
                "kind": kind,
                "device_id": device_id,
                "reserved_hours": reserved_hours,
                "contract_id": contract_id,
                "class_id": class_id,
                "catalog_id": catalog_id,
            }
        )
        return offered

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
        admission_id: uuid.UUID | None = None,
        size_bytes: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        del headers
        if self.soak_error:
            raise HostLookupUnavailable(self.soak_error)
        body = {
            "device_id": str(uuid.uuid4()),
            "class_id": class_id,
            "catalog_id": catalog_id,
            "soak_job_id": str(uuid.uuid4()),
            "pull_job_id": str(uuid.uuid4()) if enqueue_pull else None,
            "artifact_name": artifact_name,
            "file_hash": file_hash,
            "quant": quant,
            "runtime": runtime,
            "source_ref": source_ref,
            "admission_id": str(admission_id) if admission_id is not None else None,
            "size_bytes": size_bytes,
            "tenant_id": tenant_id,
        }
        self.soaks.append(body)
        return body

    async def promote_catalog(
        self,
        tenant_id: uuid.UUID,
        *,
        catalog_id: str,
        class_id: str,
        artifact_name: str,
        file_hash: str,
        admission_id: uuid.UUID,
        format: str | None = None,
        quant: str | None = None,
        runtime: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        del headers
        if self.promote_error:
            raise HostLookupUnavailable(self.promote_error)
        body = {
            "id": catalog_id,
            "catalog_id": catalog_id,
            "class_id": class_id,
            "artifact_name": artifact_name,
            "file_hash": file_hash,
            "format": format,
            "quant": quant,
            "runtime": runtime,
            "admission_id": str(admission_id),
            "origin": "upload",
            "certified": False,
            "tenant_id": str(tenant_id),
        }
        self.promotions.append(body)
        return body


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
    role: str | None = None,
) -> PlaneHop:
    if hop is not None:
        return hop
    raw = os.environ.get("HYPERMESH_SERVICE_URL") if base_url is None else base_url
    text = (raw or "").strip()
    if not text:
        return ClosedPlaneHop()
    return HttpPlaneHop(text, role=role or resolve_role().value)
