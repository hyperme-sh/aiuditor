"""Model cert program: envelopes, core suite, admission."""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from aiuditor_service.credentials import credential_ref
from aiuditor_service.domain.models import (
    AdmissionCreate,
    AdmissionResolve,
    AdmissionStatus,
    AdmissionVerdict,
    ClassEnvelopeCreate,
    SuiteMemberCreate,
)
from aiuditor_service.events import (
    ADMISSION_RESOLVED_V1,
    ADMISSION_SUBMITTED_V1,
    ENVELOPE_RECORDED_V1,
    SUITE_MEMBER_RECORDED_V1,
)
from conftest import tenant_session_headers
from aiuditor_service.program import (
    ADMISSION_ALREADY_RESOLVED,
    CATALOG_LLAMA_8B,
    CORE_SUITE_SEED,
    PACK_ID,
    put_envelope,
    put_suite_member,
    resolve_admission,
    seed_core_suite,
    submit_admission,
)
from aiuditor_service.role import WITNESS_CANNOT_WRITE_GRANT, AiuditorRole
from aiuditor_service.store import AdmissionStore, EnvelopeStore, SuiteStore


@pytest.fixture
def tenant_headers(tenant_a):
    return tenant_session_headers(
        tenant_a,
        permissions=["operator:console", "operator:program"],
    )


def test_envelope_upserts_per_class_and_catalog():
    store = EnvelopeStore()
    tenant = uuid.uuid4()
    first = put_envelope(
        store,
        tenant,
        ClassEnvelopeCreate(
            class_id="agx-large",
            catalog_id=CATALOG_LLAMA_8B,
            file_hash="sha256:abc",
            quant="Q4",
            runtime="nvidia",
            context=8192,
            fit=True,
            decode_tok_s=12.5,
            thermal_c=51.0,
        ),
    )
    second = put_envelope(
        store,
        tenant,
        ClassEnvelopeCreate(
            class_id="agx-large",
            catalog_id=CATALOG_LLAMA_8B,
            file_hash="sha256:def",
            quant="Q4",
            runtime="nvidia",
            context=8192,
            fit=True,
            decode_tok_s=14.0,
            thermal_c=52.0,
        ),
    )
    assert first.id == second.id
    assert second.file_hash == "sha256:def"
    assert second.decode_tok_s == 14.0
    assert len(store.list_for(tenant, class_id="agx-large")) == 1


def test_core_suite_seed_is_pack_v1_llama_on_public_classes():
    store = SuiteStore()
    tenant = uuid.uuid4()
    rows = seed_core_suite(store, tenant)
    assert len(rows) == 2
    assert {(row.class_id, row.catalog_id, row.pack_id) for row in rows} == set(
        CORE_SUITE_SEED
    )
    again = seed_core_suite(store, tenant)
    assert {row.id for row in again} == {row.id for row in rows}
    assert PACK_ID == "pack-v1"
    assert CATALOG_LLAMA_8B == "llama-3.1-8b-q4"


def test_admission_holds_for_named_verdict():
    from aiuditor_service.admission import record_sandbox_result
    from aiuditor_service.domain.models import AdmissionSandboxReport

    store = AdmissionStore()
    tenant = uuid.uuid4()
    row = submit_admission(
        store,
        tenant,
        AdmissionCreate(
            file_hash="sha256:upload",
            format="gguf",
            quant="Q4",
            license="llama3",
            intended_class="agx-large",
            credential="sk-publisher-secret",
        ),
    )
    assert row.status is AdmissionStatus.SANDBOXING
    assert row.verdict is None
    assert row.credential_ref == credential_ref("sk-publisher-secret")
    assert "sk-publisher" not in str(row.model_dump())
    held = record_sandbox_result(
        store,
        tenant,
        row.id,
        AdmissionSandboxReport(
            soak_passed=True,
            egress_ok=True,
            egress_reason="overlay_publish_only",
        ),
        hold_for_human=True,
    )
    assert held.status is AdmissionStatus.HOLD
    allowed = resolve_admission(
        store,
        tenant,
        held.id,
        AdmissionResolve(verdict=AdmissionVerdict.ALLOW, reason="lab soak green"),
        resolved_by="11111111-1111-1111-1111-111111111111",
    )
    assert allowed.status is AdmissionStatus.ALLOWED
    assert allowed.verdict is AdmissionVerdict.ALLOW
    with pytest.raises(HTTPException) as exc:
        resolve_admission(
            store,
            tenant,
            held.id,
            AdmissionResolve(verdict=AdmissionVerdict.DENY),
            resolved_by="22222222-2222-2222-2222-222222222222",
        )
    assert exc.value.detail == ADMISSION_ALREADY_RESOLVED


