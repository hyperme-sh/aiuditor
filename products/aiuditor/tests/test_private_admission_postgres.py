"""Scoped admission has no public promotion and requires a live human approver."""

import asyncio
import uuid

import httpx
import pytest
from aiuditor_service import middleware
from aiuditor_service.middleware import AiuditorServiceDbMiddleware
from aiuditor_service.private_models import routes
from aiuditor_service.private_models.contracts import canonical_hash
from controlplane_db.models import Group, Role, Tenant, User, UserGroupMembership
from controlplane_identity.security.tenant_session_jwt import (
    mint_host_setup_session,
    mint_tenant_session_jwt,
)
from fastapi import FastAPI
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker

pytest_plugins = ["machine_postgres"]
pytestmark = pytest.mark.integration
INTERNAL = "/api/v1/aiuditor/internal/private-model-admissions"
HUMAN = "/api/v1/aiuditor/private-model-admissions"


@pytest.fixture
async def plane(async_engine, monkeypatch):
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    monkeypatch.setattr(routes, "get_session_maker", lambda: maker)
    monkeypatch.setenv("AIUDITOR_MODEL_ADMISSION_TOKEN", "a" * 32)
    app = FastAPI()
    # Exercise the real middleware: the new purpose token is scoped to these
    # internal routes, while human routes still require normal tenant binding.
    monkeypatch.setattr(middleware, "get_session_maker", lambda: maker)
    app.add_middleware(AiuditorServiceDbMiddleware)
    app.include_router(routes.router, prefix="/api/v1")
    tenant = uuid.uuid4()
    async with maker() as session:
        session.add(Tenant(id=tenant, name=str(tenant), is_active=True))
        await session.commit()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://auditor.test"
    ) as client:
        yield (
            client,
            maker,
            tenant,
            {"X-Tenant-ID": str(tenant), "X-Model-Admission-Token": "a" * 32},
        )


async def human(maker, tenant, role="auditor", subject=None):
    subject = subject or str(uuid.uuid4())
    user_id, role_id, group_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with maker() as session:
        session.add(
            Role(id=role_id, tenant_id=tenant, name=str(role_id), permissions=[])
        )
        session.add(
            User(
                id=user_id,
                tenant_id=tenant,
                keycloak_sub=subject,
                email=f"{user_id}@example.test",
                first_name="Review",
                last_name="Human",
                gender="unspecified",
                is_active=True,
            )
        )
        await session.flush()
        session.add(Group(id=group_id, tenant_id=tenant, name=role, role_id=role_id))
        await session.flush()
        session.add(
            UserGroupMembership(tenant_id=tenant, user_id=user_id, group_id=group_id)
        )
        await session.commit()
    token = mint_tenant_session_jwt(
        claims={"sub": subject, "azp": "controlplane-frontend"}, tenant_id=tenant
    )
    return {"Authorization": f"Bearer {token}", "X-Tenant-ID": str(tenant)}, user_id


def declaration():
    configuration = {"context_tokens": 4096, "jinja": False, "timeout_seconds": 120}
    return {
        "admission_id": str(uuid.uuid4()),
        "lab_job_id": str(uuid.uuid4()),
        "lab_device_id": str(uuid.uuid4()),
        "target": {
            "revision_id": str(uuid.uuid4()),
            "format": "gguf",
            "owner_sub": str(uuid.uuid4()),
            "class_id": "agx-large",
            "backend": "cuda-jetson",
            "runtime": "nvidia",
            "runtime_image_digest": "ghcr.io/fyberlabs/hypermesh-llama@sha256:"
            + "c" * 64,
            "configuration": configuration,
            "configuration_sha256": canonical_hash(configuration),
            "manifest_sha256": "a" * 64,
            "inspection_sha256": "b" * 64,
            "file_sha256": "d" * 64,
            "size_bytes": 10,
            "license": "MIT",
        },
    }


def evidence(body):
    target = body["target"]
    return {
        "target_sha256": canonical_hash(target),
        "lab_job_id": body["lab_job_id"],
        "lab_device_id": body["lab_device_id"],
        "observed_class_id": target["class_id"],
        "file_sha256": target["file_sha256"],
        "backend": target["backend"],
        "runtime_image_digest": target["runtime_image_digest"],
        "configuration_sha256": target["configuration_sha256"],
        "soak_passed": True,
        "egress_ok": True,
        "egress_reason": "overlay_publish_only",
    }


