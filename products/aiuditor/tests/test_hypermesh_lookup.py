from __future__ import annotations

import uuid

import httpx
import pytest

from aiuditor_service.hypermesh import (
    HOST_LOOKUP_FAILED,
    HOST_LOOKUP_REQUIRED,
    PLANE_HOP_REQUIRED,
    ClosedHostLookup,
    ClosedPlaneHop,
    HttpHostLookup,
    HttpPlaneHop,
    HostLookupUnavailable,
    resolve_host_lookup,
    resolve_plane_hop,
)

DEVICE = uuid.UUID("a6400000-0640-4000-8000-000000000001")
TENANT = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


@pytest.mark.asyncio
async def test_closed_lookup_fail_closes():
    with pytest.raises(HostLookupUnavailable) as exc:
        await ClosedHostLookup().get(TENANT, DEVICE)
    assert str(exc.value) == HOST_LOOKUP_REQUIRED


def test_resolve_empty_url_is_closed():
    assert isinstance(resolve_host_lookup(base_url=""), ClosedHostLookup)
    assert isinstance(resolve_host_lookup(base_url="   "), ClosedHostLookup)


def test_resolve_url_is_http():
    lookup = resolve_host_lookup(base_url="http://hypermesh-service:8000")
    assert isinstance(lookup, HttpHostLookup)


@pytest.mark.asyncio
async def test_http_lookup_reads_enrolled_role_only(monkeypatch):
    captured: dict[str, object] = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {
                "id": str(DEVICE),
                "tenant_id": str(TENANT),
                "enrolled": True,
                "role": "host",
                "class_id": "agx-large",
                "loaded_catalog_id": "llama-3.1-8b-q4",
                "serial": "must-not-be-stored",
                "wg_public_key": "must-not-be-read-as-assignment",
            }

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, headers=None):
            captured["url"] = url
            captured["headers"] = headers
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    lookup = HttpHostLookup("http://hm.example")
    view = await lookup.get(
        TENANT,
        DEVICE,
        headers={"Authorization": "Bearer session"},
    )
    assert view is not None
    assert view.id == DEVICE
    assert view.enrolled is True
    assert view.role == "host"
    assert view.tenant_id == TENANT
    assert view.class_id == "agx-large"
    assert view.loaded_catalog_id == "llama-3.1-8b-q4"
    assert "serial" not in view.__dict__ or not hasattr(view, "serial")
    assert captured["url"] == f"http://hm.example/api/v1/hypermesh/devices/{DEVICE}"
    headers = captured["headers"]
    assert headers["Authorization"] == "Bearer session"
    assert headers["X-Tenant-ID"] == str(TENANT)


@pytest.mark.asyncio
async def test_http_lookup_404_is_miss(monkeypatch):
    class _Resp:
        status_code = 404

        def json(self):
            return {"detail": "Device not found"}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, headers=None):
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    assert await HttpHostLookup("http://hm.example").get(TENANT, DEVICE) is None


@pytest.mark.asyncio
async def test_http_lookup_5xx_fail_closes(monkeypatch):
    class _Resp:
        status_code = 503

        def json(self):
            return {}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, headers=None):
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    with pytest.raises(HostLookupUnavailable) as exc:
        await HttpHostLookup("http://hm.example").get(TENANT, DEVICE)
    assert str(exc.value) == HOST_LOOKUP_FAILED


@pytest.mark.asyncio
async def test_closed_plane_fail_closes():
    with pytest.raises(HostLookupUnavailable) as exc:
        await ClosedPlaneHop().mint_auditor_ticket(TENANT, DEVICE, reserved_hours=1)
    assert str(exc.value) == PLANE_HOP_REQUIRED


def test_resolve_plane_empty_is_closed():
    assert isinstance(resolve_plane_hop(base_url=""), ClosedPlaneHop)


@pytest.mark.asyncio
async def test_http_plane_mints_without_keeping_ticket(monkeypatch):
    captured: dict[str, object] = {}
    lease_id = uuid.uuid4()

    class _Resp:
        status_code = 201

        def json(self):
            return {
                "lease_id": str(lease_id),
                "device_id": str(DEVICE),
                "purpose": "ir",
                "exp": "2026-09-21T16:00:00+00:00",
                "ticket": "hm1.must-not-be-stored",
            }

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    grant = await HttpPlaneHop("http://hm.example").mint_auditor_ticket(
        TENANT, DEVICE, reserved_hours=6, headers={"Authorization": "Bearer session"}
    )
    assert grant.lease_id == lease_id
    assert grant.purpose == "ir"
    assert grant.device_id == DEVICE
    assert not hasattr(grant, "ticket")
    assert captured["url"] == "http://hm.example/api/v1/hypermesh/auditor-tickets"
    assert captured["json"] == {"device_id": str(DEVICE), "reserved_hours": 6}
    assert "hm1.must-not-be-stored" not in str(grant)


@pytest.mark.asyncio
async def test_http_plane_get_lease(monkeypatch):
    lease_id = uuid.uuid4()

    class _Resp:
        status_code = 200

        def json(self):
            return {
                "id": str(lease_id),
                "device_id": str(DEVICE),
                "purpose": "renter",
                "status": "active",
                "tenant_id": str(TENANT),
                "checkout_url": "https://stripe.example/must-not-be-required",
            }

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, headers=None):
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    view = await HttpPlaneHop("http://hm.example").get_lease(TENANT, lease_id)
    assert view is not None
    assert view.purpose == "renter"
    assert view.device_id == DEVICE
    assert not hasattr(view, "checkout_url")


