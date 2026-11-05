"""Cert-suite edits require operator:program. Support cannot edit the suite."""

from __future__ import annotations

import pytest

from conftest import tenant_session_headers


@pytest.mark.asyncio
async def test_support_cannot_edit_suite_and_program_can(http_client, tenant_a):
    support = tenant_session_headers(
        tenant_a,
        permissions=["operator:console", "operator:support"],
    )
    denied = await http_client.post(
        "/api/v1/aiuditor/suite",
        headers=support,
        json={
            "class_id": "agx-large",
            "catalog_id": "llama-3.1-8b-q4",
            "pack_id": "pack-v1",
        },
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "operator:program required"

    program = tenant_session_headers(
        tenant_a,
        permissions=["operator:console", "operator:program"],
    )
    created = await http_client.post(
        "/api/v1/aiuditor/suite",
        headers=program,
        json={
            "class_id": "agx-large",
            "catalog_id": "llama-extra",
            "pack_id": "pack-v1",
        },
    )
    assert created.status_code == 201, created.text


async def test_suite_replacement_preserves_class_tenant_and_configuration_guards(http_client, tenant_a, app):
    import uuid
    from aiuditor_service.role import AiuditorRole
    from aiuditor_service.program_bootstrap import bootstrap_core_suite
    headers = tenant_session_headers(tenant_a, permissions=["operator:console", "operator:program"])
    response = await http_client.post("/api/v1/aiuditor/suite",headers=headers,json={"class_id":"nx-volume","catalog_id":"legacy-nx","pack_id":"pack-v1"})
    assert response.status_code == 201
    original = response.json();path = "/api/v1/aiuditor/suite/"+original["id"]
    body = {"class_id":"nx-volume","catalog_id":"llama-3.1-8b-q4-nx","pack_id":"pack-v1","expected_catalog_id":"legacy-nx","expected_pack_id":"pack-v1"}
    support = tenant_session_headers(tenant_a,permissions=["operator:console","operator:support"])
    assert (await http_client.put(path,headers=support,json=body)).status_code == 403
    wrong_tenant = tenant_session_headers(uuid.uuid4(),permissions=["operator:console","operator:program"])
    assert (await http_client.put(path,headers=wrong_tenant,json=body)).status_code == 404
    assert (await http_client.put(path,headers=headers,json={**body,"class_id":"agx-large"})).status_code == 409
    assert (await http_client.put(path,headers=headers,json={**body,"expected_catalog_id":"stale"})).status_code == 409
    result = await http_client.put(path,headers=headers,json=body)
    assert result.status_code == 200,result.text
    assert result.json()["id"] == original["id"]
    assert (await http_client.put(path,headers=headers,json=body)).json() == result.json()
    suite = app.state.suite_store
    assert len(suite.list_for(tenant_a,class_id="nx-volume")) == 1
    bootstrap_core_suite(suite,tenant_a)
    assert [row.catalog_id for row in suite.list_for(tenant_a,class_id="nx-volume")] == [body["catalog_id"]]
    app.state.aiuditor_role = AiuditorRole.WITNESS
    assert (await http_client.put(path,headers=headers,json={**body,"catalog_id":"forged"})).status_code == 403
    assert [row.catalog_id for row in suite.list_for(tenant_a,class_id="nx-volume")] == [body["catalog_id"]]


async def test_suite_pack_only_replacement_and_duplicate_target(http_client, tenant_a):
    headers=tenant_session_headers(tenant_a,permissions=["operator:console","operator:program"])
    async def create(catalog):
        response=await http_client.post("/api/v1/aiuditor/suite",headers=headers,json={"class_id":"nx-volume","catalog_id":catalog,"pack_id":"pack-v1"})
        assert response.status_code==201
        return response.json()
    first=await create("model-a")
    second=await create("model-b")
    path="/api/v1/aiuditor/suite/"+first["id"]
    body={"class_id":"nx-volume","catalog_id":"model-a","pack_id":"thin-v1","expected_catalog_id":"model-a","expected_pack_id":"pack-v1"}
    response=await http_client.put(path,headers=headers,json=body)
    assert response.status_code==200,response.text
    assert response.json()["pack_id"]=="thin-v1"
    assert (await http_client.put(path,headers=headers,json=body)).status_code==200
    assert (await http_client.put(path,headers=headers,json={**body,"pack_id":"other"})).status_code==409
    conflict={**body,"catalog_id":second["catalog_id"],"expected_pack_id":"thin-v1"}
    assert (await http_client.put(path,headers=headers,json=conflict)).status_code==409


@pytest.mark.parametrize("scopes",[[],["operator:program"]])
async def test_generic_service_exchange_cannot_replace_reviewed_suite(http_client,tenant_a,scopes):
    import uuid
    from controlplane_identity.security.api_key_exchange_jwt import mint_api_key_session_jwt
    operator=tenant_session_headers(tenant_a,permissions=["operator:console","operator:program"])
    original={"class_id":"nx-volume","catalog_id":"legacy-nx","pack_id":"pack-v1"}
    response=await http_client.post("/api/v1/aiuditor/suite",headers=operator,json=original)
    assert response.status_code==201
    token=mint_api_key_session_jwt(key_record_id=uuid.uuid4(),tenant_id=tenant_a,scopes=scopes,purpose="service")
    service={"Authorization":"Bearer "+token,"X-Tenant-ID":str(tenant_a)}
    assert (await http_client.get("/api/v1/aiuditor/suite",headers=service)).status_code==200
    body={**original,"catalog_id":"forged","expected_catalog_id":"legacy-nx","expected_pack_id":"pack-v1"}
    assert (await http_client.put("/api/v1/aiuditor/suite/"+response.json()["id"],headers=service,json=body)).status_code==403
    rows=(await http_client.get("/api/v1/aiuditor/suite",headers=operator)).json()
    assert len(rows)==1 and rows[0]["catalog_id"]=="legacy-nx"


async def test_service_default_seed_cannot_edit_or_add_to_reviewed_program(http_client,tenant_a):
    import uuid
    from controlplane_identity.security.api_key_exchange_jwt import mint_api_key_session_jwt
    operator=tenant_session_headers(tenant_a,permissions=["operator:console","operator:program"])
    original={"class_id":"nx-volume","catalog_id":"reviewed-model","pack_id":"thin-v1"}
    response=await http_client.post("/api/v1/aiuditor/suite",headers=operator,json=original)
    assert response.status_code==201
    token=mint_api_key_session_jwt(key_record_id=uuid.uuid4(),tenant_id=tenant_a,scopes=["operator:program"],purpose="service")
    service={"Authorization":"Bearer "+token,"X-Tenant-ID":str(tenant_a)}
    assert (await http_client.post("/api/v1/aiuditor/suite",headers=service,json={**original,"pack_id":"pack-v1"})).status_code==403
    assert (await http_client.post("/api/v1/aiuditor/suite",headers=service,json={**original,"catalog_id":"second-model"})).status_code==403
    seeded=await http_client.post("/api/v1/aiuditor/suite/seed",headers=service)
    assert seeded.status_code==201,seeded.text
    rows=(await http_client.get("/api/v1/aiuditor/suite",headers=service)).json()
    nx=[row for row in rows if row['class_id']=='nx-volume']
    assert len(nx)==1 and nx[0]['id']==response.json()['id']
    assert nx[0]['catalog_id']=='reviewed-model' and nx[0]['pack_id']=='thin-v1'
