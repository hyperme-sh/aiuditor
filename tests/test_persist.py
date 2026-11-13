from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from aiuditor_service.models import AiuditorContract, AiuditorPolicy

from aiuditor_service.domain.models import (
    ApprovalPolicy,
    CatalogService,
    Connection,
    ConnectionKind,
    ConnectionPurpose,
    ConnectionStatus,
    Contract,
    ContractParty,
    ContractStatus,
    PolicyDisposition,
    ServiceBackend,
)
from aiuditor_service.persist import (
    PERSIST_UNAVAILABLE,
    MemoryPersistSession,
    PersistUnavailable,
    SqlPersistSession,
    TABLES,
    dump_for_persist,
    resolve_persist,
    sql_row,
)
from aiuditor_service.store import ConnectionStore, PolicyStore, new_stores


def _connection(tenant: uuid.UUID) -> Connection:
    return Connection(
        id=uuid.uuid4(),
        tenant_id=tenant,
        kind=ConnectionKind.HOST,
        purpose=ConnectionPurpose.LEASE,
        device_id=uuid.uuid4(),
        status=ConnectionStatus.READY,
        created_at=datetime.now(UTC),
    )


def _contract(tenant: uuid.UUID) -> Contract:
    return Contract(
        id=uuid.uuid4(),
        tenant_id=tenant,
        parties=[
            ContractParty(role="renter", subject="renter@example.com"),
            ContractParty(role="host", subject="agx-host"),
        ],
        meters=["wall_time"],
        rail="stripe",
        until=datetime.now(UTC) + timedelta(hours=1),
        status=ContractStatus.SIGNED,
        signed_by="checkout",
        created_at=datetime.now(UTC),
        signed_at=datetime.now(UTC),
    )


def _policy(tenant: uuid.UUID) -> ApprovalPolicy:
    return ApprovalPolicy(
        id=uuid.uuid4(),
        tenant_id=tenant,
        purpose="renter",
        prompt_class=None,
        connection_id=None,
        disposition=PolicyDisposition.HOLD,
        created_at=datetime.now(UTC),
    )


def test_tables_are_grant_store_not_siblings():
    assert "contracts" in TABLES
    assert "catalog" in TABLES
    assert not any(name in TABLES for name in ("turn", "oracles", "payments"))


def test_sql_row_drops_prompt_ticket_and_credential():
    tenant = uuid.uuid4()
    row = sql_row(
        "contracts",
        {
            **dump_for_persist(_contract(tenant)),
            "prompt": "do not store this",
            "ticket": "hm1.secret",
            "credential": "sk-live",
            "messages": [{"role": "user", "content": "nope"}],
        },
    )
    assert "prompt" not in row
    assert "ticket" not in row
    assert "credential" not in row
    assert "messages" not in row
    assert row["rail"] == "stripe"
    assert row["parties"][0]["role"] == "renter"


def test_memory_persist_survives_new_store_session():
    tenant = uuid.uuid4()
    persist = MemoryPersistSession()
    first = new_stores(persist)
    row = _connection(tenant)
    first.connection_store.put(row)
    first.catalog_store.put(
        CatalogService(
            id=uuid.uuid4(),
            tenant_id=tenant,
            title="AGX lease",
            backend=ServiceBackend.HYPERMESH_CLASS,
            class_id="agx-large",
            meters=["wall_time"],
            created_at=datetime.now(UTC),
        )
    )

    second = new_stores(persist)
    restored = second.connection_store.get(tenant, row.id)
    assert restored is not None
    assert restored.device_id == row.device_id
    assert restored.kind is ConnectionKind.HOST
    assert second.catalog_store.list_for(tenant)[0].title == "AGX lease"
    assert "credential" not in persist.snapshot()["connections"][str(tenant)][str(row.id)]


def test_policy_delete_is_write_through():
    tenant = uuid.uuid4()
    persist = MemoryPersistSession()
    store = PolicyStore(persist=persist)
    row = _policy(tenant)
    store.put(row)
    assert store.delete(tenant, row.id) == row
    assert new_stores(persist).policy_store.get(tenant, row.id) is None


def test_resolve_persist_stays_memory_only_when_sql_is_disabled():
    assert isinstance(resolve_persist(False, "postgresql://db/controlplane"), MemoryPersistSession)
    assert isinstance(
        resolve_persist(False, "postgresql+asyncpg://x:x@localhost:1/x"),
        MemoryPersistSession,
    )


