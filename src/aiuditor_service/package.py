"""Host/door witness package. Same image, not a second grant plane.

Binds localhost. Hydrates from AIUDITOR_PARENT_URL. Does not fulfill.
Handoff (spine D) is not this path. Do not put this on hyperme.sh.
"""

from __future__ import annotations

import os
import uuid

from aiuditor_service.role import AiuditorRole

WITNESS_BIND_ALL = "witness must bind 127.0.0.1, not 0.0.0.0"
WITNESS_TENANT_REQUIRED = (
    "AIUDITOR_HYDRATE_TENANT_ID is required when AIUDITOR_PARENT_URL is set"
)
WITNESS_TENANT_INVALID = "AIUDITOR_HYDRATE_TENANT_ID must be a UUID"
DEFAULT_HYDRATE_EVERY_S = 60.0
MIN_HYDRATE_EVERY_S = 5.0


class PackageUnavailable(RuntimeError):
    """Witness package pin failed. Do not start on all interfaces."""


def resolve_bind_host(
    role: AiuditorRole,
    host: str | None = None,
) -> str:
    raw = (os.environ.get("HOST") if host is None else host) or ""
    cleaned = raw.strip()
    if role is AiuditorRole.WITNESS:
        if cleaned in {"", "127.0.0.1", "localhost", "::1"}:
            return cleaned or "127.0.0.1"
        raise PackageUnavailable(WITNESS_BIND_ALL)
    return cleaned or "0.0.0.0"


def resolve_hydrate_tenant(
    role: AiuditorRole,
    *,
    parent_url: str | None = None,
    tenant_id: str | None = None,
) -> uuid.UUID | None:
    parent = (
        os.environ.get("AIUDITOR_PARENT_URL") if parent_url is None else parent_url
    ) or ""
    raw = (
        os.environ.get("AIUDITOR_HYDRATE_TENANT_ID") if tenant_id is None else tenant_id
    ) or ""
    parent = parent.strip()
    raw = raw.strip()
    if role is not AiuditorRole.WITNESS or not parent:
        return None
    if not raw:
        raise PackageUnavailable(WITNESS_TENANT_REQUIRED)
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise PackageUnavailable(WITNESS_TENANT_INVALID) from exc


def resolve_hydrate_every(raw: str | None = None) -> float:
    text = (os.environ.get("AIUDITOR_HYDRATE_EVERY") if raw is None else raw) or ""
    cleaned = text.strip()
    if not cleaned:
        return DEFAULT_HYDRATE_EVERY_S
    try:
        seconds = float(cleaned)
    except ValueError:
        return DEFAULT_HYDRATE_EVERY_S
    return max(MIN_HYDRATE_EVERY_S, seconds)


def witness_uses_memory_persist(role: AiuditorRole, database_url: str | None = None) -> bool:
    """Witness is a derived copy. Postgres is optional on the box."""

    if role is not AiuditorRole.WITNESS:
        return False
    url = (
        os.environ.get("DATABASE_URL") if database_url is None else database_url
    ) or ""
    cleaned = url.strip()
    return not cleaned or "localhost:1" in cleaned
