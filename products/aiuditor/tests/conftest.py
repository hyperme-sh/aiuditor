from __future__ import annotations

import uuid

import pytest

from aiuditor_service.hypermesh import FakeHostLookup, FakePlaneHop
from aiuditor_service.provider import FakeProviderHop
from api.core import config as api_config
from api.infrastructure.security import keycloak_jwt as kc_jwt

FYBER_AGX_DEVICE_ID = uuid.UUID("a6400000-0640-4000-8000-000000000001")


@pytest.fixture(autouse=True)
def _clear_keycloak_validator_cache() -> None:
    kc_jwt._validator_singleton.cache_clear()
    yield
    kc_jwt._validator_singleton.cache_clear()


@pytest.fixture(autouse=True)
def _default_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIUDITOR_ROLE", "global")
    monkeypatch.delenv("AIUDITOR_PARENT_URL", raising=False)


@pytest.fixture(autouse=True)
def _exchange_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        api_config.settings,
        "api_key_exchange_jwt_secret",
        "test-api-key-exchange-secret-min-32-chars!!",
    )


@pytest.fixture
def event_sink() -> list:
    return []


@pytest.fixture
def host_lookup(tenant_a: uuid.UUID) -> FakeHostLookup:
    fake = FakeHostLookup()
    fake.enroll(FYBER_AGX_DEVICE_ID, tenant_id=tenant_a)
    return fake


@pytest.fixture
def plane_hop() -> FakePlaneHop:
    return FakePlaneHop()


@pytest.fixture
def provider_hop() -> FakeProviderHop:
    return FakeProviderHop()


@pytest.fixture
def app(event_sink, host_lookup, plane_hop, provider_hop, monkeypatch):
    monkeypatch.setattr(
        api_config.settings,
        "database_url",
        "postgresql+asyncpg://x:x@localhost:1/x",
    )
    monkeypatch.setattr(api_config.settings, "kafka_bootstrap_servers", None)
    monkeypatch.setattr(api_config.settings, "service_name", "aiuditor-service-test")

    from aiuditor_service.app import create_app

    return create_app(
        test_event_sink=event_sink,
        enable_db_middleware=False,
        host_lookup=host_lookup,
        plane_hop=plane_hop,
        provider_hop=provider_hop,
    )


@pytest.fixture
async def http_client(app):
    import httpx
    from asgi_lifespan import LifespanManager

    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.fixture
def tenant_a() -> uuid.UUID:
    return uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


@pytest.fixture
def tenant_b() -> uuid.UUID:
    return uuid.UUID("11111111-2222-3333-4444-555555555555")


DEFAULT_SUB = "11111111-1111-1111-1111-111111111111"


def tenant_session_headers(
    tenant_id: uuid.UUID,
    *,
    sub: str = DEFAULT_SUB,
) -> dict[str, str]:
    from api.infrastructure.security.tenant_session_jwt import mint_tenant_session_jwt

    token = mint_tenant_session_jwt(
        claims={
            "sub": sub,
            "email": "operator@example.com",
            "realm_access": {"roles": ["ir"]},
            "groups": ["ir"],
            "scope": "openid",
        },
        tenant_id=tenant_id,
        extra_roles=["ir"],
    )
    return {
        "X-Tenant-ID": str(tenant_id),
        "Authorization": f"Bearer {token}",
    }


@pytest.fixture
def tenant_headers(tenant_a: uuid.UUID) -> dict[str, str]:
    return tenant_session_headers(tenant_a)
