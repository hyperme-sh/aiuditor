"""Machine identity is durable; access and every proof are short lived."""

import base64
import hashlib
import secrets
import uuid
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from aiuditor_service import machine_proof as mi

NOW = 1791400000


def pub(key):
    return base64.b64encode(
        key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode()


def proof(key, tid, *, now=NOW, next_key=None, device=None, request=None):
    body = mi.Proof(
        authority_id="local",
        control_plane_id="local",
        protocol_version=1,
        device_id=device or uuid.uuid4(),
        request_id=request or uuid.uuid4(),
        nonce=secrets.token_hex(32),
        public_key=pub(key),
        issued_at=now,
        signature="A" * 88,
        next_public_key=pub(next_key) if next_key else "",
    )
    body.signature = base64.b64encode(key.sign(mi.message(tid, body))).decode()
    return body


def test_bind_refresh_rotation_and_lost_response_recovery():
    key, replacement = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    tid = uuid.uuid4()
    body = proof(key, tid)
    state, first = mi.issue(None, tid, body, bind=True, now=NOW)
    assert mi.access_allowed(state, mi.token_digest(first["access_token"])[4:], NOW)
    assert not mi.access_allowed(
        state, mi.token_digest(first["access_token"])[4:], NOW + 600
    )
    refresh = proof(key, tid, device=body.device_id, request=body.request_id)
    state, renewed = mi.issue(state, tid, refresh, now=NOW)
    assert first["access_token"] != renewed["access_token"]
    rotation = proof(
        key, tid, next_key=replacement, device=body.device_id, request=body.request_id
    )
    state, rotated = mi.issue(state, tid, rotation, now=NOW)
    assert rotated["public_key"] == pub(replacement)
    previous_until = state["previous_until"]
    # Repeat with a fresh proof after losing the rotation response: no new grace window.
    retry = proof(
        key,
        tid,
        now=NOW + 30,
        next_key=replacement,
        device=body.device_id,
        request=body.request_id,
    )
    state, _ = mi.issue(state, tid, retry, now=NOW + 30)
    assert state["previous_until"] == previous_until
    # The pending replacement saved on disk recovers even after the overlap ends.
    recovered = proof(
        replacement,
        tid,
        now=NOW + 90000,
        device=body.device_id,
        request=body.request_id,
    )
    state, _ = mi.issue(state, tid, recovered, now=NOW + 90000)
    with pytest.raises(HTTPException, match="retired"):
        mi.issue(state, tid, proof(key, tid, now=NOW + 90000), now=NOW + 90000)
    # Only public material and token hashes are persisted.
    assert first["access_token"] not in repr(state)
    assert "private" not in state


@pytest.mark.parametrize(
    "kind",
    [
        "replay",
        "tenant",
        "device",
        "request",
        "next-key",
        "signature",
        "expired",
        "future",
    ],
)
def test_proofs_cannot_be_replayed_or_retargeted(kind):
    key = Ed25519PrivateKey.generate()
    tid = uuid.uuid4()
    body = proof(key, tid)
    state, _ = mi.issue(None, tid, body, bind=True, now=NOW)
    if kind == "tenant":
        tid = uuid.uuid4()
    elif kind == "device":
        body.device_id = uuid.uuid4()
    elif kind == "request":
        body.request_id = uuid.uuid4()
    elif kind == "next-key":
        body.next_public_key = pub(Ed25519PrivateKey.generate())
    elif kind == "signature":
        body.signature = base64.b64encode(bytes(64)).decode()
    when = NOW + (61 if kind == "expired" else -61 if kind == "future" else 0)
    with pytest.raises(HTTPException):
        mi.issue(state, tid, body, now=when)


def test_bootstrap_cannot_replace_bound_key_and_previous_cannot_fork_rotation():
    key, new, other = [Ed25519PrivateKey.generate() for _ in range(3)]
    tid = uuid.uuid4()
    state, _ = mi.issue(None, tid, proof(key, tid), bind=True, now=NOW)
    with pytest.raises(HTTPException):
        mi.issue(state, tid, proof(other, tid), bind=True, now=NOW)
    state, _ = mi.issue(state, tid, proof(key, tid, next_key=new), now=NOW)
    with pytest.raises(HTTPException):
        mi.issue(state, tid, proof(key, tid, next_key=other), now=NOW)


def test_rate_limit_does_not_evict_valid_replay_protection():
    key = Ed25519PrivateKey.generate()
    tid = uuid.uuid4()
    state, _ = mi.issue(None, tid, proof(key, tid), bind=True, now=NOW)
    state["nonces"] = [[secrets.token_hex(32), NOW + 60] for _ in range(128)]
    with pytest.raises(HTTPException) as failure:
        mi.issue(state, tid, proof(key, tid), now=NOW)
    assert failure.value.status_code == 429
    assert len(state["nonces"]) == 128
