import base64
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException

from aiuditor_service.capability_contracts import (
    CapabilityPolicy, CapabilityReview, review, signing_message,
)

NOW = 2_000_000_000


@pytest.fixture
def signed_call():
    key = Ed25519PrivateKey.generate()
    body = CapabilityReview(
        protocol_version=1, authority_id="local", control_plane_id="plane-a",
        tenant_id=uuid.uuid4(), device_id=uuid.uuid4(), request_id=uuid.uuid4(),
        wireguard_public_key=base64.b64encode(b"w" * 32).decode(),
        scope="org/a/site/b", owner="owner", capability_id="sensor/read", revision="v1",
        source_sha256="a" * 64, graph_sha256="b" * 64, arguments_sha256="c" * 64,
        call_id=uuid.uuid4(), issued_at=NOW, expires_at=NOW + 20, nonce="d" * 64,
        public_key=base64.b64encode(key.public_key().public_bytes_raw()).decode(),
        signature="x" * 88,
    )
    def sign(body):
        return body.model_copy(update={"signature": base64.b64encode(key.sign(signing_message(body))).decode()})
    return sign(body), sign


def policy(body):
    return CapabilityPolicy(
        protocol_version=1, control_plane_id=body.control_plane_id,
        device_id=body.device_id, request_id=body.request_id,
        wireguard_public_key=body.wireguard_public_key,
        grants=[dict(scope=body.scope, owner=body.owner, capability_id=body.capability_id,
                     revision=body.revision, source_sha256=body.source_sha256,
                     graph_sha256=body.graph_sha256, arguments_sha256=[body.arguments_sha256],
                     expires_at=NOW + 60)],
    ).model_dump(mode="json")


def test_default_deny_and_exact_opt_in(signed_call):
    body, _ = signed_call
    original = {"public_key": body.public_key, "tokens": [["unrelated", NOW + 60]]}
    denied, result = review(original, body, NOW)
    assert result["decision"] == "deny"
    assert "capability_calls" not in original
    assert denied["tokens"] == original["tokens"]
    state, result = review({**original, "capability_policy": policy(body)}, body, NOW)
    assert result["decision"] == "allow"
    assert result["call_id"] == str(body.call_id)
    assert len(result["request_sha256"]) == 64
    assert state["capability_calls"] == [[body.nonce, str(body.call_id), body.expires_at]]


@pytest.mark.parametrize("field,value", [
    ("scope", "org/other/site/b"), ("owner", "other"), ("capability_id", "sensor/write"),
    ("revision", "v2"), ("source_sha256", "e" * 64), ("graph_sha256", "e" * 64),
    ("arguments_sha256", "e" * 64), ("wireguard_public_key", base64.b64encode(b"z" * 32).decode()),
    ("device_id", uuid.uuid4()), ("request_id", uuid.uuid4()), ("control_plane_id", "other"),
])
def test_policy_binding_and_signature_cover_changed_fields(signed_call, field, value):
    body, sign = signed_call
    state = {"public_key": body.public_key, "capability_policy": policy(body)}
    changed = body.model_copy(update={field: value})
    with pytest.raises(HTTPException, match="Invalid capability signature"):
        review(state, changed, NOW)
    _, result = review(state, sign(changed), NOW)
    assert result["decision"] == "deny"


@pytest.mark.parametrize("field,value", [("tenant_id", uuid.uuid4()), ("authority_id", "other"),
    ("call_id", uuid.uuid4()), ("nonce", "f" * 64), ("issued_at", NOW - 1), ("expires_at", NOW + 21)])
def test_all_other_fields_are_signed(signed_call, field, value):
    body, _ = signed_call
    with pytest.raises(HTTPException):
        review({"public_key": body.public_key}, body.model_copy(update={field: value}), NOW)


def test_deny_consumes_nonce_and_call_id(signed_call):
    body, sign = signed_call
    state, _ = review({"public_key": body.public_key}, body, NOW)
    for change in ({}, {"nonce": "e" * 64}, {"call_id": uuid.uuid4()}):
        with pytest.raises(HTTPException) as error:
            review(state, sign(body.model_copy(update=change)), NOW)
        assert error.value.status_code == 409


@pytest.mark.parametrize("now", [NOW - 6, NOW + 20, NOW + 31])
def test_time_window(signed_call, now):
    body, _ = signed_call
    with pytest.raises(HTTPException) as error:
        review({"public_key": body.public_key}, body, now)
    assert error.value.status_code == 401


def test_retired_keys_expired_policy_and_bounded_state(signed_call):
    body, _ = signed_call
    state = {"public_key": "retired", "previous_key": body.public_key, "previous_until": NOW + 1,
             "capability_policy": policy(body)}
    assert review(state, body, NOW)[1]["decision"] == "allow"
    with pytest.raises(HTTPException):
        review(state, body, NOW + 1)
    state["public_key"] = body.public_key
    state["capability_policy"]["grants"][0]["expires_at"] = NOW + 19
    assert review(state, body, NOW)[1]["decision"] == "deny"
    state["capability_calls"] = [[str(i), str(i), NOW + 30] for i in range(128)]
    with pytest.raises(HTTPException) as error:
        review(state, body, NOW)
    assert error.value.status_code == 429
    state["capability_calls"] = [["old", "old", NOW]]
    assert len(review(state, body, NOW)[0]["capability_calls"]) == 1
