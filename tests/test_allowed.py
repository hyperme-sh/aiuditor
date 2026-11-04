"""GET /allowed is the grant set. allocate still binds one host."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from aiuditor_service.allowed import allowed_host_set
from aiuditor_service.domain.models import (
    Connection,
    ConnectionKind,
    ConnectionPurpose,
    ConnectionStatus,
)
from conftest import FYBER_AGX_DEVICE_ID

SECOND = uuid.UUID("a6400000-0640-4000-8000-000000000002")


def _row(
    *,
    kind: ConnectionKind = ConnectionKind.HOST,
    purpose: ConnectionPurpose = ConnectionPurpose.LEASE,
    status: ConnectionStatus = ConnectionStatus.READY,
    device_id: uuid.UUID | None = FYBER_AGX_DEVICE_ID,
) -> Connection:
    return Connection(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        kind=kind,
        purpose=purpose,
        device_id=device_id,
        status=status,
        created_at=datetime.now(UTC),
    )


def test_allowed_set_keeps_lease_hosts_and_drops_the_rest():
    disabled = _row(status=ConnectionStatus.DISABLED, device_id=SECOND)
    auditor = _row(purpose=ConnectionPurpose.AUDITOR)
    provider = _row(kind=ConnectionKind.PROVIDER, device_id=None, purpose=ConnectionPurpose.LEASE)
    provider = provider.model_copy(update={"endpoint": "https://llm.example/v1"})
    live = _row()
    chosen = allowed_host_set([disabled, auditor, provider, live])
    assert [row.id for row in chosen] == [live.id]


async def test_allowed_lists_every_lease_host_without_allocating(
    http_client, tenant_headers, host_lookup, tenant_a
):
    host_lookup.enroll(SECOND, tenant_id=tenant_a)
    for device_id in (FYBER_AGX_DEVICE_ID, SECOND):
        created = await http_client.post(
            "/api/v1/aiuditor/connections",
            headers=tenant_headers,
            json={"kind": "host", "purpose": "lease", "device_id": str(device_id)},
        )
        assert created.status_code == 201, created.text
        assert created.json()["lease_id"] is None
    auditor = await http_client.post(
        "/api/v1/aiuditor/connections",
        headers=tenant_headers,
        json={
            "kind": "host",
            "purpose": "auditor",
            "device_id": str(FYBER_AGX_DEVICE_ID),
        },
    )
    assert auditor.status_code == 201, auditor.text

    allowed = await http_client.get("/api/v1/aiuditor/allowed", headers=tenant_headers)
    assert allowed.status_code == 200, allowed.text
    body = allowed.json()
    assert {row["device_id"] for row in body["hosts"]} == {
        str(FYBER_AGX_DEVICE_ID),
        str(SECOND),
    }
    assert all(row["lease_id"] is None for row in body["hosts"])
    assert "checkout_url" not in allowed.text
    again = await http_client.get("/api/v1/aiuditor/allowed", headers=tenant_headers)
    assert again.json() == body
