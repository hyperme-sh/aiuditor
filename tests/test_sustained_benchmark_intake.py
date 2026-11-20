"""Unit contracts for scheduling declared sustained-decode lab work."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from aiuditor_service.domain.models import SUSTAINED_MEASUREMENT_PROTOCOL
from aiuditor_service.hypermesh import HostLookupUnavailable, HttpPlaneHop
from aiuditor_service.intake_loop import queue_missing_sustained_baselines


async def test_intake_only_queues_declared_core_references_without_verified_sustained_rate():
    tenant = uuid.uuid4()
    pending = SimpleNamespace(class_id="nx-volume", catalog_id="core")
    ready = SimpleNamespace(
        class_id="agx-large", catalog_id="core", measurement_verified=True, fit=True,
    )
    suites = Mock()
    suites.list_for.return_value = [pending, ready]
    envelopes = Mock()
    envelopes.list_for.return_value = [ready]
    plane = Mock()
    plane.queue_sustained_lab_soak = AsyncMock(return_value=uuid.uuid4())

    assert await queue_missing_sustained_baselines(tenant, suites, envelopes, plane) == (1, 0)

    envelopes.list_for.assert_called_once_with(
        tenant, measurement_protocol=SUSTAINED_MEASUREMENT_PROTOCOL,
    )
    plane.queue_sustained_lab_soak.assert_awaited_once_with(
        tenant, class_id="nx-volume", catalog_id="core",
    )


@pytest.mark.parametrize("status", [201, 404, 409, 503])
async def test_new_plane_hop_is_scoped_and_old_plane_has_no_legacy_fallback(monkeypatch, status):
    tenant, job = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setenv("HYPERMESH_INTERNAL_TOKEN", "fixture-owner-token")
    requests = []

    async def post(url, *, headers, json):
        requests.append((url, headers, json))
        return httpx.Response(
            status, request=httpx.Request("POST", url),
            json={"measurement_protocol": SUSTAINED_MEASUREMENT_PROTOCOL, "job_id": str(job)},
        )

    client = AsyncMock()
    client.__aenter__.return_value = SimpleNamespace(post=post)
    monkeypatch.setattr("aiuditor_service.hypermesh.httpx.AsyncClient", Mock(return_value=client))
    hop = HttpPlaneHop("https://plane.example")

    if status == 503:
        with pytest.raises(HostLookupUnavailable):
            await hop.queue_sustained_lab_soak(tenant, class_id="nx-volume", catalog_id="core")
    else:
        selected = await hop.queue_sustained_lab_soak(tenant, class_id="nx-volume", catalog_id="core")
        assert selected == (job if status == 201 else None)

    [(url, headers, payload)] = requests
    assert url == "https://plane.example/api/v1/hypermesh/lab/soaks/sustained-decode"
    assert headers["X-Tenant-ID"] == str(tenant)
    assert payload == {
        "class_id": "nx-volume", "catalog_id": "core",
        "measurement_protocol": SUSTAINED_MEASUREMENT_PROTOCOL,
    }