@pytest.mark.asyncio
async def test_http_plane_mint_conflict_and_get_miss(monkeypatch):
    class _Conflict:
        status_code = 409

        def json(self):
            return {"detail": "device has a renter lease"}

    class _Miss:
        status_code = 404

        def json(self):
            return {}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers=None, json=None):
            return _Conflict()

        async def get(self, url, headers=None):
            return _Miss()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    hop = HttpPlaneHop("http://hm.example")
    with pytest.raises(HostLookupUnavailable) as exc:
        await hop.mint_auditor_ticket(TENANT, DEVICE, reserved_hours=1)
    assert str(exc.value) == "device has a renter lease"
    assert await hop.get_lease(TENANT, uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_closed_plane_offer_fail_closes():
    with pytest.raises(HostLookupUnavailable) as exc:
        await ClosedPlaneHop().offer_lease(
            TENANT,
            kind="p1_hardware",
            device_id=DEVICE,
            reserved_hours=1,
            contract_id=uuid.uuid4(),
            class_id="agx-large",
        )
    assert str(exc.value) == PLANE_HOP_REQUIRED


@pytest.mark.asyncio
async def test_http_plane_offers_lease_without_keeping_ticket(monkeypatch):
    captured: dict[str, object] = {}
    lease_id = uuid.uuid4()
    contract_id = uuid.uuid4()
    billing_id = uuid.uuid4()

    class _Resp:
        status_code = 201

        def json(self):
            return {
                "id": str(lease_id),
                "device_id": str(DEVICE),
                "purpose": "renter",
                "contract_id": str(contract_id),
                "billing_record_id": str(billing_id),
                "payment_amount_atoms": 12_000_000,
                "payment_test_credit_available": True,
                "ticket": "hm1.must-not-be-stored",
            }

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    offered = await HttpPlaneHop("http://hm.example").offer_lease(
        TENANT,
        kind="p1_hardware",
        device_id=DEVICE,
        reserved_hours=2,
        contract_id=contract_id,
        class_id="agx-large",
        headers={"Authorization": "Bearer session"},
    )
    assert offered.lease_id == lease_id
    assert offered.purpose == "renter"
    assert offered.billing_record_id == billing_id
    assert offered.payment_amount_atoms == 12_000_000
    assert offered.payment_test_credit_available is True
    assert not hasattr(offered, "checkout_url")
    assert not hasattr(offered, "ticket")
    assert "hm1.must-not-be-stored" not in str(offered)
    assert captured["url"] == "http://hm.example/api/v1/hypermesh/leases"
    assert captured["json"]["kind"] == "p1_hardware"
    assert captured["json"]["class_id"] == "agx-large"
    assert captured["json"]["contract_id"] == str(contract_id)
    assert captured["headers"]["Authorization"] == "Bearer session"


def _capturing_post_client(captured: dict[str, object]):
    class _Resp:
        status_code = 201

        def json(self):
            return {"ok": True, "certified": False}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _Resp()

    return _Client


@pytest.mark.asyncio
async def test_lab_soak_and_promote_send_internal_token(monkeypatch):
    monkeypatch.setenv("HYPERMESH_INTERNAL_TOKEN", "test-hypermesh-internal-token")
    hop = HttpPlaneHop("http://hm.example")
    admission_id = uuid.uuid4()

    soak_captured: dict[str, object] = {}
    monkeypatch.setattr(httpx, "AsyncClient", _capturing_post_client(soak_captured))
    await hop.queue_lab_soak(
        TENANT,
        class_id="agx-large",
        catalog_id="hf-lab-soak",
        artifact_name="Lab soak",
        file_hash="sha256:abc",
        size_bytes=20 * 1024**3,
    )
    assert soak_captured["url"] == "http://hm.example/api/v1/hypermesh/lab/soaks"
    soak_headers = soak_captured["headers"]
    assert soak_headers["X-Internal-Token"] == "test-hypermesh-internal-token"
    assert soak_headers["X-Tenant-ID"] == str(TENANT)
    assert "Authorization" not in soak_headers
    assert soak_captured["json"]["size_bytes"] == 20 * 1024**3

    promote_captured: dict[str, object] = {}
    monkeypatch.setattr(httpx, "AsyncClient", _capturing_post_client(promote_captured))
    await hop.promote_catalog(
        TENANT,
        catalog_id="upload-route-check",
        class_id="agx-large",
        artifact_name="Promoted",
        file_hash="sha256:" + ("ab" * 32),
        admission_id=admission_id,
    )
    assert promote_captured["url"] == "http://hm.example/api/v1/hypermesh/catalog/promote"
    promote_headers = promote_captured["headers"]
    assert promote_headers["X-Internal-Token"] == "test-hypermesh-internal-token"
    assert promote_headers["X-Tenant-ID"] == str(TENANT)
    assert "Authorization" not in promote_headers


@pytest.mark.asyncio
async def test_lab_soak_and_promote_omit_token_when_unset(monkeypatch):
    monkeypatch.delenv("HYPERMESH_INTERNAL_TOKEN", raising=False)
    hop = HttpPlaneHop("http://hm.example")

    soak_captured: dict[str, object] = {}
    monkeypatch.setattr(httpx, "AsyncClient", _capturing_post_client(soak_captured))
    await hop.queue_lab_soak(TENANT, class_id="agx-large", catalog_id="hf-lab-soak")
    assert "X-Internal-Token" not in soak_captured["headers"]

    promote_captured: dict[str, object] = {}
    monkeypatch.setattr(httpx, "AsyncClient", _capturing_post_client(promote_captured))
    await hop.promote_catalog(
        TENANT,
        catalog_id="upload-route-check",
        class_id="agx-large",
        artifact_name="Promoted",
        file_hash="sha256:" + ("ab" * 32),
        admission_id=uuid.uuid4(),
    )
    assert "X-Internal-Token" not in promote_captured["headers"]
