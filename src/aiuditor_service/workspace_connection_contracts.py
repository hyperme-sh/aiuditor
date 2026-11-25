"""Typed workspace connector scopes and broker attestation."""

import hashlib
import hmac
import os
import re
import time
import uuid
from typing import Annotated, Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

Provider = Literal["odoo", "outlook"]
Operation = Literal["read", "draft", "update", "send"]
ActorKind = Literal["person", "skill", "schedule"]
OpaqueID = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]
SCOPE_PREFIX = b"aiuditor.workspace-connection.v1\n"
MAX_BODY_BYTES = 4096
MAX_PAST_SECONDS = 60
MAX_FUTURE_SECONDS = 5


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConnectionCreate(Contract):
    workspace_id: uuid.UUID
    connection_id: uuid.UUID
    provider: Provider
    target: OpaqueID


class ConnectionView(ConnectionCreate):
    status: Literal["enabled", "disabled"]


class GrantCreate(Contract):
    workspace_id: uuid.UUID
    grant_id: uuid.UUID
    actor_kind: ActorKind
    actor_id: OpaqueID
    operations: Annotated[list[Operation], Field(min_length=1, max_length=4)]
    expires_at: Annotated[StrictInt, Field(ge=0, le=253402300799)]

    @model_validator(mode="after")
    def unique_operations(self):
        if len(self.operations) != len(set(self.operations)):
            raise ValueError("Operations must be distinct")
        return self


class GrantView(GrantCreate):
    connection_id: uuid.UUID
    status: Literal["active", "expired", "revoked", "connection_disabled"]


class Authorization(Contract):
    version: Annotated[StrictInt, Field(ge=1, le=1)]
    request_id: uuid.UUID
    issued_at: StrictInt
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    connection_id: uuid.UUID
    grant_id: uuid.UUID
    provider: Provider
    target: OpaqueID
    operation: Operation
    actor_kind: ActorKind
    actor_id: OpaqueID
    run_id: uuid.UUID | None

    @model_validator(mode="after")
    def require_run_for_automation(self):
        if self.actor_kind != "person" and self.run_id is None:
            raise ValueError("Automation must have a run ID")
        return self


class Decision(Contract):
    version: Literal[1] = 1
    request_id: uuid.UUID
    allowed: bool
    reason: Literal["allowed", "scope_mismatch", "inactive", "operation_denied", "replayed"]


async def verified_authorization(request: Request) -> Authorization:
    key = os.environ.get("AIUDITOR_WORKSPACE_CONNECTION_KEY", "").encode()
    signature = request.headers.get("X-Workspace-Connection-Signature", "")
    if len(key) < 32 or not re.fullmatch(r"sha256=[0-9a-f]{64}", signature):
        raise HTTPException(401, "Trusted workspace connector attestation required")

    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > MAX_BODY_BYTES:
            raise HTTPException(413, "Workspace connector request is too large")
        payload.extend(chunk)
    expected = hmac.digest(key, SCOPE_PREFIX + payload, hashlib.sha256).hex()
    if not hmac.compare_digest(signature, f"sha256={expected}"):
        raise HTTPException(401, "Trusted workspace connector attestation required")
    try:
        call = Authorization.model_validate_json(payload)
        supplied_tenant = request.headers.get("X-Tenant-ID")
        if supplied_tenant is not None and uuid.UUID(supplied_tenant) != call.tenant_id:
            raise HTTPException(403, "Workspace connector tenant mismatch")
    except ValueError:
        raise HTTPException(422, "Invalid workspace connector request") from None

    now = int(time.time())
    if not now - MAX_PAST_SECONDS <= call.issued_at <= now + MAX_FUTURE_SECONDS:
        raise HTTPException(401, "Workspace connector attestation expired")
    return call


def decide(connection, grant, call: Authorization, now: int) -> Decision:
    reason = "allowed"
    if (
        connection is None
        or grant is None
        or connection.provider != call.provider
        or connection.target != call.target
        or grant.actor_kind != call.actor_kind
        or grant.actor_id != call.actor_id
    ):
        reason = "scope_mismatch"
    elif connection.disabled_at is not None or grant.revoked_at is not None or grant.expires_at <= now:
        reason = "inactive"
    elif call.operation not in grant.operations:
        reason = "operation_denied"
    return Decision(request_id=call.request_id, allowed=reason == "allowed", reason=reason)
