"""AIuditor-owned HTTPS hop to a provider connection.

credential_ref is a hash. This hop never reconstructs a stored secret.
Optional X-Provider-Authorization is forwarded once and not persisted.
Prompt bodies are not logged or evented.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit

import httpx

PROVIDER_HOP_FAILED = "provider hop failed"
PROVIDER_ENDPOINT_INVALID = "provider endpoint must be an HTTPS URL on port 443 without credentials, query or fragment"
PROVIDER_DESTINATION_FORBIDDEN = "provider destination is not permitted"


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
    # Reject parser normalization and authority/path ambiguities, including empty
    # query/fragment delimiters. Tenant input cannot override the fixed suffix.
    if any(ord(c) <= 32 or ord(c) >= 127 or c in "\\%" for c in endpoint):
        raise ProviderUnavailable(PROVIDER_ENDPOINT_INVALID)
    try:
        parsed = urlsplit(endpoint)
        host = parsed.hostname
        if (parsed.scheme != "https" or not host or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None, 443)
                or "?" in endpoint or "#" in endpoint):
            raise ValueError("invalid endpoint")
        # Literal IPs are validated below just like DNS answers. DNS names use
        # exact ASCII labels; no wildcard, escaped host, zone ID or trailing dot.
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if len(host) > 253 or any(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
                for label in host.split(".")
            ):
                raise ValueError("invalid hostname")
        if "%" in host:
            raise ValueError("zone IDs are not supported")
    except ValueError as exc:
        raise ProviderUnavailable(PROVIDER_ENDPOINT_INVALID) from exc
    text = endpoint.rstrip("/")
    return text if text.endswith("/chat/completions") else f"{text}/chat/completions"


async def _resolve_public_address(host: str, timeout_s: float) -> str:
    try:
        answers = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM),
            timeout=timeout_s,
        )
        addresses = [ipaddress.ip_address(answer[4][0]) for answer in answers]
        if not addresses or any(
            not ip.is_global or ip.is_reserved or ip.is_multicast or ip.is_unspecified
            or getattr(ip, "sixtofour", None) is not None
            or getattr(ip, "teredo", None) is not None
            or getattr(ip, "ipv4_mapped", None) is not None or "%" in str(ip)
            for ip in addresses
        ):
            raise ValueError("non-public DNS answer")
    except (OSError, ValueError, TimeoutError) as exc:
        raise ProviderUnavailable(PROVIDER_DESTINATION_FORBIDDEN) from exc
    return str(addresses[0])


class HttpProviderHop:
    def __init__(self, *, timeout_s: float = 15.0) -> None:
        self._timeout = timeout_s
        self._allowed_hosts = frozenset(
            host.strip().lower()
            for host in os.environ.get("AIUDITOR_PROVIDER_ALLOWED_HOSTS", "").split(",")
            if host.strip()
        )

    async def complete(
        self,
        endpoint: str,
        *,
        messages: list[dict[str, object]],
        model: str | None,
        authorization: str | None,
    ) -> int:
        url = _completions_url(endpoint)
        hostname = urlsplit(url).hostname
        if hostname not in self._allowed_hosts:
            raise ProviderUnavailable(PROVIDER_DESTINATION_FORBIDDEN)
        address = await _resolve_public_address(hostname, self._timeout)
        # The socket sees only a validated numeric IP, so a second DNS answer
        # cannot rebind the connection. TLS still verifies the original host.
        pinned_url = httpx.URL(url).copy_with(host=address)
        headers: dict[str, str] = {"Host": httpx.URL(url).netloc.decode("ascii")}
        if authorization:
            headers["Authorization"] = authorization
        payload: dict[str, object] = {"messages": messages}
        if model:
            payload["model"] = model
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, trust_env=False, follow_redirects=False, verify=True,
            ) as client:
                response = await client.post(
                    pinned_url, json=payload, headers=headers,
                    extensions={"sni_hostname": hostname},
                )
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
