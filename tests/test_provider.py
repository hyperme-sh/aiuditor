from __future__ import annotations

import httpx
import pytest

from aiuditor_service.provider import (
    PROVIDER_ENDPOINT_INVALID,
    PROVIDER_HOP_FAILED,
    FakeProviderHop,
    HttpProviderHop,
    ProviderUnavailable,
    _completions_url,
    resolve_provider_hop,
)


def test_completions_url_appends_and_rejects_http():
    assert (
        _completions_url("https://llm.example/v1")
        == "https://llm.example/v1/chat/completions"
    )
    assert (
        _completions_url("https://llm.example/v1/chat/completions")
        == "https://llm.example/v1/chat/completions"
    )
    with pytest.raises(ProviderUnavailable) as exc:
        _completions_url("http://llm.example/v1")
    assert str(exc.value) == PROVIDER_ENDPOINT_INVALID


def test_resolve_provider_defaults_to_http():
    assert isinstance(resolve_provider_hop(), HttpProviderHop)


@pytest.mark.asyncio
async def test_fake_provider_can_fail():
    hop = FakeProviderHop(error=PROVIDER_HOP_FAILED)
    with pytest.raises(ProviderUnavailable):
        await hop.complete(
            "https://llm.example/v1",
            messages=[],
            model=None,
            authorization=None,
        )


@pytest.mark.asyncio
async def test_http_provider_posts_without_operator_jwt(monkeypatch):
    captured: dict[str, object] = {}

    class _Resp:
        status_code = 200

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    status = await HttpProviderHop().complete(
        "https://llm.example/v1",
        messages=[{"role": "user", "content": "secret"}],
        model="test",
        authorization="Bearer one-shot",
    )
    assert status == 200
    assert captured["url"] == "https://llm.example/v1/chat/completions"
    assert captured["headers"] == {"Authorization": "Bearer one-shot"}
    assert captured["json"]["model"] == "test"


@pytest.mark.asyncio
async def test_http_provider_network_fail_closes(monkeypatch):
    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json=None, headers=None):
            raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    with pytest.raises(ProviderUnavailable) as exc:
        await HttpProviderHop().complete(
            "https://llm.example/v1",
            messages=[],
            model=None,
            authorization=None,
        )
    assert str(exc.value) == PROVIDER_HOP_FAILED