async def test_exact_target_and_human_hold_survive_retries_and_live_role_revocation(
    plane,
):
    client, maker, tenant, service = plane
    actor, user_id = await human(maker, tenant)
    body = declaration()
    results = await asyncio.gather(
        *(client.post(INTERNAL, headers=service, json=body) for _ in range(3))
    )
    assert [result.status_code for result in results] == [200, 200, 200]
    row = results[0].json()
    assert all(result.json() == row for result in results)
    path = f"{INTERNAL}/{body['admission_id']}"
    decision = f"{HUMAN}/{body['admission_id']}/verdict"
    assert (
        await client.post(
            decision, headers=actor, json={"verdict": "allow", "reason": "reviewed"}
        )
    ).status_code == 409
    report = evidence(body)
    for key, changed in [
        ("observed_class_id", "nx-volume"),
        ("configuration_sha256", "e" * 64),
        ("file_sha256", "e" * 64),
        ("runtime_image_digest", "changed"),
        ("lab_job_id", str(uuid.uuid4())),
    ]:
        assert (
            await client.post(
                path + "/sandbox", headers=service, json={**report, key: changed}
            )
        ).status_code == 409
    held = await client.post(path + "/sandbox", headers=service, json=report)
    assert held.status_code == 200 and held.json()["status"] == "hold"
    assert (
        await client.post(path + "/sandbox", headers=service, json=report)
    ).json() == held.json()
    assert (
        await client.post(
            decision, headers=service, json={"verdict": "allow", "reason": "reviewed"}
        )
    ).status_code == 401
    async with maker() as session:
        await session.execute(
            delete(UserGroupMembership).where(UserGroupMembership.user_id == user_id)
        )
        await session.commit()
    assert (
        await client.post(
            decision, headers=actor, json={"verdict": "allow", "reason": "reviewed"}
        )
    ).status_code == 403
    admin, _ = await human(maker, tenant, role="admin")
    allowed = await client.post(
        decision, headers=admin, json={"verdict": "allow", "reason": "reviewed"}
    )
    assert allowed.status_code == 200 and allowed.json()["status"] == "allowed"
    assert allowed.json()["audit"][0]["resolved_by"]
    assert (
        await client.post(f"{HUMAN}/{body['admission_id']}/revoke", headers=admin)
    ).json()["status"] == "revoked"
    assert (await client.get(path, headers=service)).json()["status"] == "revoked"


async def test_missing_egress_never_advances_and_changed_scope_never_reuses_approval(
    plane,
):
    client, maker, tenant, service = plane
    actor, _ = await human(maker, tenant)
    body = declaration()
    assert (await client.post(INTERNAL, headers=service, json=body)).status_code == 200
    changed = {**body, "target": {**body["target"], "class_id": "nx-volume"}}
    assert (
        await client.post(INTERNAL, headers=service, json=changed)
    ).status_code == 409
    report = evidence(body)
    report.pop("egress_ok")
    result = await client.post(
        f"{INTERNAL}/{body['admission_id']}/sandbox", headers=service, json=report
    )
    assert result.json()["status"] == "failed"
    assert (
        await client.post(
            f"{HUMAN}/{body['admission_id']}/verdict",
            headers=actor,
            json={"verdict": "allow", "reason": "reviewed"},
        )
    ).status_code == 409
    bad = declaration()
    bad["target"]["size_bytes"] = 50_000_000_001
    assert (await client.post(INTERNAL, headers=service, json=bad)).status_code == 422
    other_tenant = str(uuid.uuid4())
    assert (
        await client.get(
            f"{INTERNAL}/{body['admission_id']}",
            headers={**service, "X-Tenant-ID": other_tenant},
        )
    ).status_code == 404


async def test_renter_setup_service_and_model_subjects_cannot_resolve(plane):
    client, maker, tenant, service = plane
    renter, _ = await human(maker, tenant, role="renter")
    model, _ = await human(maker, tenant, role="auditor", subject="llm")
    body = declaration()
    await client.post(INTERNAL, headers=service, json=body)
    path = f"{HUMAN}/{body['admission_id']}/verdict"
    decision = {"verdict": "deny", "reason": "reviewed"}
    assert (await client.post(path, headers=renter, json=decision)).status_code == 403
    assert (await client.post(path, headers=model, json=decision)).status_code == 403
    assert (await client.post(INTERNAL, headers=renter, json=body)).status_code == 401
    token = mint_host_setup_session(
        claims={"sub": "installer", "azp": "hypermesh-host-setup"}, tenant_id=tenant
    )
    assert (
        await client.post(
            path,
            headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": str(tenant)},
            json=decision,
        )
    ).status_code == 401
