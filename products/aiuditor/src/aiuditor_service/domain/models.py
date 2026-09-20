"""Connection registry. Kind host|provider, purpose lease|auditor.

Classes, devices, and host certs stay on Hypermesh. This row is an assignment
reference: a Hypermesh device_id or a provider HTTPS endpoint. Credential is
write-only; only the hash is stored.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Self
from urllib.parse import urlparse

from pydantic import BaseModel, Field, model_validator


class ConnectionKind(StrEnum):
    HOST = "host"
    PROVIDER = "provider"


class ConnectionPurpose(StrEnum):
    LEASE = "lease"
    AUDITOR = "auditor"


class ConnectionStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    DISABLED = "disabled"


def _https_endpoint(raw: str) -> str:
    text = raw.strip()
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("endpoint must be an https URL with no userinfo")
    return text


class ConnectionCreate(BaseModel):
    """POST body. credential is write-only and never persisted or returned."""

    kind: ConnectionKind
    purpose: ConnectionPurpose
    device_id: uuid.UUID | None = None
    endpoint: str | None = Field(default=None, max_length=2048)
    credential: str | None = Field(default=None, min_length=1, max_length=4096)

    @model_validator(mode="after")
    def _kind_fields(self) -> Self:
        if self.kind is ConnectionKind.HOST:
            if self.device_id is None:
                raise ValueError("host connections require device_id")
            if self.endpoint:
                raise ValueError("host connections must not set endpoint")
            return self
        if self.device_id is not None:
            raise ValueError("provider connections must not set device_id")
        if not self.endpoint:
            raise ValueError("provider connections require endpoint")
        self.endpoint = _https_endpoint(self.endpoint)
        return self


class Connection(BaseModel):
    """Tenant-scoped connection row. credential_ref is a hash, never the secret."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    kind: ConnectionKind
    purpose: ConnectionPurpose
    device_id: uuid.UUID | None = None
    endpoint: str | None = None
    credential_ref: str | None = None
    status: ConnectionStatus
    created_at: datetime

    def to_event_payload(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "kind": self.kind.value,
            "purpose": self.purpose.value,
            "device_id": str(self.device_id) if self.device_id else None,
            "endpoint": self.endpoint,
            "credential_ref": self.credential_ref,
            "status": self.status.value,
        }
