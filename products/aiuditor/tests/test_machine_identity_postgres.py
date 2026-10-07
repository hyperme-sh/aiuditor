"""Actual SQL concurrency and independent contract revocation at the authority."""

import asyncio
import base64
import hashlib
import time
import uuid
from datetime import UTC, datetime, timedelta
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from controlplane_db.models import Tenant
from aiuditor_service import machine_identity as service, machine_proof
from aiuditor_service.models import AiuditorConnection, AiuditorContract
from test_machine_proof import proof
from machine_postgres import (
    require_docker,
    postgres_container,
    postgres_sync_url,
    migrated_postgres,
    db_async_url,
    async_engine,
)

pytestmark = pytest.mark.integration


async def test_binding_rotation_contract_isolation_and_revocation(
    async_engine, monkeypatch
):
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    monkeypatch.setattr(service, "get_session_maker", lambda: maker)
    monkeypatch.setenv("AIUDITOR_INTERNAL_TOKEN", "issuer-test-secret")
    tid, device, registration = (uuid.uuid4() for _ in range(3))
    async with maker() as s:
        s.add(Tenant(id=tid, name=str(tid), is_active=True))
        await s.commit()
    app = FastAPI()
    app.include_router(service.router, prefix="/api/v1")
    headers = {
        "X-Tenant-ID": str(tid),
        "X-Control-Plane-ID": "local",
        "X-Internal-Token": "issuer-test-secret",
    }
    keys = [Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()]

    def signed(key):
        return proof(
            key, tid, now=int(time.time()), device=device, request=registration
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://issuer.test"
    ) as client:

        async def post(path, body, **kw):
            return await client.post(
                "/api/v1/aiuditor/machine/" + path,
                headers=kw.get("headers", headers),
                json=body,
            )

        proofs = [signed(k) for k in keys]
        results = await asyncio.gather(
            *(post("bind", p.model_dump(mode="json")) for p in proofs)
        )
        assert sorted(r.status_code for r in results) == [200, 409], [
            r.text for r in results
        ]
        winner = next(i for i, r in enumerate(results) if r.status_code == 200)
        key = keys[winner]
        token = results[winner].json()["access_token"]
        assert (
            await post("token", proofs[winner].model_dump(mode="json"))
        ).status_code == 401
        # No control-plane service token or human login is needed for renewal.
        direct = await post(
            "renew",
            signed(key).model_dump(mode="json"),
            headers={"X-Tenant-ID": str(tid)},
        )
        assert direct.status_code == 200, direct.text
        unknown = await post(
            "renew",
            signed(keys[1 - winner]).model_dump(mode="json"),
            headers={"X-Tenant-ID": str(tid)},
        )
        assert unknown.status_code == 401, unknown.text
        lookup = {
            "device_id": str(device),
            "request_id": str(registration),
            "digest": hashlib.sha256(token.encode()).hexdigest(),
        }
        assert (await post("introspect", lookup)).json() == {"active": True}
        assert (
            await post(
                "introspect", lookup, headers={**headers, "X-Control-Plane-ID": "other"}
            )
        ).status_code == 401
        monkeypatch.setenv("AIUDITOR_ID", "another-authority")
        assert (await post("introspect", lookup)).json() == {"active": False}
        monkeypatch.setenv("AIUDITOR_ID", "local")
        wrong = signed(key)
        wrong.authority_id = "other"
        wrong.signature = base64.b64encode(
            key.sign(machine_proof.message(tid, wrong))
        ).decode()
        assert (await post("token", wrong.model_dump(mode="json"))).status_code == 403
        now = datetime.now(UTC)
        conn = uuid.uuid4()
        contracts = [uuid.uuid4(), uuid.uuid4()]
        async with maker() as s:
            s.add(
                AiuditorConnection(
                    id=conn,
                    tenant_id=tid,
                    kind="host",
                    purpose="lease",
                    device_id=device,
                    status="ready",
                    created_at=now,
                )
            )
            for cid in contracts:
                s.add(
                    AiuditorContract(
                        id=cid,
                        tenant_id=tid,
                        parties=[],
                        meters=["wall_time"],
                        rail="stripe",
                        until=now + timedelta(hours=1),
                        connection_id=conn,
                        status="signed",
                        created_at=now,
                    )
                )
            await s.commit()
        grants = []
        for cid in contracts:
            p = service.ContractProof(**signed(key).model_dump(), contract_id=cid)
            p.signature = base64.b64encode(
                key.sign(machine_proof.message(tid, p))
            ).decode()
            r = await post("contract-token", p.model_dump(mode="json"))
            assert r.status_code == 200, r.text
            grants.append(r.json()["access_token"])

        async def check(i, cid=None):
            return (
                await post(
                    "contract-introspect",
                    {
                        **lookup,
                        "digest": hashlib.sha256(grants[i].encode()).hexdigest(),
                        "contract_id": str(cid or contracts[i]),
                    },
                )
            ).json()["active"]

        assert await check(0) and await check(1)
        assert not await check(0, contracts[1])
        async with maker() as s:
            row = await s.get(AiuditorContract, contracts[0])
            row.status = "suspended"
            await s.commit()
        assert not await check(0) and await check(1)
        assert not (
            await post(
                "introspect",
                {**lookup, "digest": hashlib.sha256(grants[1].encode()).hexdigest()},
            )
        ).json()["active"]
        assert (
            await post(
                "revoke", {"device_id": str(device), "request_id": str(registration)}
            )
        ).status_code == 200
        assert not await check(1)
        assert not (await post("introspect", lookup)).json()["active"]
        assert (
            await post("token", signed(key).model_dump(mode="json"))
        ).status_code == 410