@pytest.mark.asyncio
async def test_program_http_and_hydrate(
    http_client, tenant_headers, tenant_a, event_sink, monkeypatch
):
    from test_lab_reporting import report, signed, KEY
    monkeypatch.setenv("AIUDITOR_LAB_REPORT_KEY", KEY)
    body = report(tenant_a)
    body['envelope'].update(catalog_id=CATALOG_LLAMA_8B, decode_tok_s=12.5, context=8192)
    envelope = await http_client.post("/api/v1/aiuditor/envelopes", **signed(body))
    assert envelope.status_code == 201, envelope.text
    seeded = await http_client.post(
        "/api/v1/aiuditor/suite/seed", headers=tenant_headers
    )
    assert seeded.status_code == 201, seeded.text
    assert len(seeded.json()) == 2
    admission = await http_client.post(
        "/api/v1/aiuditor/admissions",
        headers=tenant_headers,
        json={
            "file_hash": "sha256:upload-2",
            "format": "gguf",
            "license": "mit",
            "intended_class": "nx-volume",
            "credential": "publisher-token",
        },
    )
    assert admission.status_code == 201, admission.text
    assert admission.json()["status"] == "sandboxing"
    assert admission.json()["credential_ref"] == credential_ref("publisher-token")
    assert "credential" not in admission.json() or admission.json().get("credential") is None
    verdict = await http_client.post(
        f"/api/v1/aiuditor/admissions/{admission.json()['id']}/verdict",
        headers=tenant_headers,
        json={"verdict": "deny", "reason": "license hold"},
    )
    assert verdict.status_code == 200, verdict.text
    assert verdict.json()["status"] == "denied"

    listed_env = await http_client.get(
        "/api/v1/aiuditor/envelopes",
        headers=tenant_headers,
        params={"class_id": "agx-large"},
    )
    listed_suite = await http_client.get(
        "/api/v1/aiuditor/suite",
        headers=tenant_headers,
        params={"class_id": "agx-large"},
    )
    assert listed_env.status_code == 200
    assert listed_suite.status_code == 200
    assert listed_env.json()[0]["decode_tok_s"] == 12.5
    assert listed_suite.json()[0]["catalog_id"] == CATALOG_LLAMA_8B

    hydrated = await http_client.get("/api/v1/aiuditor/hydrate", headers=tenant_headers)
    assert hydrated.status_code == 200, hydrated.text
    body = hydrated.json()
    assert len(body["envelopes"]) == 1
    assert len(body["suite_members"]) == 2
    assert len(body["admissions"]) == 1
    assert body["admissions"][0]["verdict"] == "deny"
    dumped = str(body)
    assert "publisher-token" not in dumped
    # Field name prompt_class may appear on approval inbox rows; bodies must not.
    assert "messages" not in dumped
    assert "'prompt':" not in dumped and '"prompt":' not in dumped

    types = {item["event_type"] for item in event_sink}
    assert ENVELOPE_RECORDED_V1 in types
    assert SUITE_MEMBER_RECORDED_V1 in types
    assert ADMISSION_SUBMITTED_V1 in types
    assert ADMISSION_RESOLVED_V1 in types


