"""Route contracts with an in-memory transaction substitute; no live authority."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
import time
import uuid

import pytest
from fastapi import HTTPException, Response
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from aiuditor_service import machine_identity as routes
from test_machine_proof import proof


@pytest.mark.asyncio
async def test_client_routes_require_attestation_and_keep_policy(monkeypatch):
    tid = uuid.uuid4()
    key = Ed25519PrivateKey.generate()
    first = proof(key, tid, now=int(time.time()))
    row = SimpleNamespace(request_id=first.request_id, state={}, revoked=False)
    class Session:
        async def execute(self, *_): pass
        async def scalar(self, *_): return row
    @asynccontextmanager
    async def transaction(_): yield Session()
    monkeypatch.setattr(routes, 'transaction', transaction)
    monkeypatch.setenv('AIUDITOR_ID', 'local')
    calls = []
    def actor(request):
        calls.append(request)
        if request != 'attested': raise HTTPException(401, 'Trusted plane required')
        return tid, 'local'
    monkeypatch.setattr(routes, 'actor', actor)
    for handler in (routes.bind_client, routes.renew_client):
        with pytest.raises(HTTPException) as denied:
            await handler('untrusted', first, Response())
        assert denied.value.status_code == 401
    result = await routes.bind_client('attested', first, Response())
    assert result['token_type'] == 'Bearer'
    assert row.state['owner_attested_client']
    fresh = proof(key, tid, now=int(time.time()), device=first.device_id, request=first.request_id)
    with pytest.raises(HTTPException) as denied:
        await routes.renew('attested', fresh, Response())
    assert denied.value.status_code == 403
    await routes.renew_client('attested', fresh, Response())
    import hashlib
    lookup = routes.Lookup(device_id=first.device_id, request_id=first.request_id,
                           digest=hashlib.sha256(result['access_token'].encode()).hexdigest())
    assert not (await routes.introspect('attested', lookup, Response()))['active']
    assert (await routes.introspect_client('attested', lookup, Response()))['active']
    row.revoked = True
    assert not (await routes.introspect_client('attested', lookup, Response()))['active']
    with pytest.raises(HTTPException) as denied:
        await routes.renew_client('attested', fresh, Response())
    assert denied.value.status_code == 410
    assert calls


@pytest.mark.asyncio
async def test_client_revoke_cannot_disable_a_colliding_host(monkeypatch):
    tid, device, request_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    row = SimpleNamespace(request_id=request_id, state={"tokens":["host-token"]}, revoked=False)
    class Session:
        async def scalar(self, *_): return row
        async def execute(self, *_): raise AssertionError("Client revocation must not touch host connections")
    @asynccontextmanager
    async def transaction(_): yield Session()
    monkeypatch.setattr(routes, 'transaction', transaction)
    monkeypatch.setattr(routes, 'actor', lambda _: (tid, 'local'))
    body = routes.Revoke(device_id=device, request_id=request_id)
    with pytest.raises(HTTPException) as denied:
        await routes.revoke_client(None, body, Response())
    assert denied.value.status_code == 404
    assert not row.revoked and row.state['tokens'] == ['host-token']
    row.state['owner_attested_client'] = True
    assert (await routes.revoke_client(None, body, Response()))['revoked']
    assert row.revoked and row.state['tokens'] == []
    assert (await routes.revoke_client(None, body, Response()))['revoked']
