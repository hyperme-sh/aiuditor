from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

from fastapi import HTTPException
import pytest

from aiuditor_service import workspace_connection_routes as routes

BASE = "/api/v1/aiuditor/workspace-connections"
SIGNING_KEY = b"example-fixture-workspace-key-0123456789"


@pytest.fixture
def call(monkeypatch):
    monkeypatch.setenv("AIUDITOR_WORKSPACE_CONNECTION_KEY", SIGNING_KEY.decode())
    return {
        "version": 1,
        "request_id": str(uuid.uuid4()),
        "issued_at": int(time.time()),
        "tenant_id": str(uuid.uuid4()),
        "workspace_id": str(uuid.uuid4()),
        "connection_id": str(uuid.uuid4()),
        "grant_id": str(uuid.uuid4()),
        "provider": "outlook",
        "target": "fixture-mailbox",
        "operation": "read",
        "actor_kind": "person",
        "actor_id": "fixture-operator",
        "run_id": None,
    }


@pytest.fixture
def owner_store(monkeypatch, call):
    connection = SimpleNamespace(
        connection_id=uuid.UUID(call["connection_id"]), workspace_id=uuid.UUID(call["workspace_id"]),
        provider=call["provider"], target=call["target"], disabled_at=None,
    )
    grant = SimpleNamespace(
        grant_id=uuid.UUID(call["grant_id"]), workspace_id=connection.workspace_id,
        connection_id=connection.connection_id, actor_kind=call["actor_kind"], actor_id=call["actor_id"],
        expires_at=call["issued_at"] + 120, revoked_at=None, operations=["read"],
    )
    session = SimpleNamespace(scalar=AsyncMock())
    transactions = []

    @asynccontextmanager
    async def transaction(tenant):
        transactions.append(tenant)
        yield session

    monkeypatch.setattr(routes, "transaction", transaction)
    return connection, grant, session, transactions


def signed(call):
    body = json.dumps(call, separators=(",", ":")).encode()
    digest = hmac.new(SIGNING_KEY, b"aiuditor.workspace-connection.v1\n" + body, hashlib.sha256).hexdigest()
    return body, {"X-Workspace-Connection-Signature": f"sha256={digest}"}


async def authorize(http_client, call):
    body, headers = signed(call)
    result = await http_client.post(f"{BASE}/authorize", content=body, headers=headers)
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    return result.json()


async def test_authorization_is_consumed_and_audited(http_client, call, owner_store):
    connection, grant, session, transactions = owner_store
    request_id = uuid.UUID(call["request_id"])
    session.scalar.side_effect = [connection, grant, request_id, connection, grant, None]

    assert await authorize(http_client, call) == {
        "version": 1, "request_id": str(request_id), "allowed": True, "reason": "allowed",
    }
    assert await authorize(http_client, call) == {
        "version": 1, "request_id": str(request_id), "allowed": False, "reason": "replayed",
    }
    assert transactions == [uuid.UUID(call["tenant_id"])] * 2
    query = session.scalar.await_args_list[2].args[0]
    assert query.compile().params["reason"] == "allowed"
    assert "actor_id" not in query.compile().params
    assert "target" not in query.compile().params


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"target": "other-mailbox"}, "scope_mismatch"),
        ({"provider": "odoo"}, "scope_mismatch"),
        ({"actor_id": "other-user"}, "scope_mismatch"),
        ({"actor_kind": "schedule", "run_id": str(uuid.uuid4())}, "scope_mismatch"),
        ({"operation": "send"}, "operation_denied"),
        ({"operation": "draft"}, "operation_denied"),
    ],
)
async def test_operation_never_expands_granted_scope(http_client, call, owner_store, changes, reason):
    connection, grant, session, _ = owner_store
    session.scalar.side_effect = [connection, grant, uuid.UUID(call["request_id"])]

    result = await authorize(http_client, {**call, **changes})

    assert result["allowed"] is False
    assert result["reason"] == reason


@pytest.mark.parametrize("scope", ["tenant_id", "workspace_id"])
async def test_wrong_tenant_and_workspace_select_only_the_attested_scope(http_client, call, owner_store, scope):
    _, _, session, _ = owner_store
    other = uuid.uuid4()
    session.scalar.side_effect = [None, uuid.UUID(call["request_id"])]

    result = await authorize(http_client, {**call, scope: str(other)})

    assert result["reason"] == "scope_mismatch"
    query = session.scalar.await_args_list[0].args[0]
    assert query.compile().params[scope + "_1"] == other


async def test_revocation_denies_the_next_call(http_client, monkeypatch, call, owner_store):
    connection, grant, session, _ = owner_store
    next_id = uuid.uuid4()
    session.scalar.side_effect = [
        connection, grant, uuid.UUID(call["request_id"]), connection, grant, connection, grant, next_id,
    ]
    monkeypatch.setattr(routes, "human", lambda _: (uuid.UUID(call["tenant_id"]), "workspace-admin"))
    current_authority = AsyncMock()
    monkeypatch.setattr(routes, "require_authority", current_authority)

    assert (await authorize(http_client, call))["allowed"] is True
    revoked = await http_client.post(
        f"{BASE}/{call['connection_id']}/grants/{call['grant_id']}/revoke",
        params={"workspace_id": call["workspace_id"]},
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    assert current_authority.await_args.args[3:] == ("organization.admin", connection.workspace_id)
    assert (await authorize(http_client, {**call, "request_id": str(next_id)}))["reason"] == "inactive"


async def test_grant_expires_without_reusing_the_previous_decision(http_client, call, owner_store):
    connection, grant, session, _ = owner_store
    grant.expires_at = call["issued_at"] - 1
    session.scalar.side_effect = [connection, grant, uuid.UUID(call["request_id"])]
    assert (await authorize(http_client, call))["reason"] == "inactive"


async def test_invalid_attestations_are_refused_before_borrowing_storage(http_client, call, owner_store):
    body, headers = signed(call)
    stale, stale_headers = signed({**call, "issued_at": call["issued_at"] - 120})
    invalid = [
        (body, {}, 401),
        (body.replace(b"fixture-mailbox", b"different-mailbox"), headers, 401),
        (stale, stale_headers, 401),
        (body, {**headers, "X-Tenant-ID": str(uuid.uuid4())}, 403),
        (b" " * 4097, headers, 413),
    ]
    for payload, request_headers, status in invalid:
        result = await http_client.post(f"{BASE}/authorize", content=payload, headers=request_headers)
        assert result.status_code == status
    assert owner_store[3] == []


async def test_registration_checks_current_admin_and_redacts_bad_input(http_client, monkeypatch, call, owner_store):
    body = {key: call[key] for key in ("workspace_id", "connection_id", "provider", "target")}
    monkeypatch.setattr(routes, "human", lambda _: (uuid.UUID(call["tenant_id"]), "operator"))
    authority = AsyncMock(side_effect=HTTPException(403, "Current workspace admin required"))
    monkeypatch.setattr(routes, "require_authority", authority)

    refusal = await http_client.post(BASE, json=body)
    assert refusal.status_code == 403
    assert authority.await_args.args[3:] == ("organization.admin", uuid.UUID(call["workspace_id"]))
    invalid = await http_client.post(BASE, json={**body, "token": "input-to-redact"})
    assert invalid.status_code == 422
    assert "input-to-redact" not in invalid.text
    assert invalid.headers["cache-control"] == "no-store"
    assert owner_store[3] == []
