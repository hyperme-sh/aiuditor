"""AIuditor-owned HTTPS hop to a provider connection.

credential_ref is a hash. This hop never reconstructs a stored secret.
Optional X-Provider-Authorization is forwarded once and not persisted.
Prompt bodies are not logged or evented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlparse

import httpx

PROVIDER_HOP_FAILED = "provider hop failed"
PROVIDER_ENDPOINT_INVALID = "provider endpoint is not https"


class ProviderUnavailable(Exception):
    """Provider HTTPS hop failed or is not configured."""


class ProviderHop(Protocol):
    async def complete(
        self,
        endpoint: str,
        *,
        messages: list[dict[str, object]],
        model: str | None,
        authorization: str | None,
    ) -> int: ...


def _completions_url(endpoint: str) -> str:
    text = endpoint.rstrip("/")
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ProviderUnavailable(PROVIDER_ENDPOINT_INVALID)
    if text.endswith("/chat/completions"):
        return text
    return f"{text}/chat/completions"


class HttpProviderHop:
    def __init__(self, *, timeout_s: float = 15.0) -> None:
        self._timeout = timeout_s

    async def complete(
        self,
        endpoint: str,
        *,
        messages: list[dict[str, object]],
        model: str | None,
        authorization: str | None,
    ) -> int:
        url = _completions_url(endpoint)
        headers: dict[str, str] = {}
        if authorization:
            headers["Authorization"] = authorization
        payload: dict[str, object] = {"messages": messages}
        if model:
            payload["model"] = model
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(PROVIDER_HOP_FAILED) from exc
        return response.status_code


@dataclass
class FakeProviderHop:
    status_code: int = 200
    calls: list[dict[str, object]] = field(default_factory=list)
    error: str | None = None

    async def complete(
        self,
        endpoint: str,
        *,
        messages: list[dict[str, object]],
        model: str | None,
        authorization: str | None,
    ) -> int:
        _completions_url(endpoint)
        self.calls.append(
            {
                "endpoint": endpoint,
                "message_count": len(messages),
                "model": model,
                "authorization": authorization,
            }
        )
        if self.error:
            raise ProviderUnavailable(self.error)
        return self.status_code


def resolve_provider_hop(hop: ProviderHop | None = None) -> ProviderHop:
    if hop is not None:
        return hop
    return HttpProviderHop()
