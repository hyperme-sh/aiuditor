"""Exact-call machine authorization, independent of model routing policy."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Annotated, Literal

from cryptography.exceptions import InvalidSignature
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from aiuditor_service.machine_proof import public_key

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Name = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")]
Identity = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CapabilityBinding(StrictModel):
    scope: str = Field(max_length=270, pattern=r"^org/[A-Za-z0-9_-]+/site/[A-Za-z0-9_-]+$")
    owner: Name
    capability_id: Name
    revision: Name
    source_sha256: Digest
    graph_sha256: Digest

    @field_validator("owner", "capability_id", "revision")
    @classmethod
    def path_name(cls, value):
        if ".." in value or "//" in value or value.endswith("/"):
            raise ValueError("Invalid capability name")
        return value


class CapabilityGrant(CapabilityBinding):
    arguments_sha256: list[Digest] = Field(min_length=1, max_length=64)
    expires_at: int = Field(strict=True, gt=0)


class CapabilityPolicy(StrictModel):
    protocol_version: Literal[1]
    control_plane_id: Identity
    device_id: uuid.UUID
    request_id: uuid.UUID
    wireguard_public_key: str = Field(min_length=44, max_length=44)
    grants: list[CapabilityGrant] = Field(max_length=64)

    @field_validator("wireguard_public_key")
    @classmethod
    def wg_key(cls, value):
        try:
            raw = base64.b64decode(value, validate=True)
            if len(raw) != 32 or raw == bytes(32) or base64.b64encode(raw).decode() != value:
                raise ValueError()
        except ValueError:
            raise ValueError("Invalid WireGuard public key") from None
        return value


class CapabilityReview(CapabilityBinding):
    protocol_version: Literal[1]
    authority_id: Identity
    control_plane_id: Identity
    tenant_id: uuid.UUID
    device_id: uuid.UUID
    request_id: uuid.UUID
    wireguard_public_key: str = Field(min_length=44, max_length=44)
    arguments_sha256: Digest
    call_id: uuid.UUID
    issued_at: int = Field(strict=True)
    expires_at: int = Field(strict=True)
    nonce: Digest
    public_key: str = Field(min_length=44, max_length=44)
    signature: str = Field(min_length=88, max_length=88, repr=False)


def signing_message(body: CapabilityReview) -> bytes:
    # Every field except the signature is bound. Only ASCII strings, integers and
    # UUIDs occur; consumers can reproduce these canonical bytes without floats.
    payload = body.model_dump(mode="json", exclude={"signature"})
    return b"hypermesh-capability-review-v1\n" + json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def verify_signature(body, now=None):
    now = int(time.time()) if now is None else now
    if not (now - 30 <= body.issued_at <= now + 5) or not (
        now < body.expires_at <= body.issued_at + 30
    ):
        raise HTTPException(401, "Capability call expired or clock invalid")
    try:
        signature = base64.b64decode(body.signature, validate=True)
        if len(signature) != 64 or base64.b64encode(signature).decode() != body.signature:
            raise ValueError()
        public_key(body.public_key).verify(signature, signing_message(body))
    except (InvalidSignature, ValueError):
        raise HTTPException(401, "Invalid capability signature") from None


def review(state, body, now=None):
    """Consume one call under the machine row lock, including policy refusals."""
    now = int(time.time()) if now is None else now
    verify_signature(body, now)
    state = dict(state or {})
    current = hmac.compare_digest(state.get("public_key", ""), body.public_key)
    previous = hmac.compare_digest(state.get("previous_key", ""), body.public_key) and state.get("previous_until", 0) > now
    if not (current or previous):
        raise HTTPException(401, "Machine signing key retired")
    calls = [entry for entry in state.get("capability_calls", []) if entry[2] > now]
    if any(entry[0] == body.nonce or entry[1] == str(body.call_id) for entry in calls):
        raise HTTPException(409, "Capability call already reviewed")
    if len(calls) >= 128:
        raise HTTPException(429, "Capability review rate exceeded")
    state["capability_calls"] = calls + [[body.nonce, str(body.call_id), body.expires_at]]
    policy = state.get("capability_policy")
    allowed = False
    if policy:
        policy = CapabilityPolicy.model_validate(policy)
        bound = (
            policy.device_id == body.device_id
            and policy.request_id == body.request_id
            and policy.control_plane_id == body.control_plane_id
            and hmac.compare_digest(policy.wireguard_public_key, body.wireguard_public_key)
        )
        binding = {key: getattr(body, key) for key in CapabilityBinding.model_fields}
        allowed = bound and any(
            grant.model_dump(include=set(CapabilityBinding.model_fields)) == binding
            and grant.expires_at >= body.expires_at
            and body.arguments_sha256 in grant.arguments_sha256
            for grant in policy.grants
        )
    return state, {
        "protocol_version": 1,
        "call_id": str(body.call_id),
        "request_sha256": hashlib.sha256(signing_message(body)).hexdigest(),
        "decision": "allow" if allowed else "deny",
        "expires_at": body.expires_at,
    }