def test_resolve_persist_fail_closes_on_dummy_or_missing_url():
    with pytest.raises(PersistUnavailable, match=PERSIST_UNAVAILABLE):
        resolve_persist(True, "postgresql+asyncpg://x:x@localhost:1/x")
    with pytest.raises(PersistUnavailable, match=PERSIST_UNAVAILABLE):
        resolve_persist(True, "")


class _FakeResult:
    def scalars(self):
        return self

    def all(self):
        return []


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def execute(self, stmt, params=None):
        self.calls.append(("execute", str(stmt), params))
        return _FakeResult()

    def merge(self, obj):
        self.calls.append(
            (
                "merge",
                type(obj).__name__,
                {column.key: getattr(obj, column.key) for column in obj.__table__.columns},
            )
        )

    def get(self, model, pk):
        self.calls.append(("get", model.__name__, pk))
        return None

    def delete(self, obj):
        self.calls.append(("delete", type(obj).__name__))

    def begin(self):
        from contextlib import nullcontext
        return nullcontext()

    def commit(self):
        self.calls.append(("commit",))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_sql_persist_sets_rls_guc_and_merges_contract():
    tenant = uuid.uuid4()
    session = _FakeSession()
    persist = SqlPersistSession(lambda: session)
    row = _contract(tenant)
    persist.upsert("contracts", tenant, row.id, dump_for_persist(row))
    assert any(
        call[0] == "execute" and call[2] == {"tid": str(tenant)} for call in session.calls
    )
    merged = [call for call in session.calls if call[0] == "merge"]
    assert merged[0][1] == AiuditorContract.__name__
    assert merged[0][2]["tenant_id"] == tenant
    assert merged[0][2]["rail"] == "stripe"
    assert "prompt" not in merged[0][2]
    assert ("commit",) in session.calls


def test_sql_persist_delete_sets_rls_guc():
    tenant = uuid.uuid4()
    policy_id = uuid.uuid4()
    session = _FakeSession()
    persist = SqlPersistSession(lambda: session)
    persist.delete("policies", tenant, policy_id)
    assert any(
        call[0] == "execute" and call[2] == {"tid": str(tenant)} for call in session.calls
    )
    assert ("get", AiuditorPolicy.__name__, policy_id) in session.calls


def test_sql_persist_load_snapshot_and_hydrate_are_empty_without_rows():
    persist = SqlPersistSession(lambda: _FakeSession())
    tenant = uuid.uuid4()
    assert persist.load_rows("contracts", tenant) == {}
    assert persist.snapshot()["contracts"] == {}
    bundle = new_stores()
    persist.hydrate(bundle)
    assert bundle.contract_store.list_for(tenant) == []


def test_resolve_persist_sql_init_failure_fail_closes(monkeypatch):
    def _boom(_cls, _url):
        raise RuntimeError("no db")

    monkeypatch.setattr(SqlPersistSession, "from_url", classmethod(_boom))
    with pytest.raises(PersistUnavailable, match=PERSIST_UNAVAILABLE):
        resolve_persist(True, "postgresql://db/controlplane")


def test_connection_store_reloads_from_shared_persist():
    tenant = uuid.uuid4()
    persist = MemoryPersistSession()
    writer = ConnectionStore(persist=persist)
    row = _connection(tenant)
    writer.put(row)
    reader = ConnectionStore(persist=persist)
    assert reader.get(tenant, row.id) is not None
    assert reader.get(tenant, row.id).status is ConnectionStatus.READY


@pytest.mark.asyncio
async def test_http_put_is_503_when_persist_is_unavailable(
    event_sink, host_lookup, plane_hop, provider_hop, tenant_headers
):
    import httpx
    from asgi_lifespan import LifespanManager

    from aiuditor_service.app import create_app

    class _Down:
        def candidate_scope(self):
            raise PersistUnavailable(PERSIST_UNAVAILABLE)

        def hydrate(self, stores) -> None:
            return None

        def upsert(self, table, tenant_id, key, payload) -> None:
            raise PersistUnavailable(PERSIST_UNAVAILABLE)

        def delete(self, table, tenant_id, key) -> None:
            raise PersistUnavailable(PERSIST_UNAVAILABLE)

        def snapshot(self):
            return {}

        def load_rows(self, table, tenant_id):
            return {}

    app = create_app(
        test_event_sink=event_sink,
        enable_db_middleware=False,
        host_lookup=host_lookup,
        plane_hop=plane_hop,
        provider_hop=provider_hop,
        persist_session=_Down(),
    )
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/v1/aiuditor/connections",
                headers=tenant_headers,
                json={
                    "kind": "host",
                    "purpose": "auditor",
                    "device_id": "a6400000-0640-4000-8000-000000000001",
                },
            )
    assert created.status_code == 503, created.text
    assert created.json()["error"] == PERSIST_UNAVAILABLE


