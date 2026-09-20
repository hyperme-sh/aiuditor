from __future__ import annotations

import uuid

import httpx
import pytest

from aiuditor_service.hypermesh import (
    HOST_LOOKUP_FAILED,
    HOST_LOOKUP_REQUIRED,
    ClosedHostLookup,
    HttpHostLookup,
    HostLookupUnavailable,
    resolve_host_lookup,
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
