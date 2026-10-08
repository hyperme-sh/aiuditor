"""AIuditor-owned machine proof and bounded credential state transitions."""

from __future__ import annotations
import base64
import hashlib
import hmac
import secrets
import time
import uuid
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

TOKEN_TTL = 600
KEY_OVERLAP = 86400
PREFIX = "hm_msi_"


class Proof(BaseModel):
    model_config = ConfigDict(extra="forbid")
    authority_id: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"
    )
    control_plane_id: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"
    )
    protocol_version: Literal[1]
    device_id: uuid.UUID
    request_id: uuid.UUID
    nonce: str = Field(pattern=r"^[a-f0-9]{64}$")
    public_key: str = Field(min_length=44, max_length=44)
    issued_at: int = Field(strict=True)
    signature: str = Field(min_length=88, max_length=88, repr=False)
    next_public_key: str = Field(default="", max_length=44)


def message(tid, p):
    return "\n".join(
        (
            "hypermesh-machine-identity-v1",
            str(tid),
            str(p.device_id),
            str(p.request_id),
            p.authority_id,
            p.control_plane_id,
            p.public_key,
            str(p.issued_at),
            p.nonce,
            p.next_public_key,
            *([str(p.contract_id)] if hasattr(p, "contract_id") else []),
        )
    ).encode()


def public_key(value):
    try:
        raw = base64.b64decode(value, validate=True)
        if len(raw) != 32 or base64.b64encode(raw).decode() != value:
            raise ValueError()
        return Ed25519PublicKey.from_public_bytes(raw)
    except ValueError:
        raise HTTPException(400, "Invalid machine public key") from None


def verified_proof(tid, p, now):
    if abs(now - p.issued_at) > 60:
        raise HTTPException(401, "Machine proof expired; synchronize the host clock")
    try:
        public_key(p.public_key).verify(
            base64.b64decode(p.signature, validate=True), message(tid, p)
        )
    except (InvalidSignature, ValueError):
        raise HTTPException(401, "Invalid machine proof") from None
    if p.next_public_key:
        public_key(p.next_public_key)


def issue(state, tid, p, *, bind=False, now=None):
    """Pure state transition; caller must hold the registration row lock."""
    now = int(time.time()) if now is None else now
    verified_proof(tid, p, now)
    state = dict(state or {})
    if not state:
        if not bind or p.next_public_key:
            raise HTTPException(401, "Machine identity not bound")
        state = {
            "public_key": p.public_key,
            "created_at": now,
            "nonces": [],
            "tokens": [],
        }
    current = hmac.compare_digest(state["public_key"], p.public_key)
    previous = (
        hmac.compare_digest(state.get("previous_key", ""), p.public_key)
        and state.get("previous_until", 0) > now
    )
    if bind and not current:
        raise HTTPException(409, "Machine identity already bound; use signed rotation")
    if not current and not previous:
        raise HTTPException(401, "Machine signing key retired")
    nonces = [v for v in state.get("nonces", []) if v[1] > now]
    if any(hmac.compare_digest(v[0], p.nonce) for v in nonces):
        raise HTTPException(401, "Machine proof already used")
    if len(nonces) >= 128:
        raise HTTPException(429, "Machine proof rate exceeded")
    tokens = [v for v in state.get("tokens", []) if v[1] > now]
    if len(tokens) >= 32:
        raise HTTPException(429, "Machine token rate exceeded")
    if p.next_public_key:
        if p.next_public_key == p.public_key:
            raise HTTPException(400, "Rotation requires a new key")
        if previous:
            # Lost rotation reply: acknowledge the already-committed transition.
            if p.next_public_key != state["public_key"]:
                raise HTTPException(
                    409, "Previous key cannot choose another replacement"
                )
        else:
            if state.get("previous_until", 0) > now:
                raise HTTPException(409, "Previous rotation overlap is still active")
            state.update(
                previous_key=p.public_key,
                previous_until=now + KEY_OVERLAP,
                public_key=p.next_public_key,
                created_at=now,
            )
    token = PREFIX + str(p.device_id) + "." + secrets.token_urlsafe(32)
    state["nonces"] = nonces + [[p.nonce, p.issued_at + 61]]
    state["tokens"] = tokens + [
        [hashlib.sha256(token.encode()).hexdigest(), now + TOKEN_TTL]
    ]
    return state, {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": TOKEN_TTL,
        "public_key": state["public_key"],
        "key_created_at": state["created_at"],
    }


def token_digest(secret):
    if secret.startswith(PREFIX):
        return "msi:" + hashlib.sha256(secret.encode()).hexdigest()
    return hashlib.sha256(secret.encode()).hexdigest()


def access_allowed(state, digest, now=None):
    now = time.time() if now is None else now
    return any(
        hmac.compare_digest(v[0], digest) and v[1] > now
        for v in (state or {}).get("tokens", [])
    )
