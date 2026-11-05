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