@pytest.mark.asyncio
async def test_witness_cannot_write_program(
    event_sink, host_lookup, plane_hop, provider_hop, tenant_a, tenant_headers, monkeypatch
):
    from asgi_lifespan import LifespanManager
    import httpx

    from aiuditor_service.app import create_app
    from api.core import config as api_config

    monkeypatch.setattr(
        api_config.settings,
        "database_url",
        "postgresql+asyncpg://x:x@localhost:1/x",
    )
    app = create_app(
        test_event_sink=event_sink,
        enable_db_middleware=False,
        host_lookup=host_lookup,
        plane_hop=plane_hop,
        provider_hop=provider_hop,
        role=AiuditorRole.WITNESS,
    )
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            from test_lab_reporting import report
            denied = await client.post("/api/v1/aiuditor/envelopes",json=report(tenant_a),headers=tenant_headers)
            suite = await client.post(
                "/api/v1/aiuditor/suite/seed", headers=tenant_headers
            )
            admit = await client.post(
                "/api/v1/aiuditor/admissions",
                headers=tenant_headers,
                json={
                    "file_hash": "sha256:y",
                    "format": "gguf",
                    "license": "mit",
                    "intended_class": "agx-large",
                },
            )
    assert denied.status_code == 403
    assert denied.json()["detail"] == WITNESS_CANNOT_WRITE_GRANT
    assert suite.status_code == 403
    assert admit.status_code == 403


def test_suite_member_put_is_idempotent():
    store = SuiteStore()
    tenant = uuid.uuid4()
    a = put_suite_member(
        store,
        tenant,
        SuiteMemberCreate(
            class_id="agx-large", catalog_id=CATALOG_LLAMA_8B, pack_id=PACK_ID
        ),
    )
    b = put_suite_member(
        store,
        tenant,
        SuiteMemberCreate(
            class_id="agx-large", catalog_id=CATALOG_LLAMA_8B, pack_id=PACK_ID
        ),
    )
    assert a.id == b.id
    assert len(store.list_for(tenant)) == 1


def test_conditional_creation_loses_race_without_overwriting_or_caching(monkeypatch):
    from aiuditor_service.persist import MemoryPersistSession
    persist = MemoryPersistSession()
    store, competing = EnvelopeStore(persist=persist), EnvelopeStore(persist=persist)
    tenant = uuid.uuid4()
    body = ClassEnvelopeCreate(class_id="agx-large", catalog_id="model", file_hash="sha256:"+"a"*64, runtime="nvidia", decode_tok_s=10)
    original = persist.create_envelope
    winner = []
    def raced(payload):
        winner.append(put_envelope(competing, tenant, body.model_copy(update={"decode_tok_s":23})))
        return original(payload)
    monkeypatch.setattr(persist,"create_envelope",raced)
    with pytest.raises(HTTPException) as conflict:
        put_envelope(store,tenant,body,if_absent=True)
    assert conflict.value.status_code==409
    assert store.envelopes.get(tenant,{})=={}
    assert store.for_class_catalog(tenant,"agx-large","model").id==winner[0].id
    assert store.for_class_catalog(tenant,"agx-large","model").decode_tok_s==23


def test_conditional_creation_retains_first_reference():
    store=EnvelopeStore();tenant=uuid.uuid4()
    body=ClassEnvelopeCreate(class_id="agx-large",catalog_id="model",file_hash="sha256:"+"a"*64,runtime="nvidia")
    first=put_envelope(store,tenant,body,if_absent=True)
    with pytest.raises(HTTPException):put_envelope(store,tenant,body,if_absent=True)
    assert store.for_class_catalog(tenant,"agx-large","model").id==first.id

@pytest.mark.asyncio
@pytest.mark.parametrize("path",["/api/v1/aiuditor/envelopes", "/api/v1/aiuditor/envelopes/initial"])
async def test_http_conditional_envelope_rejects_existing_reference(http_client,tenant_headers,path,tenant_a,monkeypatch):
    from test_lab_reporting import report, signed, KEY
    monkeypatch.setenv("AIUDITOR_LAB_REPORT_KEY",KEY)
    body=report(tenant_a);body['envelope'].update(catalog_id='first-reference',decode_tok_s=12)
    req=signed(body);req['headers']['If-None-Match']='*'
    first=await http_client.post(path,**req)
    assert first.status_code==201,first.text
    changed={**body,'job_id':str(uuid.uuid4()),'envelope':{**body['envelope'],'decode_tok_s':99}}
    req=signed(changed);req['headers']['If-None-Match']='*'
    second=await http_client.post(path,**req)
    assert second.status_code==409
    rows=(await http_client.get("/api/v1/aiuditor/envelopes",headers=tenant_headers)).json()
    assert next(row for row in rows if row["id"]==first.json()["id"])["decode_tok_s"]==12
