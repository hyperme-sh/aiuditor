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