@pytest.mark.asyncio
async def test_lifespan_fail_closes_without_database(
    event_sink, host_lookup, plane_hop, provider_hop, monkeypatch
):
    from asgi_lifespan import LifespanManager
    from api.core import config as api_config

    from aiuditor_service.app import create_app

    monkeypatch.setattr(
        api_config.settings,
        "database_url",
        "postgresql+asyncpg://x:x@localhost:1/x",
    )
    app = create_app(
        test_event_sink=event_sink,
        enable_db_middleware=True,
        host_lookup=host_lookup,
        plane_hop=plane_hop,
        provider_hop=provider_hop,
    )
    with pytest.raises(PersistUnavailable, match=PERSIST_UNAVAILABLE):
        async with LifespanManager(app):
            pass


@pytest.mark.asyncio
async def test_http_put_survives_on_injected_persist(
    event_sink, host_lookup, plane_hop, provider_hop, tenant_headers, tenant_a
):
    import httpx
    from asgi_lifespan import LifespanManager

    from aiuditor_service.app import create_app

    persist = MemoryPersistSession()
    app = create_app(
        test_event_sink=event_sink,
        enable_db_middleware=False,
        host_lookup=host_lookup,
        plane_hop=plane_hop,
        provider_hop=provider_hop,
        persist_session=persist,
    )
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/v1/aiuditor/connections",
                headers=tenant_headers,
                json={
                    "kind": "host",
                    "purpose": "auditor",
                    "device_id": "a6400000-0640-4000-8000-000000000001",
                },
            )
            assert created.status_code == 201, created.text
            connection_id = created.json()["id"]

    restored = new_stores(persist).connection_store.get(tenant_a, uuid.UUID(connection_id))
    assert restored is not None
    assert restored.purpose is ConnectionPurpose.AUDITOR
    assert "credential" not in persist.snapshot()["connections"][str(tenant_a)][connection_id]


@pytest.mark.parametrize("scoped,failure_stage", [
    (False, "connect"), (False, "tenant"), (False, "select"), (False, "fetch"),
    (True, "tenant"), (True, "select"), (True, "fetch"),
])
def test_sql_read_failures_are_unavailable(failure_stage, scoped, monkeypatch):
    from aiuditor_service import persist as module

    class FailingSession(_FakeSession):
        def execute(self, stmt, params=None):
            is_tenant = params is not None and "tid" in params
            if failure_stage == ("tenant" if is_tenant else "select"):
                raise PermissionError("private database diagnostic")
            if failure_stage == "fetch" and not is_tenant:
                class FailedResult(_FakeResult):
                    def all(self):
                        raise TimeoutError("private database diagnostic")
                return FailedResult()
            return super().execute(stmt, params)

    session = FailingSession()
    def factory():
        if failure_stage == "connect":
            raise ConnectionError("private database diagnostic")
        return session

    if scoped:
        monkeypatch.setattr(module, "_SCOPE", type(module._SCOPE)("test_scope", default=session))
    with pytest.raises(PersistUnavailable, match=PERSIST_UNAVAILABLE) as error:
        SqlPersistSession(factory).load_rows("contracts", uuid.uuid4())
    assert "private database diagnostic" not in str(error.value)
    assert error.value.__cause__ is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [PermissionError, TimeoutError])
async def test_failed_sql_reads_return_503_preserve_cache_then_reconcile_empty(
    app, http_client, tenant_headers, tenant_a, failure,
):
    row = _policy(tenant_a)
    store = app.state.policy_store
    store.put(row)
    bucket = store.policies[tenant_a]

    class RecoverableSession(_FakeSession):
        available = False
        def execute(self, stmt, params=None):
            if params is None and not self.available:
                raise failure("private database diagnostic")
            return super().execute(stmt, params)

    session = RecoverableSession()
    store.persist = SqlPersistSession(lambda: session)
    for method, path in (
        ("GET", "/api/v1/aiuditor/policies"),
        ("DELETE", f"/api/v1/aiuditor/policies/{row.id}"),
    ):
        response = await http_client.request(method, path, headers=tenant_headers)
        assert response.status_code == 503, response.text
        assert response.json() == {"error": PERSIST_UNAVAILABLE}
        assert bucket == {row.id: row}
    session.available = True
    response = await http_client.get("/api/v1/aiuditor/policies", headers=tenant_headers)
    assert response.status_code == 200
    assert response.json() == []
    assert bucket == {}
