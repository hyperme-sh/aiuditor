"""AIuditor deployment role. One image, three roles.

global is the Azure marketplace SoT. operator is a private plane.
witness is a host/door package: hydrate, inbox, policy. It never fulfills
and it never mints a ticket the market honors.
"""

from __future__ import annotations

import os
from enum import StrEnum

from fastapi import HTTPException, Request

ROLE_HEADER = "X-Aiuditor-Role"
ROLE_INVALID = "AIUDITOR_ROLE must be global, operator, or witness"
WITNESS_CANNOT_WRITE_GRANT = "witness cannot write a grant"
WITNESS_CANNOT_MINT = "witness cannot mint a market ticket"
WITNESS_PULL_ONLY = "only a witness pulls hydrate from a parent"

WRITER_MARKET = "market"
WRITER_PRIVATE = "private"
WRITER_NONE = "none"


class AiuditorRole(StrEnum):
    GLOBAL = "global"
    OPERATOR = "operator"
    WITNESS = "witness"


class RoleUnavailable(RuntimeError):
    """AIUDITOR_ROLE must be global, operator, or witness."""


_WRITER = {
    AiuditorRole.GLOBAL: WRITER_MARKET,
    AiuditorRole.OPERATOR: WRITER_PRIVATE,
    AiuditorRole.WITNESS: WRITER_NONE,
}


def resolve_role(raw: str | None = None) -> AiuditorRole:
    text = raw if raw is not None else os.environ.get("AIUDITOR_ROLE")
    cleaned = (text or "").strip().lower() or "global"
    try:
        return AiuditorRole(cleaned)
    except ValueError as exc:
        raise RoleUnavailable(ROLE_INVALID) from exc


def writer_for(role: AiuditorRole) -> str:
    return _WRITER[role]


def role_from_request(request: Request) -> AiuditorRole:
    role = getattr(request.app.state, "aiuditor_role", None)
    if isinstance(role, AiuditorRole):
        return role
    return resolve_role(str(role) if role is not None else None)


def require_grant_write(role: AiuditorRole) -> None:
    if role is AiuditorRole.WITNESS:
        raise HTTPException(status_code=403, detail=WITNESS_CANNOT_WRITE_GRANT)


def require_market_mint(role: AiuditorRole) -> None:
    if role is AiuditorRole.WITNESS:
        raise HTTPException(status_code=403, detail=WITNESS_CANNOT_MINT)


def require_witness(role: AiuditorRole) -> None:
    if role is not AiuditorRole.WITNESS:
        raise HTTPException(status_code=403, detail=WITNESS_PULL_ONLY)
